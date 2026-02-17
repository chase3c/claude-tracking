# claude-tracking

Monitor all your running Claude Code sessions from one terminal dashboard. See what each session is doing, its status, and jump to any tmux pane instantly — even across tmux sessions.

## Requirements

- Python 3.9+
- [tmux](https://github.com/tmux/tmux) — sessions are tracked by their tmux pane
- [textual](https://github.com/Textualize/textual) (installed automatically)

## Install

```bash
pipx install .
```

Or for development (editable — code changes take effect on restart):

```bash
pipx install -e .
```

> **Gotcha:** A regular `pip install .` copies files to site-packages. If you're actively developing, always use `-e` or your changes won't be picked up.

## Setup

Install the tracking hooks into Claude Code:

```bash
claude-track setup
```

This adds hooks to `~/.claude/settings.json` that fire on session events. A backup of your existing settings is created automatically.

All new Claude Code sessions will be tracked from this point forward. Existing sessions won't appear until their next event.

To remove the hooks later:

```bash
claude-track uninstall
```

## Usage

Run the TUI in a dedicated tmux pane:

```bash
claude-track tui
```

Sessions are displayed in a kanban-style layout with columns for each status: **Waiting** | **Idle** | **Active** (and optionally **Ended**).

### Keybindings

#### Main view

| Key | Action |
|---|---|
| `h/l` or arrows | Move between columns |
| `j/k` or arrows | Move between sessions |
| `Space` / `Enter` | Open pane overlay (live view of session output) |
| `g` | Jump to session's tmux pane (works across tmux sessions) |
| `p` | Toggle priority flag on session |
| `d` | Dismiss session |
| `a` | Show/hide ended sessions |
| `r` | Force refresh |
| `q` | Quit |

#### Pane overlay

| Key | Action |
|---|---|
| `j/k` | Send navigation keys to the session |
| `Enter` | Send Enter to the session |
| `Tab` | Send Tab to the session |
| `1-5` | Send number key to the session |
| `g` | Jump to session's tmux pane |
| `d` | Dismiss session |
| `Esc` | Close overlay |

The pane overlay shows a live capture of the session's tmux pane, refreshing every 750ms. You can interact with permission prompts directly through it.

### Jumping between sessions

Press `g` on any session to jump to its tmux pane. This works across tmux sessions — if the Claude session is in a different tmux session, the TUI will switch your client to it.

To jump back, use **`prefix + t`** from anywhere. The TUI registers this tmux keybinding on startup, pointing to whatever pane it's running in.

> **Note:** This replaces tmux's default `prefix + t` (clock mode). The binding is set at runtime and resets when tmux restarts.

## How it works

```
Claude Session 1 ──┐
Claude Session 2 ──┤── hooks ──▶ claude-track hook ──▶ ~/.claude/tracking.db
Claude Session 3 ──┘                                         │
                                                              ▼
                                                        claude-track tui
                                                        (tmux pane)
```

Claude Code fires hook events on session lifecycle changes. The `claude-track hook` command receives these via stdin and writes them to a SQLite database. The TUI polls that database every 3 seconds.

### Tracked events

| Event | What it captures |
|---|---|
| `SessionStart` | New session, tmux pane ID |
| `UserPromptSubmit` | What the user asked |
| `PostToolUse` | Which tools are running (Edit, Bash, Grep, etc.) |
| `PermissionRequest` | Session blocked, needs approval |
| `Stop` | Session idle, waiting for input |
| `Notification` | Idle prompts |
| `SubagentStart/Stop` | Subagent lifecycle |
| `SessionEnd` | Session terminated |

### Session statuses

| Status | Meaning |
|---|---|
| **Active** | Claude is working |
| **Idle** | Waiting for user input |
| **Waiting** | Blocked on permission approval |
| **Ended** | Session terminated |

## Container support

For Claude Code sessions running inside dev containers, a bridge mechanism forwards events to the host.

```bash
# On the host, register the workspace directory
claude-track bridge-dirs add /path/to/workspace

# In the container, the bridge script writes events to a shared JSONL file
# The host TUI picks them up automatically
```

See `claude_tracking/container_bridge.py` for the container-side setup.

## Data

All tracking data lives in `~/.claude/tracking.db` (SQLite). To fully uninstall:

```bash
claude-track uninstall          # remove hooks
pipx uninstall claude-tracking  # remove package
rm ~/.claude/tracking.db        # remove data
rm ~/.claude/tui-pane           # remove jump-back state
```
