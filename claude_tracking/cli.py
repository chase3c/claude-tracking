"""CLI entry point for claude-track."""
import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="claude-track",
        description="Monitor all running Claude Code sessions.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="Install tracking hooks into Claude Code")
    sub.add_parser("uninstall", help="Remove tracking hooks")
    sub.add_parser("tui", help="Launch terminal UI dashboard")

    web_parser = sub.add_parser("web", help="Launch web dashboard")
    web_parser.add_argument(
        "-p", "--port", type=int, default=7860, help="Port (default: 7860)"
    )

    sub.add_parser("hook", help="Process a hook event from stdin (internal)")

    name_parser = sub.add_parser("set-name", help="Name the current session")
    name_parser.add_argument("name", help="Display name for this session")

    bridge_parser = sub.add_parser(
        "bridge-dirs", help="Manage directories scanned for container bridge events"
    )
    bridge_sub = bridge_parser.add_subparsers(dest="bridge_action")
    bridge_sub.add_parser("list", help="List configured bridge directories")
    bridge_add = bridge_sub.add_parser("add", help="Add a directory")
    bridge_add.add_argument("path", help="Directory path to add")
    bridge_rm = bridge_sub.add_parser("remove", help="Remove a directory")
    bridge_rm.add_argument("path", help="Directory path to remove")

    args = parser.parse_args()

    if args.command == "setup":
        from .setup_hooks import install
        install()

    elif args.command == "uninstall":
        from .setup_hooks import uninstall
        uninstall()

    elif args.command == "tui":
        try:
            from .tui import SessionTracker
        except ImportError:
            print("textual is required for the TUI: pip install textual")
            sys.exit(1)
        app = SessionTracker()
        app.run()

    elif args.command == "web":
        from .server import run_server
        run_server(args.port)

    elif args.command == "hook":
        from .track import handle_hook
        handle_hook()

    elif args.command == "set-name":
        from .track import set_name
        try:
            session_id = set_name(args.name)
            print(f"Named session {session_id[:8]}… → \"{args.name}\"")
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "bridge-dirs":
        from .server import load_bridge_dirs, save_bridge_dirs
        dirs = load_bridge_dirs()
        if args.bridge_action == "list":
            if dirs:
                for d in dirs:
                    print(d)
            else:
                print("No bridge directories configured.")
                print("Add one with: claude-track bridge-dirs add /path/to/workspace")
        elif args.bridge_action == "add":
            path = os.path.abspath(args.path)
            if path not in dirs:
                dirs.append(path)
                save_bridge_dirs(dirs)
                print(f"Added: {path}")
            else:
                print(f"Already configured: {path}")
        elif args.bridge_action == "remove":
            path = os.path.abspath(args.path)
            if path in dirs:
                dirs.remove(path)
                save_bridge_dirs(dirs)
                print(f"Removed: {path}")
            else:
                print(f"Not found: {path}")
        else:
            bridge_parser.print_help()

    else:
        parser.print_help()
