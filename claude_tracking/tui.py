"""Claude Code session tracker — Terminal UI.

Run in a tmux pane to monitor all your Claude Code sessions.
"""
import json
import os
import sqlite3
import subprocess
import threading
from datetime import datetime

from rich.table import Table as RichTable
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

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


def fetch_sessions(show_all=False):
    if not os.path.exists(DB_PATH):
        return []
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=1000")
        if show_all:
            where = ""
        else:
            where = "WHERE status NOT IN ('dismissed', 'ended')"
        rows = db.execute(f"""
            SELECT * FROM sessions
            {where}
            ORDER BY
                CASE status
                    WHEN 'active' THEN 0
                    WHEN 'waiting' THEN 1
                    WHEN 'idle' THEN 2
                    WHEN 'ended' THEN 3
                    WHEN 'dismissed' THEN 4
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


def _read_transcript_lines(path, source="host"):
    """Read raw lines from a transcript file, using docker exec for containers."""
    if path and os.path.exists(path):
        with open(path) as f:
            return f.readlines()
    if source.startswith("container:"):
        container_id = source[len("container:"):]
        try:
            result = subprocess.run(
                ["docker", "exec", container_id, "cat", path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.splitlines(keepends=True)
        except Exception:
            pass
    return []


def read_transcript(path, max_messages=3, source="host"):
    """Read recent assistant text output from a transcript JSONL file."""
    if not path:
        return []
    messages = []
    try:
        for line in _read_transcript_lines(path, source):
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


# ---------------------------------------------------------------------------
# SessionCard — one tile per session
# ---------------------------------------------------------------------------


class SessionCard(Static):
    """A card widget displaying a single session's status."""

    session_data = reactive(dict, always_update=True)

    def __init__(self, session: dict, **kwargs):
        super().__init__(**kwargs)
        self.session_data = session

    def render(self):
        s = self.session_data
        if not s:
            return ""

        status = s.get("status", "unknown")
        project = short_project(s.get("project_dir", ""))
        activity = format_activity(
            s.get("last_tool", ""),
            s.get("last_detail", ""),
            s.get("last_event", ""),
        )
        last_prompt = s.get("last_prompt", "")
        prompts = s.get("prompt_count", 0)
        tools = s.get("tool_count", 0)
        last = time_ago(s.get("last_activity", ""))
        status_label = STATUS_LABELS.get(status, status)

        # Override activity for waiting sessions
        if status == "waiting":
            activity = "[bold #db6d28]\u26a0 NEEDS PERMISSION[/]"

        # Use a Rich Table for left/right alignment
        table = RichTable(
            show_header=False, box=None, padding=(0, 0), expand=True,
        )
        table.add_column("left", ratio=1, no_wrap=True, overflow="ellipsis")
        table.add_column("right", justify="right", no_wrap=True)

        # Line 1: project name + status
        table.add_row(f"[bold]{project}[/]", status_label)

        # Line 2: current activity
        table.add_row(activity if activity else "[dim]\u2014[/]", "")

        # Line 3: last prompt (truncated, dim)
        if last_prompt:
            prompt_text = last_prompt[:50] + ("\u2026" if len(last_prompt) > 50 else "")
            table.add_row(f'[dim]"{prompt_text}"[/]', "")

        # Line 4: prompt/tool counts + time ago
        table.add_row(f"[dim]P:{prompts} T:{tools}[/]", f"[dim]{last}[/]")

        return table

    def watch_session_data(self, data: dict) -> None:
        for cls in ("status-active", "status-idle", "status-waiting", "status-ended"):
            self.remove_class(cls)
        status = data.get("status", "unknown") if data else "unknown"
        self.add_class(f"status-{status}")


# ---------------------------------------------------------------------------
# DetailScreen — full-screen overlay for a single session
# ---------------------------------------------------------------------------


class DetailScreen(Screen):
    """Full-screen detail view showing session info and transcript."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("q", "go_back", "Back", show=False),
        Binding("g", "jump", "Jump to pane"),
        Binding("d", "dismiss_session", "Dismiss"),
        Binding("r", "refresh", "Refresh"),
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("down", "scroll_down", "Down", show=False),
        Binding("up", "scroll_up", "Up", show=False),
    ]

    CSS = """
    DetailScreen {
        background: $surface;
    }
    #detail-header {
        padding: 1 2;
        background: $panel;
        border-bottom: tall $accent;
        height: auto;
    }
    #detail-scroll {
        height: 1fr;
    }
    #detail-body {
        padding: 1 2;
        height: auto;
    }
    """

    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self.session_id = session_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="detail-header")
        yield VerticalScroll(Static(id="detail-body"), id="detail-scroll")
        yield Footer()

    def on_mount(self):
        self._refresh_detail()
        self._timer = self.set_interval(REFRESH_SECONDS, self._refresh_detail)

    def _refresh_detail(self):
        sessions = fetch_sessions(show_all=True)
        session = next(
            (s for s in sessions if s["session_id"] == self.session_id), None
        )
        if not session:
            self.query_one("#detail-header", Static).update(
                "[dim]Session not found[/]"
            )
            return

        # Header
        lines = []
        status_label = STATUS_LABELS.get(
            session.get("status", ""), session.get("status", "")
        )
        sid_short = self.session_id[:12]
        lines.append(
            f"[bold]{short_project(session.get('project_dir', ''))}[/]"
            f"  {status_label}  [dim]{sid_short}[/]"
        )
        if session.get("model"):
            lines.append(f"[dim]Model:[/] {session['model']}")
        if session.get("tmux_window"):
            pane = session.get("tmux_pane", "?")
            lines.append(f"[dim]Tmux:[/] {session['tmux_window']} ({pane})")
        if session.get("last_prompt"):
            prompt = session["last_prompt"][:100]
            lines.append(f'[dim]Task:[/] "{prompt}"')

        self.query_one("#detail-header", Static).update("\n".join(lines))

        # Body — transcript
        transcript = session.get("transcript_path", "")
        source = session.get("source", "host") or "host"
        recent_output = read_transcript(transcript, max_messages=20, source=source)

        body_lines = []
        if recent_output:
            body_lines.append("[bold]Transcript[/]\n")
            for msg in recent_output:
                preview = msg.replace("\n", " ")
                if len(preview) > 500:
                    preview = preview[:500] + "\u2026"
                body_lines.append(f"  [white]{preview}[/]\n")
        else:
            body_lines.append("[dim]No transcript available[/]")

        self.query_one("#detail-body", Static).update("\n".join(body_lines))

    def action_go_back(self):
        self.dismiss()

    def action_scroll_down(self):
        self.query_one("#detail-scroll", VerticalScroll).scroll_down()

    def action_scroll_up(self):
        self.query_one("#detail-scroll", VerticalScroll).scroll_up()

    def action_jump(self):
        try:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT tmux_pane FROM sessions WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            db.close()
            if row and row["tmux_pane"]:
                pane = row["tmux_pane"]
                subprocess.run(["tmux", "select-window", "-t", pane], timeout=2)
                subprocess.run(["tmux", "select-pane", "-t", pane], timeout=2)
        except Exception:
            pass

    def action_dismiss_session(self):
        try:
            db = sqlite3.connect(DB_PATH)
            db.execute(
                "UPDATE sessions SET status = 'dismissed' WHERE session_id = ?",
                (self.session_id,),
            )
            db.commit()
            db.close()
            self.dismiss()
        except Exception:
            pass

    def action_refresh(self):
        self._refresh_detail()


# ---------------------------------------------------------------------------
# SessionTracker — main app with tiled card grid
# ---------------------------------------------------------------------------


class SessionTracker(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #grid-scroll {
        height: 1fr;
    }
    #card-grid {
        grid-size: 3;
        grid-gutter: 1;
        padding: 1;
        height: auto;
    }
    SessionCard {
        height: auto;
        min-height: 5;
        padding: 1 2;
        border: round $secondary;
        background: $panel;
    }
    SessionCard.status-active {
        border: round green;
    }
    SessionCard.status-waiting {
        border: round #db6d28;
    }
    SessionCard.status-idle {
        border: round yellow;
    }
    SessionCard.status-ended {
        border: round #666666;
    }
    SessionCard.card-selected {
        border: double $accent;
        background: $boost;
    }
    SessionCard.card-selected.status-active {
        border: double green;
        background: $boost;
    }
    SessionCard.card-selected.status-waiting {
        border: double #db6d28;
        background: $boost;
    }
    SessionCard.card-selected.status-idle {
        border: double yellow;
        background: $boost;
    }
    SessionCard.card-selected.status-ended {
        border: double #666666;
        background: $boost;
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
        Binding("h", "move_left", "Left", show=False),
        Binding("l", "move_right", "Right", show=False),
        Binding("j", "move_down", "Down", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("left", "move_left", "Left", show=False),
        Binding("right", "move_right", "Right", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("up", "move_up", "Up", show=False),
        Binding("space", "open_detail", "Detail"),
        Binding("enter", "open_detail", "Detail", show=False),
        Binding("g", "jump", "Jump to pane"),
        Binding("d", "dismiss", "Dismiss"),
        Binding("a", "show_all", "Show ended"),
        Binding("r", "force_refresh", "Refresh"),
    ]

    show_ended = reactive(False)
    selected_index = reactive(0)

    def __init__(self):
        super().__init__()
        self._session_ids: list[str] = []
        self._num_columns = 3

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(Grid(id="card-grid"), id="grid-scroll")
        yield Static(id="status-bar")
        yield Footer()

    async def on_mount(self):
        self._num_columns = self._compute_columns()
        grid = self.query_one("#card-grid", Grid)
        grid.styles.grid_size_columns = self._num_columns

        # Start bridge watcher for container sessions
        from .server import bridge_watcher
        self._bridge_stop = threading.Event()
        self._bridge_thread = threading.Thread(
            target=bridge_watcher, args=(self._bridge_stop,), daemon=True
        )
        self._bridge_thread.start()

        await self.refresh_data()
        self.set_interval(REFRESH_SECONDS, self.refresh_data)

    def on_resize(self, _event):
        num_cols = self._compute_columns()
        if num_cols != self._num_columns:
            self._num_columns = num_cols
            grid = self.query_one("#card-grid", Grid)
            grid.styles.grid_size_columns = num_cols

    def _compute_columns(self) -> int:
        width = self.size.width
        if width < 80:
            return 1
        if width < 120:
            return 2
        return 3

    async def refresh_data(self):
        sessions = fetch_sessions(show_all=self.show_ended)

        # Status bar summary
        counts: dict[str, int] = {}
        for s in sessions:
            counts[s["status"]] = counts.get(s["status"], 0) + 1

        summary_parts = []
        for status in ("active", "waiting", "idle", "ended", "dismissed"):
            if counts.get(status, 0) > 0:
                summary_parts.append(f"{counts[status]} {status}")
        summary = " \u00b7 ".join(summary_parts) if summary_parts else "No sessions"
        mode = "  \u00b7  [bold]Showing all[/]" if self.show_ended else ""

        self.query_one("#status-bar", Static).update(
            f" {summary}  \u00b7  Refreshing every {REFRESH_SECONDS}s{mode}"
        )

        # Preserve selection by session_id
        old_selected_id = None
        if self._session_ids and 0 <= self.selected_index < len(self._session_ids):
            old_selected_id = self._session_ids[self.selected_index]

        new_session_ids = [s["session_id"] for s in sessions]
        grid = self.query_one("#card-grid", Grid)

        if new_session_ids == self._session_ids:
            # Same sessions in same order — update in-place
            cards = list(grid.query(SessionCard))
            for card, session in zip(cards, sessions):
                card.session_data = session
        else:
            # Sessions changed — rebuild grid
            await grid.remove_children()
            new_cards = [SessionCard(session) for session in sessions]
            if new_cards:
                await grid.mount_all(new_cards)
            self._session_ids = new_session_ids

        # Restore selection
        if old_selected_id and old_selected_id in self._session_ids:
            self.selected_index = self._session_ids.index(old_selected_id)
        elif self._session_ids:
            self.selected_index = min(
                self.selected_index, len(self._session_ids) - 1
            )
        else:
            self.selected_index = 0

        self._update_selection()

    def _update_selection(self):
        grid = self.query_one("#card-grid", Grid)
        cards = list(grid.query(SessionCard))
        for i, card in enumerate(cards):
            if i == self.selected_index:
                card.add_class("card-selected")
                card.scroll_visible()
            else:
                card.remove_class("card-selected")

    def _get_selected_session_id(self):
        if self._session_ids and 0 <= self.selected_index < len(self._session_ids):
            return self._session_ids[self.selected_index]
        return None

    # -- Navigation ----------------------------------------------------------

    def action_move_left(self):
        if self.selected_index > 0:
            self.selected_index -= 1
            self._update_selection()

    def action_move_right(self):
        if self.selected_index < len(self._session_ids) - 1:
            self.selected_index += 1
            self._update_selection()

    def action_move_down(self):
        new = self.selected_index + self._num_columns
        if new < len(self._session_ids):
            self.selected_index = new
            self._update_selection()

    def action_move_up(self):
        new = self.selected_index - self._num_columns
        if new >= 0:
            self.selected_index = new
            self._update_selection()

    # -- Actions -------------------------------------------------------------

    def action_open_detail(self):
        sid = self._get_selected_session_id()
        if sid:
            self.push_screen(DetailScreen(sid))

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

    async def action_dismiss(self):
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
            await self.refresh_data()
        except Exception:
            pass

    async def action_show_all(self):
        self.show_ended = not self.show_ended
        await self.refresh_data()

    async def action_force_refresh(self):
        await self.refresh_data()


if __name__ == "__main__":
    app = SessionTracker()
    app.run()
