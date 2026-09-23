import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ct", description="codetools: run web-chat AI batches against a local project.")
    parser.add_argument("root", nargs="?", default=".", help="project root (default: current directory)")
    parser.add_argument("--no-watch", action="store_true", help="start with clipboard watching off")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error(f"{root} is not a directory")

    from .engine import Engine
    from .settings import SettingsError
    from .tui import CodetoolsApp

    try:
        engine = Engine(root)
    except SettingsError as e:
        print(f"codetools: bad settings: {e}", file=sys.stderr)
        return 2
    CodetoolsApp(engine, watch=not args.no_watch).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
