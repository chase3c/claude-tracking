"""Local web server for the Claude Code session dashboard."""
import http.server
import json
import os
import sqlite3
import subprocess
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


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/dashboard":
            self.serve_file(DASHBOARD_PATH, "text/html")
        elif self.path == "/api/sessions":
            self.serve_sessions()
        elif self.path.startswith("/api/events/"):
            session_id = urllib.parse.unquote(self.path[len("/api/events/"):])
            self.serve_events(session_id)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/jump/"):
            session_id = urllib.parse.unquote(self.path[len("/api/jump/"):])
            self.jump_to_session(session_id)
        elif self.path.startswith("/api/dismiss/"):
            session_id = urllib.parse.unquote(self.path[len("/api/dismiss/"):])
            self.dismiss_session(session_id)
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


def ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            project_dir TEXT,
            tmux_pane TEXT,
            tmux_window TEXT,
            tmux_session TEXT,
            status TEXT DEFAULT 'active',
            started_at TEXT,
            last_activity TEXT,
            last_event TEXT,
            last_tool TEXT,
            last_detail TEXT,
            last_prompt TEXT,
            prompt_count INTEGER DEFAULT 0,
            tool_count INTEGER DEFAULT 0,
            model TEXT,
            transcript_path TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp TEXT,
            event_type TEXT,
            tool_name TEXT,
            detail TEXT
        )
    """)
    db.commit()
    db.close()


def run_server(port=7860):
    ensure_db()
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"Dashboard: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    run_server()
