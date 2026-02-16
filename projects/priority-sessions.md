# Priority Sessions

Mark sessions as priority so they stand out visually and sort to the top of their columns.

## Design

### Interaction
- `p` keybinding toggles priority on the selected session (same pattern as `d` for dismiss, `a` for show all)
- Toggle is instant — updates DB and refreshes immediately

### Visual Treatment
- Priority cards get a distinct visual indicator that stacks with status colors
- Options to consider:
  - **Star/marker in card header**: prepend `★` or `!` to the project name line
  - **Border style change**: use `thick` or `heavy` border instead of `round` (Textual supports several box types)
  - **Background tint**: slightly different background color for priority cards
  - Likely do a combination — marker + border change is the most readable in a terminal

### Sorting
- Priority sessions sort to the top within each kanban column
- Among priority sessions, existing sort order applies (last_activity DESC)
- Among non-priority sessions, existing sort order applies

### Storage
- Add `is_priority INTEGER DEFAULT 0` column to the `sessions` table in `tracking.db`
- Migration in `init_db()` (same pattern as existing `transcript_path` and `source` column migrations)
- DB-backed so it persists across TUI restarts
- Web dashboard can pick this up later for free

## Tasks

- [x] Add `is_priority` column to DB schema + migration in `track.py`
- [x] Add `p` keybinding to `SessionTracker` to toggle priority on selected session
- [x] Update `fetch_sessions()` query to sort priority sessions first within each status group
- [x] Add CSS class + visual styling for priority cards (border style, marker, or both)
- [x] Update `SessionCard.render()` to show priority indicator in card content
- [x] Update `SessionCard.watch_session_data()` to apply/remove priority CSS class
- [x] Show priority count or indicator in the status bar

## Implementation Notes

- The `fetch_sessions()` SQL already sorts by status then `last_activity DESC` — add `is_priority DESC` between the status CASE and `last_activity`
- CSS class `card-priority` can define the visual treatment independent of status classes
- `watch_session_data` already manages status classes — extend it to handle priority class too
- The `PaneOverlay` could also show a priority indicator in its header, but that's optional
