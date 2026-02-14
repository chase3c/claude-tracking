"""Local web server for the Claude Code session dashboard."""
import http.server
import json
import os
import sqlite3
import subprocess
import threading
import urllib.parse
from pathlib import Path

DB_PATH = os.path.expanduser("~/.claude/tracking.db")
DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=3000")
    return db


def parse_transcript_lines(raw_lines):
    """Parse transcript JSONL lines into chat messages."""
    messages = []
    tool_names = {}  # tool_use_id -> tool name
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg = entry.get("message", entry)
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue

        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            has_task_result = False
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            text_parts.append(text)
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        tool_id = block.get("id", "")
                        if tool_id:
                            tool_names[tool_id] = name
                        text_parts.append(f"[Tool: {name}]")
                    elif block.get("type") == "tool_result":
                        tool_id = block.get("tool_use_id", "")
                        tool_name = tool_names.get(tool_id, "")
                        if tool_name in ("Task", "ExitPlanMode"):
                            result_content = block.get("content", "")
                            if isinstance(result_content, str) and result_content.strip():
                                text_parts.append(result_content.strip())
                                has_task_result = True
                            elif isinstance(result_content, list):
                                for rb in result_content:
                                    if isinstance(rb, dict) and rb.get("type") == "text":
                                        t = rb.get("text", "").strip()
                                        if t:
                                            text_parts.append(t)
                                            has_task_result = True
                elif isinstance(block, str):
                    if block.strip():
                        text_parts.append(block.strip())
            content = "\n".join(text_parts)
            # Task results come in user messages but are really assistant output
            if has_task_result and role == "user":
                role = "assistant"
        elif isinstance(content, str):
            content = content.strip()

        if content:
            messages.append({"role": role, "content": content})

    return messages


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/dashboard":
            self.serve_file(DASHBOARD_PATH, "text/html")
        elif self.path == "/api/sessions":
            self.serve_sessions()
        elif self.path.startswith("/api/events/"):
            session_id = urllib.parse.unquote(self.path[len("/api/events/"):])
            self.serve_events(session_id)
        elif self.path.startswith("/api/transcript/"):
            session_id = urllib.parse.unquote(self.path[len("/api/transcript/"):])
            self.serve_transcript(session_id)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/jump/"):
            session_id = urllib.parse.unquote(self.path[len("/api/jump/"):])
            self.jump_to_session(session_id)
        elif self.path.startswith("/api/dismiss/"):
            session_id = urllib.parse.unquote(self.path[len("/api/dismiss/"):])
            self.dismiss_session(session_id)
        elif self.path.startswith("/api/send/"):
            session_id = urllib.parse.unquote(self.path[len("/api/send/"):])
            self.send_to_session(session_id)
        else:
            self.send_error(404)

    def serve_file(self, path, content_type):
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            with open(path, "rb") as f:
                self.wfile.write(f.read())
        except FileNotFoundError:
            self.send_error(404, f"File not found: {path}")

    def serve_sessions(self):
        try:
            db = get_db()
            rows = db.execute("""
                SELECT * FROM sessions
                WHERE status != 'dismissed'
                ORDER BY
                    CASE status
                        WHEN 'active' THEN 0
                        WHEN 'waiting' THEN 1
                        WHEN 'idle' THEN 2
                        WHEN 'ended' THEN 3
                    END,
                    last_activity DESC
            """).fetchall()
            db.close()
            self.send_json([dict(r) for r in rows])
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def serve_events(self, session_id):
        try:
            db = get_db()
            rows = db.execute("""
                SELECT * FROM events
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT 50
            """, (session_id,)).fetchall()
            db.close()
            self.send_json([dict(r) for r in rows])
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def jump_to_session(self, session_id):
        try:
            db = get_db()
            row = db.execute(
                "SELECT tmux_pane FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            db.close()

            if row and row["tmux_pane"]:
                pane = row["tmux_pane"]
                subprocess.run(["tmux", "select-window", "-t", pane], timeout=2)
                subprocess.run(["tmux", "select-pane", "-t", pane], timeout=2)
                self.send_json({"ok": True, "pane": pane})
            else:
                self.send_json({"ok": False, "error": "No tmux pane recorded"}, 404)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def serve_transcript(self, session_id):
        try:
            db = get_db()
            row = db.execute(
                "SELECT transcript_path, source FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            db.close()

            if not row or not row["transcript_path"]:
                self.send_json([], 200)
                return

            path = row["transcript_path"]
            source = row["source"] or "host"
            raw_lines = None

            if os.path.exists(path):
                with open(path) as f:
                    raw_lines = f.readlines()
            elif source.startswith("container:"):
                # Transcript is inside the container volume — read via docker
                container_id = source[len("container:"):]
                # Map host path back to container path
                container_path = path
                for bd in load_bridge_dirs():
                    if path.startswith(bd):
                        container_path = "/workspace" + path[len(bd):]
                        break
                # Also check unmapped paths (e.g. /home/node/.claude/...)
                try:
                    result = subprocess.run(
                        ["docker", "exec", container_id, "cat", container_path],
                        capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode == 0 and result.stdout:
                        raw_lines = result.stdout.splitlines(keepends=True)
                except Exception:
                    pass

            if not raw_lines:
                self.send_json([], 200)
                return

            messages = parse_transcript_lines(raw_lines)
            self.send_json(messages)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def send_to_session(self, session_id):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))
            message = body.get("message", "").strip()

            if not message:
                self.send_json({"ok": False, "error": "Empty message"}, 400)
                return

            db = get_db()
            row = db.execute(
                "SELECT tmux_pane FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            db.close()

            if not row or not row["tmux_pane"]:
                self.send_json({"ok": False, "error": "No tmux pane"}, 404)
                return

            pane = row["tmux_pane"]

            # Verify the pane still exists before sending
            check = subprocess.run(
                ["tmux", "display-message", "-p", "-t", pane, "#D"],
                capture_output=True, text=True, timeout=5,
            )
            if check.returncode != 0:
                self.send_json({"ok": False, "error": f"Pane {pane} no longer exists"}, 400)
                return

            # Send literal text first, then Enter as a separate key
            result = subprocess.run(
                ["tmux", "send-keys", "-t", pane, "-l", message],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                self.send_json({"ok": False, "error": f"tmux send-keys failed: {result.stderr.strip()}"}, 500)
                return

            result = subprocess.run(
                ["tmux", "send-keys", "-t", pane, "Enter"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                self.send_json({"ok": False, "error": f"tmux send-keys failed: {result.stderr.strip()}"}, 500)
                return

            self.send_json({"ok": True})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def dismiss_session(self, session_id):
        try:
            db = get_db()
            db.execute(
                "UPDATE sessions SET status = 'dismissed' WHERE session_id = ?",
                (session_id,),
            )
            db.commit()
            db.close()
            self.send_json({"ok": True})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


OFFSETS_PATH = os.path.expanduser("~/.claude/tracking-bridge-offsets.json")
BRIDGE_DIRS_PATH = os.path.expanduser("~/.claude/tracking-bridge-dirs.json")


def ensure_db():
    from .track import init_db
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    init_db(db)
    db.close()


def load_bridge_dirs():
    """Load the list of directories to scan for bridge files."""
    dirs = []
    if os.path.exists(BRIDGE_DIRS_PATH):
        try:
            with open(BRIDGE_DIRS_PATH) as f:
                dirs = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return dirs


def save_bridge_dirs(dirs):
    os.makedirs(os.path.dirname(BRIDGE_DIRS_PATH), exist_ok=True)
    with open(BRIDGE_DIRS_PATH, "w") as f:
        json.dump(dirs, f, indent=2)


def load_offsets():
    if os.path.exists(OFFSETS_PATH):
        try:
            with open(OFFSETS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_offsets(offsets):
    os.makedirs(os.path.dirname(OFFSETS_PATH), exist_ok=True)
    with open(OFFSETS_PATH, "w") as f:
        json.dump(offsets, f)


def bridge_watcher(stop_event):
    """Background thread that watches for container bridge event files."""
    from .track import track

    offsets = load_offsets()

    while not stop_event.is_set():
        try:
            bridge_dirs = load_bridge_dirs()
            for dir_path in bridge_dirs:
                bridge_file = os.path.join(
                    dir_path, ".claude-tracking-bridge", "events.jsonl"
                )
                if not os.path.exists(bridge_file):
                    continue

                file_size = os.path.getsize(bridge_file)
                offset = offsets.get(bridge_file, 0)

                if file_size <= offset:
                    continue

                with open(bridge_file) as f:
                    f.seek(offset)
                    new_data = f.read()
                    new_offset = f.tell()

                for line in new_data.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    data = event.get("data", {})
                    container = event.get("container", "unknown")
                    host_dir = event.get("host_dir", "")
                    host_tmux_pane = event.get("host_tmux_pane", "")
                    source = f"container:{container}"

                    # Map container /workspace paths to host paths
                    # Fall back to the bridge dir's parent if host_dir not set
                    map_root = host_dir or dir_path
                    cwd = data.get("cwd", "")
                    if cwd.startswith("/workspace"):
                        data["cwd"] = map_root + cwd[len("/workspace"):]
                    tp = data.get("transcript_path", "")
                    if tp.startswith("/workspace"):
                        data["transcript_path"] = (
                            map_root + tp[len("/workspace"):]
                        )

                    try:
                        track(
                            data, source=source,
                            tmux_pane_override=host_tmux_pane or None,
                        )
                    except Exception:
                        pass

                offsets[bridge_file] = new_offset
                save_offsets(offsets)

        except Exception:
            pass

        stop_event.wait(2)


def run_server(port=7860):
    ensure_db()

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=bridge_watcher, args=(stop_event,), daemon=True
    )
    watcher.start()

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"Dashboard: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        stop_event.set()
        server.shutdown()


if __name__ == "__main__":
    run_server()
