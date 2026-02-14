# Polish UX

## Done

- [x] Fix web dashboard "Send" — added error handling to tmux send-keys (pre-flight pane check, capture stderr, surface errors in chat UI)
- [x] Show Task and plan output in web dashboard transcript

## Next up

## Ideas

- [ ] Markdown rendering improvements (tables, nested lists, etc.)
- [ ] Auto-scroll chat only when already at bottom (don't jump if user scrolled up)
- [ ] Show a "sending..." indicator after sending a message
- [ ] Better empty state when transcript hasn't loaded yet
- [ ] TUI: show transcript output without needing to open detail panel
- [ ] Stale session detection (mark as ended if no activity for X minutes)
