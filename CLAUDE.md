# claude-tracking

Monitor all running Claude Code sessions from one dashboard.

## Project Structure

- `claude_tracking/` — main package
  - `server.py` — HTTP server powering the web dashboard (transcript API, send-to-tmux, session management)
  - `dashboard.html` — single-file web UI (HTML/CSS/JS, served by server.py)
  - `tui.py` — terminal UI (Textual-based)
  - `track.py` — session tracking/discovery logic
  - `cli.py` — CLI entry point (`claude-track` command)
  - `setup_hooks.py` — hook setup utilities
- `projects/` — ongoing project trackers (markdown files with task lists)

## Ongoing Work

Check `projects/` for current project plans and task lists. Update them as work is completed.

## Dev

- Entry point: `claude-track` CLI (`claude_tracking/cli.py`)
- Web dashboard: `claude-track web`
- Install locally: `pip install -e .`
- Python 3.9+, only runtime dep is `textual`
