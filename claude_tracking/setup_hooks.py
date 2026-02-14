"""Install/uninstall Claude Code tracking hooks."""
import json
import os
import shutil
from datetime import datetime

SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
HOOK_COMMAND = "claude-track hook"

HOOK_EVENTS = {
    "SessionStart": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 5}]}
    ],
    "UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 5}]}
    ],
    "PostToolUse": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 5, "async": True}]}
    ],
    "Stop": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 5}]}
    ],
    "PermissionRequest": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 5}]}
    ],
    "SessionEnd": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 5}]}
    ],
}


def install():
    settings = {}
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
        backup = SETTINGS_PATH + f".backup.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(SETTINGS_PATH, backup)
        print(f"  Backed up settings to {backup}")

    existing_hooks = settings.get("hooks", {})

    for event, entries in HOOK_EVENTS.items():
        existing_cmds = {
            h.get("command", "")
            for entry in existing_hooks.get(event, [])
            for h in entry.get("hooks", [])
        }
        if HOOK_COMMAND in existing_cmds:
            print(f"  {event}: already installed, skipping")
            continue
        existing_hooks.setdefault(event, []).extend(entries)
        print(f"  {event}: added tracking hook")

    settings["hooks"] = existing_hooks

    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print(f"\nHooks installed in {SETTINGS_PATH}")
    print(f"\nNew Claude Code sessions will now be tracked automatically.")
    print(f"\nTo view sessions:")
    print(f"  claude-track tui   — terminal dashboard")
    print(f"  claude-track web   — web dashboard at http://localhost:7860")


def uninstall():
    if not os.path.exists(SETTINGS_PATH):
        print("No settings file found.")
        return

    with open(SETTINGS_PATH) as f:
        settings = json.load(f)

    hooks = settings.get("hooks", {})
    removed = 0
    for event in list(hooks.keys()):
        before = len(hooks[event])
        hooks[event] = [
            entry for entry in hooks[event]
            if not any(HOOK_COMMAND in h.get("command", "") for h in entry.get("hooks", []))
        ]
        removed += before - len(hooks[event])
        if not hooks[event]:
            del hooks[event]

    settings["hooks"] = hooks
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print(f"Removed {removed} tracking hooks from {SETTINGS_PATH}")


if __name__ == "__main__":
    install()
