# claude-tracking

Monitor all your running Claude Code sessions from one dashboard. See what each session is doing, its status, and jump to any tmux pane instantly.

![TUI and Web dashboards for tracking Claude Code sessions](https://img.shields.io/badge/status-alpha-yellow)

## Install

```bash
pip install .
```

Or if you prefer an isolated install:

```bash
pipx install .
```

## Setup

Install the tracking hooks into Claude Code:

```bash
claude-track setup
```

This adds hooks to `~/.claude/settings.json` that fire on session events (start, stop, tool use, prompts, permission requests). A backup of your settings is created automatically.

All new Claude Code sessions will be tracked from this point forward.

## Usage

### Terminal UI

Best for tmux workflows — run it in its own pane:

```bash
claude-track tui
```

| Key       | Action              |
|-----------|---------------------|
| `Enter`   | Jump to tmux pane   |
| `Space`   | Toggle detail panel |
| `d`       | Dismiss session     |
| `a`       | Show/hide ended     |
| `r`       | Force refresh       |
| `q`       | Quit                |

### Web Dashboard

```bash
claude-track web
```

Opens a dashboard at [http://localhost:7860](http://localhost:7860). Custom port with `-p`:

```bash
claude-track web -p 8080
```

### Both

Run both at the same time — they read from the same SQLite database, no conflicts.

## How It Works

```
Claude Session 1 ──┐
Claude Session 2 ──┤── hooks ──▶ claude-track hook ──▶ ~/.claude/tracking.db
Claude Session 3 ──┘                                         │
                                                    ┌────────┴────────┐
                                                    ▼                 ▼
                                              claude-track tui  claude-track web
                                              (tmux pane)       (localhost:7860)
```

**Tracked events:**

- `SessionStart` — session created, captures tmux pane ID
- `UserPromptSubmit` — what the user asked (shown as current task)
- `PostToolUse` — which tools are running (Edit, Bash, Grep, etc.)
- `Stop` — session idle, waiting for input
- `PermissionRequest` — session blocked, needs approval
- `SessionEnd` — session terminated

**Session statuses:**

- **Active** — Claude is working
- **Idle** — waiting for user input
- **Waiting** — blocked on permission approval
- **Ended** — session terminated

## Uninstall

Remove the tracking hooks:

```bash
claude-track uninstall
```

To fully remove:

```bash
pip uninstall claude-tracking
rm ~/.claude/tracking.db
```
