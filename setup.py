#!/usr/bin/env python3
"""Install Claude Code tracking hooks into ~/.claude/settings.json.

Merges tracking hooks alongside any existing hooks without clobbering them.
Creates a backup of the current settings before modifying.
"""
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
TRACK_SCRIPT = str(Path(__file__).parent / "track.py")

HOOK_EVENTS = {
    "SessionStart": [
        {"hooks": [{"type": "command", "command": f"python3 {TRACK_SCRIPT}", "timeout": 5}]}
    ],
    "UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": f"python3 {TRACK_SCRIPT}", "timeout": 5}]}
    ],
    "PostToolUse": [
        {"hooks": [{"type": "command", "command": f"python3 {TRACK_SCRIPT}", "timeout": 5, "async": True}]}
    ],
    "Stop": [
        {"hooks": [{"type": "command", "command": f"python3 {TRACK_SCRIPT}", "timeout": 5}]}
    ],
    "PermissionRequest": [
        {"hooks": [{"type": "command", "command": f"python3 {TRACK_SCRIPT}", "timeout": 5}]}
    ],
    "SessionEnd": [
        {"hooks": [{"type": "command", "command": f"python3 {TRACK_SCRIPT}", "timeout": 5}]}
    ],
}


def install():
    settings = {}
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)

        # Backup
        backup = SETTINGS_PATH + f".backup.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(SETTINGS_PATH, backup)
        print(f"  Backed up settings to {backup}")

    existing_hooks = settings.get("hooks", {})

    for event, entries in HOOK_EVENTS.items():
        if event in existing_hooks:
            # Check if we already installed (avoid duplicates on re-run)
            existing_cmds = {
                h.get("command", "")
                for entry in existing_hooks[event]
                for h in entry.get("hooks", [])
            }
            if TRACK_SCRIPT in " ".join(existing_cmds):
                print(f"  {event}: already installed, skipping")
                continue
            existing_hooks[event].extend(entries)
        else:
            existing_hooks[event] = entries
        print(f"  {event}: added tracking hook")

    settings["hooks"] = existing_hooks

    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print(f"\nHooks installed in {SETTINGS_PATH}")
    print(f"Tracking script: {TRACK_SCRIPT}")
    print(f"\nNew Claude Code sessions will now be tracked automatically.")
    print(f"\nTo view sessions:")
    print(f"  TUI:  python3 {Path(__file__).parent / 'tui.py'}")
    print(f"  Web:  python3 {Path(__file__).parent / 'server.py'}  →  http://localhost:7860")


def uninstall():
    if not os.path.exists(SETTINGS_PATH):
        print("No settings file found.")
        return

    with open(SETTINGS_PATH) as f:
        settings = json.load(f)

    hooks = settings.get("hooks", {})
    for event in list(hooks.keys()):
        hooks[event] = [
            entry for entry in hooks[event]
            if not any(TRACK_SCRIPT in h.get("command", "") for h in entry.get("hooks", []))
        ]
        if not hooks[event]:
            del hooks[event]

    settings["hooks"] = hooks
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print(f"Tracking hooks removed from {SETTINGS_PATH}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--uninstall":
        uninstall()
    else:
        print("Installing Claude Code tracking hooks...\n")
        install()
