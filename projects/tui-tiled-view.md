# TUI Tiled View

Redesign the TUI from a table-based list to a tiled card/grid layout.

## Goals

- Each session is a card/tile with a colored border indicating status
  - Green: active
  - Orange: waiting for permission
  - Yellow: idle
  - Dim/gray: ended/dismissed
- Card shows: project name, current activity, last output preview
- Permission-waiting state should be immediately visually obvious
- Designed for 6-10 sessions max

## Detail View

- Select a tile and press space/enter to expand into a detailed view
- Shows: full transcript messages, event history, model info, tmux pane info
- Options for how to present it:
  - Expand in place (tile grows, pushes others aside)
  - Overlay/modal (panel slides over grid)
  - Split view (tiles left, detail right)
- Dismiss to return to grid

## Tasks

- [x] Design card widget with status-colored borders
- [x] Build grid/tiled layout to replace DataTable
- [x] Show key info per card (project, activity, last output snippet)
- [x] Highlight permission-waiting sessions prominently
- [x] Add detail view toggle (expand selected tile)
- [x] Show transcript messages in detail view
- [x] Keyboard nav: arrow keys between tiles, enter/space for detail
- [x] Keep keybindings: g (jump to pane), d (dismiss), a (show all), q (quit)

## Kanban Columns (done)

Replaced flat grid with kanban-style status columns: Waiting → Idle → Active (left to right).
- `Horizontal` container with 3 `VerticalScroll` columns, each with header showing count
- Navigation changed from flat `selected_index` to `(sel_col, sel_row)` — h/l between columns, j/k within
- Selection follows session across column moves when status changes
- `a` toggle dynamically mounts/removes a 4th "Ended" column
- Empty columns show header + "None"
- Removed responsive grid logic (`_compute_columns`, `on_resize`)

## Implementation Notes

- `SessionCard(Static)` renders Rich Table markup with 4 lines: project+status, activity, last prompt, counts+time
- Waiting sessions show bold orange "NEEDS PERMISSION" label
- `PaneOverlay(ModalScreen)` replaced `DetailScreen` — shows live tmux pane content via `tmux capture-pane -p -e -S -500`, auto-refreshes every 0.75s
  - Input bar sends commands via `tmux send-keys`
  - Scrollback: captures 500 lines of history, preserves scroll position (only auto-scrolls if already at bottom)
  - `g` jumps to the tmux pane, `d` dismisses, Escape closes
  - Removed all transcript/JSONL parsing (`fetch_events`, `read_transcript`, `_read_transcript_lines`, `import json`)
- Selection tracked by session_id across refreshes; in-place updates when session list is unchanged per-column
- `refresh_data` is async to properly await `remove_children()`/`mount_all()`
- tmux: `prefix + g` bound to `last-window` for quick return from jumped-to panes
