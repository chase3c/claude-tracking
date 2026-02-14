# Polish UX

## Next up

- [ ] Fix web dashboard "Send" — messages aren't actually reaching the Claude session via tmux send-keys. Debug the full flow: API endpoint → tmux pane lookup → send-keys -l + Enter.

## Ideas

- [ ] Markdown rendering improvements (tables, nested lists, etc.)
- [ ] Auto-scroll chat only when already at bottom (don't jump if user scrolled up)
- [ ] Show a "sending..." indicator after sending a message
- [ ] Better empty state when transcript hasn't loaded yet
- [ ] TUI: show transcript output without needing to open detail panel
- [ ] Stale session detection (mark as ended if no activity for X minutes)
