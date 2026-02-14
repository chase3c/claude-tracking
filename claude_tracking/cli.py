"""CLI entry point for claude-track."""
import argparse
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

    else:
        parser.print_help()
