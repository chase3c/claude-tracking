# Polish UX

## Done

- [x] Fix web dashboard "Send" — added error handling to tmux send-keys (pre-flight pane check, capture stderr, surface errors in chat UI)
- [x] Show Task and plan output in web dashboard transcript

## In Progress

- [ ] Investigate filing a Claude Code issue for missing permission denial hook event (PermissionRequest fires but nothing fires when user denies — known gap, see #19628, #13024)

## Done (recent)

- [x] TUI: Permission granting from pane overlay — replaced text input with key passthrough (j/k/Enter/1-5/Tab) to interact with Claude's permission picker without leaving the TUI
- [x] Permission-aware session status tracking (`pending_permissions` counter in DB)
  - Increment on `PermissionRequest`, decrement on `PostToolUse`/`PostToolUseFailure`
  - Reset on `Stop`, `UserPromptSubmit`, `SessionEnd`, `Notification(idle_prompt)`
  - Session stays `waiting` until all pending permissions resolved
  - Fixes sub-agent permission flickering (last-write-wins → counter-based)
- [x] Added hooks for `PostToolUseFailure`, `SubagentStart`, `SubagentStop`, `Notification`
- [x] `Notification(idle_prompt)` → idle status (catches "Claude is waiting for your input")
- [x] `Notification(permission_prompt)` → reinforces waiting status
- [x] Events without clear status meaning (`SubagentStart/Stop`, unknown notifications) now preserve current status instead of flipping to active
- [x] Debug payload dump (`/tmp/hook-dump.jsonl`) for inspecting raw hook data

## Ideas

- [ ] Markdown rendering improvements (tables, nested lists, etc.)
- [ ] Auto-scroll chat only when already at bottom (don't jump if user scrolled up)
- [ ] Show a "sending..." indicator after sending a message
- [ ] Better empty state when transcript hasn't loaded yet
- [x] TUI: replace DetailScreen with live tmux pane overlay (PaneOverlay)
- [x] Stale session detection — solved via `Notification(idle_prompt)` + preserve-status for non-meaningful events
