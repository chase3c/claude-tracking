"""Claude Code session tracker — Terminal UI.

Run in a tmux pane to monitor all your Claude Code sessions.
"""
import json
import os
import sqlite3
import subprocess
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Static

DB_PATH = os.path.expanduser("~/.claude/tracking.db")
REFRESH_SECONDS = 3

STATUS_DOTS = {
    "active": "[green]\u25cf[/]",
    "idle": "[yellow]\u25cf[/]",
    "waiting": "[#db6d28]\u25cf[/]",
    "ended": "[dim]\u25cf[/]",
}

STATUS_LABELS = {
    "active": "[green]Active[/]",
    "idle": "[yellow]Idle[/]",
    "waiting": "[#db6d28]Waiting[/]",
    "ended": "[dim]Ended[/]",
}


def time_ago(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        delta = datetime.now() - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ""


def short_project(path):
    if not path:
        return ""
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]
    parts = path.split("/")
    if len(parts) > 3:
        return "/".join(parts[-2:])
    return path


def format_activity(last_tool, last_detail, last_event):
    if last_event == "Stop":
        return "[dim]Waiting for input[/]"
    if last_event == "PermissionRequest":
        return "[#db6d28]Needs permission[/]"
    if last_event == "UserPromptSubmit":
        if last_detail:
            text = last_detail[:60] + ("\u2026" if len(last_detail) > 60 else "")
            return f'[dim]Prompt:[/] "{text}"'
        return "[dim]User prompted[/]"
    if not last_tool:
        return ""

    label = {
        "Edit": "Editing",
        "Write": "Writing",
        "Read": "Reading",
        "Bash": "Running",
        "Grep": "Searching",
        "Glob": "Finding",
        "Task": "Task",
        "WebSearch": "Searching web",
        "WebFetch": "Fetching",
    }.get(last_tool, last_tool)

    if not last_detail:
        return label

    detail = last_detail
    if "/" in detail and last_tool in ("Edit", "Write", "Read"):
        detail = detail.split("/")[-1]
    if len(detail) > 50:
        detail = detail[:50] + "\u2026"

    if last_tool == "Bash":
        return f"{label} [cyan]`{detail}`[/]"
    return f"{label} [cyan]{detail}[/]"


def fetch_sessions():
    if not os.path.exists(DB_PATH):
        return []
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=1000")
        rows = db.execute("""
            SELECT * FROM sessions
            WHERE status NOT IN ('dismissed', 'ended')
            ORDER BY
                CASE status
                    WHEN 'active' THEN 0
                    WHEN 'waiting' THEN 1
                    WHEN 'idle' THEN 2
                END,
                last_activity DESC
        """).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def fetch_events(session_id):
    if not os.path.exists(DB_PATH):
        return []
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=1000")
        rows = db.execute("""
            SELECT * FROM events
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 20
        """, (session_id,)).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def read_transcript(path, max_messages=3):
    """Read recent assistant text output from a transcript JSONL file."""
    if not path or not os.path.exists(path):
        return []
    messages = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Transcript entries wrap the message in an outer object
                msg = entry.get("message", entry)
                role = msg.get("role", "")
                if role != "assistant":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                text_parts.append(text)
                    content = "\n".join(text_parts)
                if content and content.strip():
                    messages.append(content.strip())
    except Exception:
        return []
    return messages[-max_messages:]


class DetailPanel(Static):
    pass


class SessionTracker(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #sessions-table {
        height: 1fr;
    }
    #detail {
        height: auto;
        max-height: 45%;
        border-top: tall $accent;
        padding: 1 2;
        background: $panel;
    }
    #detail.hidden {
        display: none;
    }
    #status-bar {
        height: 1;
        padding: 0 2;
        background: $boost;
        color: $text-muted;
    }
    """

    TITLE = "Claude Sessions"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "jump", "Jump to pane"),
        Binding("space", "toggle_detail", "Details"),
        Binding("d", "dismiss", "Dismiss"),
        Binding("a", "show_all", "Show ended"),
        Binding("r", "force_refresh", "Refresh"),
    ]

    show_ended = reactive(False)
    selected_session_id = reactive("")

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="sessions-table")
        yield DetailPanel(id="detail", classes="hidden")
        yield Static(id="status-bar")
        yield Footer()

    def on_mount(self):
        table = self.query_one("#sessions-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("", "Project", "Activity", "P", "T", "Last Active")
        self.refresh_data()
        self.set_interval(REFRESH_SECONDS, self.refresh_data)

    def refresh_data(self):
        table = self.query_one("#sessions-table", DataTable)
        sessions = fetch_sessions()

        counts = {}
        for s in sessions:
            counts[s["status"]] = counts.get(s["status"], 0) + 1

        summary_parts = []
        for status in ("active", "waiting", "idle", "ended"):
            if counts.get(status, 0) > 0:
                summary_parts.append(f"{counts[status]} {status}")
        summary = " \u00b7 ".join(summary_parts) if summary_parts else "No sessions"

        self.query_one("#status-bar", Static).update(
            f" {summary}  \u00b7  Refreshing every {REFRESH_SECONDS}s"
        )

        try:
            cursor_row = table.cursor_row
        except Exception:
            cursor_row = 0

        table.clear()
        self._session_ids = []

        for s in sessions:
            status = s.get("status", "unknown")
            dot = STATUS_DOTS.get(status, "?")
            project = short_project(s.get("project_dir", ""))
            activity = format_activity(
                s.get("last_tool", ""),
                s.get("last_detail", ""),
                s.get("last_event", ""),
            )
            prompts = str(s.get("prompt_count", 0))
            tools = str(s.get("tool_count", 0))
            last = time_ago(s.get("last_activity", ""))

            table.add_row(dot, project, activity, prompts, tools, last)
            self._session_ids.append(s["session_id"])

        if self._session_ids and cursor_row < len(self._session_ids):
            table.move_cursor(row=cursor_row)

        detail = self.query_one("#detail", DetailPanel)
        if "hidden" not in detail.classes and self.selected_session_id:
            self._update_detail(self.selected_session_id)

    def on_data_table_row_highlighted(self, event):
        if hasattr(self, "_session_ids") and event.cursor_row < len(self._session_ids):
            self.selected_session_id = self._session_ids[event.cursor_row]
            detail = self.query_one("#detail", DetailPanel)
            if "hidden" not in detail.classes:
                self._update_detail(self.selected_session_id)

    def _get_selected_session_id(self):
        table = self.query_one("#sessions-table", DataTable)
        if not hasattr(self, "_session_ids") or not self._session_ids:
            return None
        try:
            row = table.cursor_row
            if row < len(self._session_ids):
                return self._session_ids[row]
        except Exception:
            pass
        return None

    def _update_detail(self, session_id):
        detail = self.query_one("#detail", DetailPanel)
        events = fetch_events(session_id)

        sessions = fetch_sessions()
        session = next((s for s in sessions if s["session_id"] == session_id), None)
        if not session:
            detail.update("[dim]Session not found[/]")
            return

        lines = []
        sid_short = session_id[:12]
        status_label = STATUS_LABELS.get(session.get("status", ""), session.get("status", ""))
        lines.append(f"[bold]{short_project(session.get('project_dir', ''))}[/]  {status_label}  [dim]{sid_short}[/]")

        if session.get("model"):
            lines.append(f"[dim]Model:[/] {session['model']}")
        if session.get("tmux_window"):
            pane = session.get("tmux_pane", "?")
            lines.append(f"[dim]Tmux:[/] {session['tmux_window']} ({pane})")
        if session.get("last_prompt"):
            prompt = session["last_prompt"][:100]
            lines.append(f'[dim]Task:[/] "{prompt}"')
        lines.append("")

        # Show recent Claude output from transcript
        transcript = session.get("transcript_path", "")
        recent_output = read_transcript(transcript, max_messages=5)
        if recent_output:
            lines.append("[bold]Recent output[/]")
            for msg in recent_output:
                preview = msg.replace("\n", " ")
                if len(preview) > 300:
                    preview = preview[:300] + "\u2026"
                lines.append(f"  [white]{preview}[/]")
                lines.append("")

        detail.update("\n".join(lines))

    def action_cursor_down(self):
        table = self.query_one("#sessions-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self):
        table = self.query_one("#sessions-table", DataTable)
        table.action_cursor_up()

    def action_jump(self):
        sid = self._get_selected_session_id()
        if not sid:
            return
        try:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT tmux_pane FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
            db.close()
            if row and row["tmux_pane"]:
                pane = row["tmux_pane"]
                subprocess.run(["tmux", "select-window", "-t", pane], timeout=2)
                subprocess.run(["tmux", "select-pane", "-t", pane], timeout=2)
        except Exception:
            pass

    def action_toggle_detail(self):
        detail = self.query_one("#detail", DetailPanel)
        if "hidden" in detail.classes:
            detail.remove_class("hidden")
            sid = self._get_selected_session_id()
            if sid:
                self.selected_session_id = sid
                self._update_detail(sid)
        else:
            detail.add_class("hidden")

    def action_dismiss(self):
        sid = self._get_selected_session_id()
        if not sid:
            return
        try:
            db = sqlite3.connect(DB_PATH)
            db.execute(
                "UPDATE sessions SET status = 'dismissed' WHERE session_id = ?",
                (sid,),
            )
            db.commit()
            db.close()
            self.refresh_data()
        except Exception:
            pass

    def action_show_all(self):
        self.show_ended = not self.show_ended
        self.refresh_data()

    def action_force_refresh(self):
        self.refresh_data()


if __name__ == "__main__":
    app = SessionTracker()
    app.run()
