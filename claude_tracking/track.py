"""Claude Code session tracking hook.

Reads hook event JSON from stdin and records session state to SQLite.
Called via: claude-track hook
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

DB_PATH = os.path.expanduser("~/.claude/tracking.db")


def init_db(db):
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=3000")
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
            model TEXT
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


def get_tmux_info():
    pane = os.environ.get("TMUX_PANE", "")
    window = ""
    session = ""
    if pane:
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "-t", pane, "#W"],
                capture_output=True, text=True, timeout=2,
            )
            window = result.stdout.strip()
            result = subprocess.run(
                ["tmux", "display-message", "-p", "-t", pane, "#S"],
                capture_output=True, text=True, timeout=2,
            )
            session = result.stdout.strip()
        except Exception:
            pass
    return pane, window, session


def extract_detail(event_name, tool_name, tool_input):
    if event_name == "UserPromptSubmit":
        return ""
    if tool_name == "Bash":
        return tool_input.get("command", "")[:120]
    if tool_name in ("Edit", "Write", "Read"):
        return tool_input.get("file_path", "")
    if tool_name == "Grep":
        return tool_input.get("pattern", "")
    if tool_name == "Glob":
        return tool_input.get("pattern", "")
    if tool_name == "Task":
        return tool_input.get("description", "")[:120]
    if tool_name == "WebSearch":
        return tool_input.get("query", "")[:120]
    if tool_name == "WebFetch":
        return tool_input.get("url", "")[:120]
    return ""


def derive_status(event_name):
    if event_name == "Stop":
        return "idle"
    if event_name == "SessionEnd":
        return "ended"
    if event_name == "PermissionRequest":
        return "waiting"
    return "active"


def track(data):
    now = datetime.now().isoformat()
    session_id = data.get("session_id", "unknown")
    event_name = data.get("hook_event_name", "unknown")
    cwd = data.get("cwd", "")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    model = data.get("model", "")

    pane, window, tmux_session = get_tmux_info()
    detail = extract_detail(event_name, tool_name, tool_input)
    status = derive_status(event_name)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    init_db(db)

    existing = db.execute(
        "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()

    if existing:
        updates = ["last_activity = ?", "last_event = ?", "status = ?"]
        params = [now, event_name, status]

        if pane:
            updates.append("tmux_pane = ?")
            params.append(pane)
        if window:
            updates.append("tmux_window = ?")
            params.append(window)
        if tmux_session:
            updates.append("tmux_session = ?")
            params.append(tmux_session)
        if model:
            updates.append("model = ?")
            params.append(model)
        if cwd:
            updates.append("project_dir = ?")
            params.append(cwd)

        if event_name == "UserPromptSubmit":
            prompt_text = data.get("prompt", "")[:200]
            updates.append("last_prompt = ?")
            params.append(prompt_text)
            updates.append("last_detail = ?")
            params.append(prompt_text[:120])
            updates.append("prompt_count = prompt_count + 1")
        elif tool_name:
            updates.append("last_tool = ?")
            params.append(tool_name)
            if detail:
                updates.append("last_detail = ?")
                params.append(detail)
            updates.append("tool_count = tool_count + 1")

        params.append(session_id)
        db.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?",
            params,
        )
    else:
        prompt_text = ""
        if event_name == "UserPromptSubmit":
            prompt_text = data.get("prompt", "")[:200]

        db.execute(
            """INSERT INTO sessions
               (session_id, project_dir, tmux_pane, tmux_window, tmux_session,
                status, started_at, last_activity, last_event, last_tool,
                last_detail, last_prompt, prompt_count, tool_count, model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, cwd, pane, window, tmux_session,
                status, now, now, event_name, tool_name,
                detail, prompt_text,
                1 if event_name == "UserPromptSubmit" else 0,
                1 if tool_name else 0,
                model,
            ),
        )

    db.execute(
        "INSERT INTO events (session_id, timestamp, event_type, tool_name, detail) VALUES (?, ?, ?, ?, ?)",
        (session_id, now, event_name, tool_name, detail),
    )

    db.commit()
    db.close()


def handle_hook():
    """Entry point for the hook command."""
    try:
        data = json.load(sys.stdin)
        track(data)
    except Exception as e:
        print(f"tracking error: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    handle_hook()
