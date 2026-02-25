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
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Static

DB_PATH = os.path.expanduser("~/.claude/tracking.db")
TUI_PANE_FILE = os.path.expanduser("~/.claude/tui-pane")
REFRESH_SECONDS = 3
JUMP_BACK_KEY = "t"  # prefix + t to jump back to TUI


def _register_tui_pane():
    """Save this TUI's tmux pane location and register a jump-back keybinding."""
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        return
    # Get our session name
    session = ""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane, "#S"],
            capture_output=True, text=True, timeout=2,
        )
        session = result.stdout.strip()
    except Exception:
        pass
    # Write pane info so the keybinding can read it
    try:
        os.makedirs(os.path.dirname(TUI_PANE_FILE), exist_ok=True)
        with open(TUI_PANE_FILE, "w") as f:
            f.write(f"{pane}\n{session}\n")
    except Exception:
        return
    # Register tmux keybinding: prefix + T -> jump back to this pane
    jump_cmd = (
        f"sh -c '"
        f"PANE=$(head -1 {TUI_PANE_FILE}); "
        f"SESSION=$(sed -n 2p {TUI_PANE_FILE}); "
        f'[ -n "$SESSION" ] && tmux switch-client -t "$SESSION"; '
        f'[ -n "$PANE" ] && tmux select-window -t "$PANE" && tmux select-pane -t "$PANE"'
        f"'"
    )
    try:
        subprocess.run(["tmux", "unbind-key", JUMP_BACK_KEY], timeout=2)
        subprocess.run(
            ["tmux", "bind-key", JUMP_BACK_KEY, "run-shell", jump_cmd],
            timeout=2,
        )
    except Exception:
        pass


STATUS_DOTS = {
    "active": "[green]\u25cf[/]",
    "idle": "[yellow]\u25cf[/]",
    "waiting": "[#db6d28]\u25cf[/]",
    "pending": "[#4a9eff]\u25cf[/]",
    "ended": "[dim]\u25cf[/]",
}

STATUS_LABELS = {
    "active": "[green]Active[/]",
    "idle": "[yellow]Idle[/]",
    "waiting": "[#db6d28]Waiting[/]",
    "pending": "[#4a9eff]Pending[/]",
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


def fuzzy_match(query: str, target: str) -> bool:
    """Return True if all query chars appear in order in target."""
    if not query:
        return True
    target = target.lower()
    query = query.lower()
    idx = 0
    for char in query:
        pos = target.find(char, idx)
        if pos == -1:
            return False
        idx = pos + 1
    return True


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
                COALESCE(is_priority, 0) DESC,
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

        # Override activity for special statuses
        if status == "waiting":
            activity = "[bold #db6d28]\u26a0 NEEDS PERMISSION[/]"
        if status == "pending":
            reason = s.get("pending_reason", "") or ""
            activity = f"[#4a9eff]\u23f8 {reason}[/]" if reason else "[#4a9eff]\u23f8 Pending[/]"

        # Use a Rich Table for left/right alignment
        table = RichTable(
            show_header=False, box=None, padding=(0, 0), expand=True,
        )
        table.add_column("left", ratio=1, no_wrap=True, overflow="ellipsis")
        table.add_column("right", justify="right", no_wrap=True)

        # Line 1: custom name (if set) or project dir + status
        priority_marker = "[bold #bb77ff]★[/] " if s.get("is_priority") else ""
        custom_name = s.get("name", "")
        if custom_name:
            table.add_row(f"{priority_marker}[bold]{custom_name}[/]", status_label)
            table.add_row(f"[dim]{project}[/]", "")
        else:
            table.add_row(f"{priority_marker}[bold]{project}[/]", status_label)

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
        if data and data.get("is_priority"):
            self.add_class("card-priority")
        else:
            self.remove_class("card-priority")


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
    #pane-hint {
        dock: bottom;
        height: 1;
        padding: 0 2;
        background: $panel;
        color: $text-muted;
    }
    """

    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self.session_id = session_id
        self._tmux_pane: str | None = None
        self._tmux_session: str | None = None
        self._pane_alive = True
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="pane-container"):
            yield Static(id="pane-header")
            yield VerticalScroll(Static(id="pane-content"), id="pane-scroll")
            yield Static(
                "j/k: navigate \u00b7 Enter: select \u00b7 1\u20135: pick \u00b7 Tab: amend \u00b7 g: jump \u00b7 d: dismiss \u00b7 Esc: close",
                id="pane-hint",
            )

    def on_mount(self):
        # Look up tmux pane from the database
        try:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT tmux_pane, tmux_session FROM sessions WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            db.close()
            if row:
                self._tmux_pane = row["tmux_pane"]
                self._tmux_session = row["tmux_session"]
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

    def _send_key(self, key: str):
        """Send a single key to the tmux pane."""
        if not self._tmux_pane or not self._pane_alive:
            return
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", self._tmux_pane, key],
                timeout=2,
            )
        except Exception:
            pass
        self._refresh_pane()

    def action_close_overlay(self):
        self.dismiss()

    def on_key(self, event) -> None:
        """Forward navigation keys to tmux pane for permission picker."""
        if event.key == "g":
            event.prevent_default()
            event.stop()
            self._do_jump()
        elif event.key == "d":
            event.prevent_default()
            event.stop()
            self._do_dismiss_session()
        elif event.key in ("j", "k"):
            event.prevent_default()
            event.stop()
            self._send_key(event.key)
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            self._send_key("Enter")
        elif event.key == "tab":
            event.prevent_default()
            event.stop()
            self._send_key("Tab")
        elif event.key in ("1", "2", "3", "4", "5"):
            event.prevent_default()
            event.stop()
            self._send_key(event.key)

    def _do_jump(self):
        if not self._tmux_pane:
            return
        try:
            if self._tmux_session:
                subprocess.run(
                    ["tmux", "switch-client", "-t", self._tmux_session],
                    timeout=2,
                )
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
# PendingReasonScreen — prompt for a reason when marking a session pending
# ---------------------------------------------------------------------------


class PendingReasonScreen(ModalScreen):
    """Modal to enter a reason when marking a session as pending."""

    CSS = """
    PendingReasonScreen {
        align: center middle;
    }
    #reason-container {
        width: 60;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #reason-label {
        height: auto;
        margin-bottom: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def compose(self) -> ComposeResult:
        with Vertical(id="reason-container"):
            yield Static("[bold]Pending reason[/] [dim](optional — press Enter to skip)[/]", id="reason-label")
            yield Input(placeholder="e.g. waiting on PR #123…", id="reason-input")

    def on_mount(self):
        self.query_one("#reason-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# SessionTracker — main app with kanban-style status columns
# ---------------------------------------------------------------------------


class SessionTracker(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #columns {
        height: 1fr;
    }
    .status-column {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    .col-header {
        height: auto;
        padding: 0 1;
        text-align: center;
    }
    .col-empty {
        height: auto;
        padding: 0 1;
        text-align: center;
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
    SessionCard.status-pending {
        border: round #4a9eff;
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
    SessionCard.card-selected.status-pending {
        border: double #4a9eff;
        background: $boost;
    }
    SessionCard.card-priority {
        border: thick #bb77ff;
    }
    SessionCard.card-priority.status-active {
        border: thick #bb77ff;
    }
    SessionCard.card-priority.status-waiting {
        border: thick #bb77ff;
    }
    SessionCard.card-priority.status-idle {
        border: thick #bb77ff;
    }
    SessionCard.card-priority.status-ended {
        border: thick #bb77ff;
    }
    SessionCard.card-selected.card-priority {
        border: double #bb77ff;
        background: $boost;
    }
    SessionCard.card-selected.card-priority.status-active {
        border: double #bb77ff;
        background: $boost;
    }
    SessionCard.card-selected.card-priority.status-waiting {
        border: double #bb77ff;
        background: $boost;
    }
    SessionCard.card-selected.card-priority.status-idle {
        border: double #bb77ff;
        background: $boost;
    }
    SessionCard.card-selected.card-priority.status-ended {
        border: double #bb77ff;
        background: $boost;
    }
    #status-bar {
        height: 1;
        padding: 0 2;
        background: $boost;
        color: $text-muted;
    }
    #search-bar {
        height: auto;
        padding: 0 1;
        display: none;
    }
    #search-bar.active {
        display: block;
    }
    #search-bar Input {
        width: 1fr;
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
        Binding("p", "toggle_priority", "Priority"),
        Binding("d", "dismiss", "Dismiss"),
        Binding("a", "show_all", "Show ended"),
        Binding("r", "force_refresh", "Refresh"),
        Binding("/", "start_search", "Search"),
        Binding("w", "toggle_pending", "Pending"),
    ]

    show_ended = reactive(False)

    # Column definitions: (col_id, status_key, header_icon)
    _BASE_COLUMNS = [
        ("col-waiting", "waiting", "\u26a0"),
        ("col-idle", "idle", "\u25cf"),
        ("col-active", "active", "\u25cf"),
        ("col-pending", "pending", "\u23f8"),
    ]
    _ENDED_COLUMN = ("col-ended", "ended", "\u25cf")

    def __init__(self):
        super().__init__()
        # Each entry: (col_id, [session_ids])
        self._columns: list[tuple[str, list[str]]] = []
        self._sel_col: int = 0
        self._sel_row: int = 0
        # Map session_id -> session dict for quick lookup
        self._sessions_by_id: dict[str, dict] = {}
        self._searching: bool = False
        self._search_query: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="columns"):
            for col_id, _status, _icon in self._BASE_COLUMNS:
                with VerticalScroll(id=col_id, classes="status-column"):
                    yield Static(classes="col-header")
        with Horizontal(id="search-bar"):
            yield Input(placeholder="fuzzy search sessions…", id="search-input")
        yield Static(id="status-bar")
        yield Footer()

    async def on_mount(self):
        _register_tui_pane()

        from .track import cleanup_stale_sessions
        cleanup_stale_sessions()

        # Start bridge watcher for container sessions
        from .server import bridge_watcher
        self._bridge_stop = threading.Event()
        self._bridge_thread = threading.Thread(
            target=bridge_watcher, args=(self._bridge_stop,), daemon=True
        )
        self._bridge_thread.start()

        await self.refresh_data()
        self.set_interval(REFRESH_SECONDS, self.refresh_data)

    def _get_column_defs(self):
        """Return active column definitions based on show_ended state."""
        cols = list(self._BASE_COLUMNS)
        if self.show_ended:
            cols.append(self._ENDED_COLUMN)
        return cols

    async def refresh_data(self):
        sessions = fetch_sessions(show_all=self.show_ended)
        if self._search_query:
            sessions = [
                s for s in sessions
                if fuzzy_match(self._search_query, s.get("name", "") or "")
                or fuzzy_match(self._search_query, s.get("project_dir", "") or "")
            ]
        self._sessions_by_id = {s["session_id"]: s for s in sessions}

        # Status bar summary
        counts: dict[str, int] = {}
        for s in sessions:
            counts[s["status"]] = counts.get(s["status"], 0) + 1

        summary_parts = []
        for status in ("active", "waiting", "idle", "ended", "dismissed"):
            if counts.get(status, 0) > 0:
                summary_parts.append(f"{counts[status]} {status}")
        summary = " \u00b7 ".join(summary_parts) if summary_parts else "No sessions"
        priority_count = sum(1 for s in sessions if s.get("is_priority"))
        priority_label = f"  \u00b7  [bold #bb77ff]\u2605 {priority_count} priority[/]" if priority_count else ""
        mode = "  \u00b7  [bold]Showing all[/]" if self.show_ended else ""

        self.query_one("#status-bar", Static).update(
            f" {summary}{priority_label}  \u00b7  Refreshing every {REFRESH_SECONDS}s{mode}"
        )

        # Preserve selection by session_id
        old_selected_id = self._get_selected_session_id()

        # Bucket sessions into columns
        col_defs = self._get_column_defs()
        buckets: dict[str, list[dict]] = {col_id: [] for col_id, _, _ in col_defs}
        for s in sessions:
            status = s["status"]
            if status == "pending":
                buckets["col-pending"].append(s)
            elif status == "waiting":
                buckets["col-waiting"].append(s)
            elif status == "idle":
                buckets["col-idle"].append(s)
            elif status == "active":
                buckets["col-active"].append(s)
            elif status in ("ended", "dismissed") and "col-ended" in buckets:
                buckets["col-ended"].append(s)

        # Build old column data for diffing
        old_col_sids = {col_id: sids for col_id, sids in self._columns}

        # Update columns tracking
        new_columns: list[tuple[str, list[str]]] = []
        for col_id, _status_key, _icon in col_defs:
            sids = [s["session_id"] for s in buckets.get(col_id, [])]
            new_columns.append((col_id, sids))

        # Update each column's widgets
        for col_id, status_key, icon in col_defs:
            try:
                container = self.query_one(f"#{col_id}", VerticalScroll)
            except Exception:
                continue

            col_sessions = buckets.get(col_id, [])
            new_sids = [s["session_id"] for s in col_sessions]
            old_sids = old_col_sids.get(col_id, [])

            # Update header
            status_label = status_key.upper()
            header = container.query(".col-header")
            if header:
                header.first().update(
                    f"{icon} {status_label} ({len(col_sessions)})"
                )

            if new_sids == old_sids:
                # Same sessions — update cards in-place
                cards = list(container.query(SessionCard))
                for card, session in zip(cards, col_sessions):
                    card.session_data = session
            else:
                # Remove old cards and empty placeholders
                for card in list(container.query(SessionCard)):
                    await card.remove()
                for empty in list(container.query(".col-empty")):
                    await empty.remove()

                if col_sessions:
                    new_cards = [SessionCard(s) for s in col_sessions]
                    await container.mount_all(new_cards)
                else:
                    await container.mount(
                        Static("[dim]None[/]", classes="col-empty")
                    )

        self._columns = new_columns

        # Restore selection — follow by session_id if it moved columns
        if old_selected_id:
            found = False
            for ci, (col_id, sids) in enumerate(self._columns):
                if old_selected_id in sids:
                    self._sel_col = ci
                    self._sel_row = sids.index(old_selected_id)
                    found = True
                    break
            if not found:
                self._clamp_selection()
        else:
            self._clamp_selection()

        self._update_selection()

    def _clamp_selection(self):
        """Clamp selection to valid bounds."""
        if not self._columns:
            self._sel_col = 0
            self._sel_row = 0
            return
        self._sel_col = min(self._sel_col, len(self._columns) - 1)
        col_sids = self._columns[self._sel_col][1]
        if col_sids:
            self._sel_row = min(self._sel_row, len(col_sids) - 1)
        else:
            self._sel_row = 0

    def _update_selection(self):
        # Clear all selections
        for card in self.query(SessionCard):
            card.remove_class("card-selected")

        sid = self._get_selected_session_id()
        if not sid:
            return

        # Find and highlight the selected card
        for col_id, sids in self._columns:
            if sid in sids:
                idx = sids.index(sid)
                try:
                    container = self.query_one(f"#{col_id}", VerticalScroll)
                    cards = list(container.query(SessionCard))
                    if 0 <= idx < len(cards):
                        cards[idx].add_class("card-selected")
                        cards[idx].scroll_visible()
                except Exception:
                    pass
                break

    def _get_selected_session_id(self):
        if not self._columns:
            return None
        if 0 <= self._sel_col < len(self._columns):
            sids = self._columns[self._sel_col][1]
            if sids and 0 <= self._sel_row < len(sids):
                return sids[self._sel_row]
        return None

    # -- Navigation ----------------------------------------------------------

    def action_move_left(self):
        if not self._columns:
            return
        new_col = self._sel_col - 1
        if new_col < 0:
            return
        self._sel_col = new_col
        col_sids = self._columns[self._sel_col][1]
        if col_sids:
            self._sel_row = min(self._sel_row, len(col_sids) - 1)
        else:
            self._sel_row = 0
        self._update_selection()

    def action_move_right(self):
        if not self._columns:
            return
        new_col = self._sel_col + 1
        if new_col >= len(self._columns):
            return
        self._sel_col = new_col
        col_sids = self._columns[self._sel_col][1]
        if col_sids:
            self._sel_row = min(self._sel_row, len(col_sids) - 1)
        else:
            self._sel_row = 0
        self._update_selection()

    def action_move_down(self):
        if not self._columns:
            return
        col_sids = self._columns[self._sel_col][1]
        if self._sel_row < len(col_sids) - 1:
            self._sel_row += 1
            self._update_selection()

    def action_move_up(self):
        if not self._columns:
            return
        if self._sel_row > 0:
            self._sel_row -= 1
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
                "SELECT tmux_pane, tmux_session FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
            db.close()
            if row and row["tmux_pane"]:
                pane = row["tmux_pane"]
                target_session = row["tmux_session"]
                if target_session:
                    subprocess.run(
                        ["tmux", "switch-client", "-t", target_session],
                        timeout=2,
                    )
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

    async def action_toggle_pending(self):
        sid = self._get_selected_session_id()
        if not sid:
            return
        session = self._sessions_by_id.get(sid, {})
        if session.get("status") == "pending":
            # Clear pending → idle
            try:
                db = sqlite3.connect(DB_PATH)
                db.execute(
                    "UPDATE sessions SET status = 'idle', pending_reason = NULL WHERE session_id = ?",
                    (sid,),
                )
                db.commit()
                db.close()
            except Exception:
                pass
        else:
            # Prompt for reason then mark pending
            reason = await self.push_screen_wait(PendingReasonScreen())
            if reason is None:
                return  # cancelled
            try:
                db = sqlite3.connect(DB_PATH)
                db.execute(
                    "UPDATE sessions SET status = 'pending', pending_reason = ? WHERE session_id = ?",
                    (reason or None, sid),
                )
                db.commit()
                db.close()
            except Exception:
                pass
        await self.refresh_data()

    async def action_toggle_priority(self):
        sid = self._get_selected_session_id()
        if not sid:
            return
        try:
            db = sqlite3.connect(DB_PATH)
            db.execute("PRAGMA busy_timeout=1000")
            row = db.execute(
                "SELECT COALESCE(is_priority, 0) FROM sessions WHERE session_id = ?",
                (sid,),
            ).fetchone()
            new_val = 0 if (row and row[0]) else 1
            db.execute(
                "UPDATE sessions SET is_priority = ? WHERE session_id = ?",
                (new_val, sid),
            )
            db.commit()
            db.close()
            await self.refresh_data()
        except Exception:
            pass

    async def action_show_all(self):
        self.show_ended = not self.show_ended
        columns_container = self.query_one("#columns", Horizontal)
        if self.show_ended:
            # Mount the ended column
            col = VerticalScroll(
                Static(classes="col-header"),
                id="col-ended",
                classes="status-column",
            )
            await columns_container.mount(col)
        else:
            # Remove the ended column
            try:
                ended_col = self.query_one("#col-ended", VerticalScroll)
                await ended_col.remove()
            except Exception:
                pass
        await self.refresh_data()

    async def action_force_refresh(self):
        await self.refresh_data()

    def action_start_search(self):
        """Open search bar and enter insert mode."""
        self._searching = True
        self.query_one("#search-bar").add_class("active")
        self.query_one("#search-input", Input).focus()

    def _enter_search_normal_mode(self):
        """Blur input but keep filter active — back to j/k navigation."""
        self.set_focus(None)

    async def _clear_search(self):
        """Clear filter and hide search bar entirely."""
        self._searching = False
        self._search_query = ""
        inp = self.query_one("#search-input", Input)
        inp.value = ""
        self.query_one("#search-bar").remove_class("active")
        self.set_focus(None)
        await self.refresh_data()

    async def on_input_changed(self, event: Input.Changed) -> None:
        self._search_query = event.value
        await self.refresh_data()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in insert mode: drop to normal mode to navigate results."""
        self._enter_search_normal_mode()

    async def on_key(self, event) -> None:
        inp = self.query_one("#search-input", Input)
        input_focused = self.focused is inp

        if input_focused and event.key == "escape":
            # Insert mode → normal mode (keep filter)
            event.prevent_default()
            self._enter_search_normal_mode()
        elif self._searching and not input_focused and event.key == "escape":
            # Normal mode with filter → clear search
            event.prevent_default()
            await self._clear_search()
        elif self._searching and not input_focused and event.key == "slash":
            # Normal mode → back to insert mode
            event.prevent_default()
            inp.focus()


if __name__ == "__main__":
    app = SessionTracker()
    app.run()
