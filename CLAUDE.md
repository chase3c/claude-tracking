# claude-tracking

Monitor all running Claude Code sessions from one dashboard.

## Project Structure

- `claude_tracking/` — main package
  - `tui.py` — terminal UI (Textual-based) — **primary interface**
  - `track.py` — session tracking/discovery logic
  - `cli.py` — CLI entry point (`claude-track` command)
  - `setup_hooks.py` — hook setup utilities
  - `server.py` — HTTP server for web dashboard (not actively used)
  - `dashboard.html` — web UI (not actively used)
- `projects/` — ongoing project trackers (markdown files with task lists)

## Active Interface

The TUI (`claude-track` in a tmux pane) is the primary interface. It uses a kanban-style layout with status columns (Waiting | Idle | Active, optionally Ended). The web dashboard exists but is not currently in use.

## Ongoing Work

Check `projects/` for current project plans and task lists. Update them as work is completed.

## Dev

- Entry point: `claude-track` CLI (`claude_tracking/cli.py`)
- TUI: `claude-track` (default)
- Web dashboard: `claude-track web` (not actively used)
- Install locally: `pip install -e .`
- Python 3.9+, only runtime dep is `textual`
