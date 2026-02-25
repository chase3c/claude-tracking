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
            model TEXT,
            transcript_path TEXT,
            source TEXT DEFAULT 'host'
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
    # Migrate: add transcript_path if missing
    try:
        db.execute("ALTER TABLE sessions ADD COLUMN transcript_path TEXT")
        db.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migrate: add source if missing
    try:
        db.execute("ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT 'host'")
        db.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migrate: add pending_permissions counter
    try:
        db.execute("ALTER TABLE sessions ADD COLUMN pending_permissions INTEGER DEFAULT 0")
        db.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migrate: add is_priority flag
    try:
        db.execute("ALTER TABLE sessions ADD COLUMN is_priority INTEGER DEFAULT 0")
        db.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migrate: add custom session name
    try:
        db.execute("ALTER TABLE sessions ADD COLUMN name TEXT")
        db.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
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


def derive_status(event_name, pending_permissions=0, notification_type="",
                  current_status="active"):
    """Derive session status from event name and pending permission count.

    Permission-aware: a PostToolUse only clears 'waiting' if all pending
    permission requests have been resolved. Events that aren't meaningful
    status signals (SubagentStart/Stop, unknown Notifications) preserve
    the current status.
    """
    if event_name == "Stop":
        return "idle"
    if event_name == "Notification" and notification_type == "idle_prompt":
        return "idle"
    if event_name == "SessionEnd":
        return "ended"
    if event_name == "PermissionRequest":
        return "waiting"
    if event_name == "Notification" and notification_type == "permission_prompt":
        return "waiting"
    # For PostToolUse/PostToolUseFailure: stay 'waiting' if more are pending
    if event_name in ("PostToolUse", "PostToolUseFailure"):
        return "waiting" if pending_permissions > 0 else "active"
    # Active work signals
    if event_name in ("UserPromptSubmit", "PreToolUse"):
        return "active"
    # SubagentStart/Stop, other Notifications, etc. — don't change status
    return current_status


def track(data, source=None, tmux_pane_override=None):
    # Debug: dump raw hook payload
    try:
        with open("/tmp/hook-dump.jsonl", "a") as f:
            f.write(json.dumps(data) + "\n")
    except Exception:
        pass

    now = datetime.now().isoformat()
    session_id = data.get("session_id", "unknown")
    event_name = data.get("hook_event_name", "unknown")
    cwd = data.get("cwd", "")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    model = data.get("model", "")
    transcript_path = data.get("transcript_path", "")
    notification_type = data.get("notification_type", "")
    source = source or "host"

    if tmux_pane_override:
        pane = tmux_pane_override
        # Look up window/session for the overridden pane
        window = ""
        tmux_session = ""
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
            tmux_session = result.stdout.strip()
        except Exception:
            pass
    else:
        pane, window, tmux_session = get_tmux_info()
    detail = extract_detail(event_name, tool_name, tool_input)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    init_db(db)

    existing = db.execute(
        "SELECT session_id, pending_permissions, status FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()

    # Compute new pending_permissions count
    if existing:
        current_pending = existing[1] or 0
        current_status = existing[2] or "active"
    else:
        current_pending = 0
        current_status = "active"

    is_idle_notification = (event_name == "Notification" and notification_type == "idle_prompt")

    if event_name == "PermissionRequest":
        new_pending = current_pending + 1
    elif event_name in ("PostToolUse", "PostToolUseFailure"):
        new_pending = max(0, current_pending - 1)
    elif event_name in ("Stop", "UserPromptSubmit", "SessionEnd") or is_idle_notification:
        # Session moved on — all pending permissions are moot
        new_pending = 0
    else:
        new_pending = current_pending

    status = derive_status(event_name, pending_permissions=new_pending,
                           notification_type=notification_type,
                           current_status=current_status)

    if existing:
        updates = ["last_activity = ?", "last_event = ?", "status = ?",
                    "pending_permissions = ?"]
        params = [now, event_name, status, new_pending]

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
        if transcript_path:
            updates.append("transcript_path = ?")
            params.append(transcript_path)

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
        # New session: if it has a tmux pane, end any other sessions on that pane
        if pane:
            db.execute(
                """UPDATE sessions SET status = 'ended'
                   WHERE tmux_pane = ? AND session_id != ?
                   AND status NOT IN ('ended', 'dismissed')""",
                (pane, session_id),
            )

        prompt_text = ""
        if event_name == "UserPromptSubmit":
            prompt_text = data.get("prompt", "")[:200]

        db.execute(
            """INSERT INTO sessions
               (session_id, project_dir, tmux_pane, tmux_window, tmux_session,
                status, started_at, last_activity, last_event, last_tool,
                last_detail, last_prompt, prompt_count, tool_count, model,
                transcript_path, source, pending_permissions)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, cwd, pane, window, tmux_session,
                status, now, now, event_name, tool_name,
                detail, prompt_text,
                1 if event_name == "UserPromptSubmit" else 0,
                1 if tool_name else 0,
                model, transcript_path, source, new_pending,
            ),
        )

    db.execute(
        "INSERT INTO events (session_id, timestamp, event_type, tool_name, detail) VALUES (?, ?, ?, ?, ?)",
        (session_id, now, event_name, tool_name, detail),
    )

    db.commit()
    db.close()


def cleanup_stale_sessions():
    """Mark sessions ended if their tmux pane no longer exists in the expected session.

    Called once at TUI startup to clear out sessions left in active/waiting/idle
    after a reboot or crash.
    """
    if not os.path.exists(DB_PATH):
        return
    try:
        # Get all currently active (tmux_session, pane_id) pairs
        active_panes: set[tuple[str, str]] = set()
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{session_name} #{pane_id}"],
                capture_output=True, text=True, timeout=2,
            )
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) == 2:
                    active_panes.add((parts[0], parts[1]))
        except Exception:
            pass

        db = sqlite3.connect(DB_PATH)
        db.execute("PRAGMA busy_timeout=1000")
        rows = db.execute(
            "SELECT session_id, tmux_pane, tmux_session FROM sessions WHERE status IN ('active', 'waiting', 'idle')"
        ).fetchall()
        stale = []
        for session_id, pane, tmux_session in rows:
            if not pane or not tmux_session:
                stale.append(session_id)
                continue
            if (tmux_session, pane) not in active_panes:
                stale.append(session_id)
        if stale:
            db.executemany(
                "UPDATE sessions SET status = 'ended' WHERE session_id = ?",
                [(sid,) for sid in stale],
            )
            db.commit()
        db.close()
    except Exception:
        pass


def set_name(name: str) -> str:
    """Set a custom name for the session in the current tmux pane.

    Writes to tracking.db and syncs via `claude session rename`.
    Returns the session_id on success, raises RuntimeError on failure.
    """
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        raise RuntimeError("TMUX_PANE not set — are you in a tmux pane?")

    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA busy_timeout=1000")
    init_db(db)

    row = db.execute(
        "SELECT session_id FROM sessions WHERE tmux_pane = ? ORDER BY last_activity DESC LIMIT 1",
        (pane,),
    ).fetchone()

    if not row:
        db.close()
        raise RuntimeError(f"No tracked session found for pane {pane}")

    session_id = row[0]
    db.execute("UPDATE sessions SET name = ? WHERE session_id = ?", (name, session_id))
    db.commit()
    db.close()

    # Sync with Claude's own session rename
    try:
        subprocess.run(
            ["claude", "session", "rename", session_id, name],
            timeout=5,
        )
    except Exception:
        pass  # tracking DB update already succeeded; Claude rename is best-effort

    return session_id


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
