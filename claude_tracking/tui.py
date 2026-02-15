"""Claude Code session tracker — Terminal UI.

Run in a tmux pane to monitor all your Claude Code sessions.
"""
import os
import sqlite3
import subprocess
import threading
from datetime import datetime

from rich.table import Table as RichTable
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Static

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
# PaneOverlay — modal overlay showing live tmux pane content
# ---------------------------------------------------------------------------


class PaneOverlay(ModalScreen):
    """Modal overlay showing live tmux pane output with input capability."""

    BINDINGS = [
        Binding("escape", "close_overlay", "Close", priority=True),
    ]

    CSS = """
    PaneOverlay {
        align: center middle;
    }
    #pane-container {
        width: 90%;
        height: 90%;
        border: round $accent;
        background: $surface;
    }
    #pane-header {
        height: auto;
        padding: 0 2;
        background: $panel;
        border-bottom: tall $accent;
    }
    #pane-scroll {
        height: 1fr;
    }
    #pane-content {
        padding: 0 1;
        height: auto;
    }
    #pane-input {
        dock: bottom;
    }
    """

    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self.session_id = session_id
        self._tmux_pane: str | None = None
        self._pane_alive = True
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="pane-container"):
            yield Static(id="pane-header")
            yield VerticalScroll(Static(id="pane-content"), id="pane-scroll")
            yield Input(placeholder="Type and press Enter to send…", id="pane-input")

    def on_mount(self):
        # Look up tmux pane from the database
        try:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT tmux_pane FROM sessions WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            db.close()
            if row:
                self._tmux_pane = row["tmux_pane"]
        except Exception:
            pass

        if not self._tmux_pane:
            self.query_one("#pane-header", Static).update(
                "[dim]No tmux pane found for this session[/]"
            )
            self.query_one("#pane-content", Static).update(
                "[dim]This session has no associated tmux pane.[/]"
            )
            return

        self.query_one("#pane-input", Input).focus()
        self._refresh_pane()
        self._timer = self.set_interval(0.75, self._refresh_pane)

    def _refresh_pane(self):
        if not self._tmux_pane or not self._pane_alive:
            return

        # Update header from DB
        try:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            session = db.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            db.close()
        except Exception:
            session = None

        if session:
            status = session["status"] or "unknown"
            status_label = STATUS_LABELS.get(status, status)
            project = short_project(session["project_dir"] or "")
            model = session["model"] or ""
            header = (
                f"[bold]{project}[/]  {status_label}"
                f"  [dim]{self._tmux_pane}[/]"
            )
            if model:
                header += f"  [dim]{model}[/]"
            self.query_one("#pane-header", Static).update(header)

        # Capture tmux pane content with scrollback history
        scroll = self.query_one("#pane-scroll", VerticalScroll)
        at_bottom = scroll.scroll_y >= scroll.max_scroll_y - 2

        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", self._tmux_pane,
                 "-p", "-e", "-S", "-500"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode != 0:
                self._pane_alive = False
                self.query_one("#pane-content", Static).update(
                    "[dim]Pane no longer exists.[/]"
                )
                if self._timer:
                    self._timer.stop()
                return
            content = Text.from_ansi(result.stdout)
        except FileNotFoundError:
            self._pane_alive = False
            content = Text("tmux is not installed or not in PATH.")
            if self._timer:
                self._timer.stop()
        except subprocess.TimeoutExpired:
            content = Text("tmux capture-pane timed out.")

        self.query_one("#pane-content", Static).update(content)
        # Only auto-scroll if user was already at the bottom
        if at_bottom:
            scroll.scroll_end(animate=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self._tmux_pane or not self._pane_alive:
            return
        text = event.value
        if not text:
            return
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", self._tmux_pane, "-l", text],
                timeout=2,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", self._tmux_pane, "Enter"],
                timeout=2,
            )
        except Exception:
            pass
        self.query_one("#pane-input", Input).clear()
        # Snap back to bottom after sending input
        self.query_one("#pane-scroll", VerticalScroll).scroll_end(animate=False)
        self._refresh_pane()

    def action_close_overlay(self):
        self.dismiss()

    def on_key(self, event) -> None:
        """Handle g/d shortcuts only when input is NOT focused."""
        input_widget = self.query_one("#pane-input", Input)
        if input_widget.has_focus:
            return
        if event.key == "g":
            event.prevent_default()
            event.stop()
            self._do_jump()
        elif event.key == "d":
            event.prevent_default()
            event.stop()
            self._do_dismiss_session()

    def _do_jump(self):
        if not self._tmux_pane:
            return
        try:
            subprocess.run(["tmux", "select-window", "-t", self._tmux_pane], timeout=2)
            subprocess.run(["tmux", "select-pane", "-t", self._tmux_pane], timeout=2)
        except Exception:
            pass

    def _do_dismiss_session(self):
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
            self.push_screen(PaneOverlay(sid))

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
