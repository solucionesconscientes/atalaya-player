"""Command-line entry point for mpvd (H0: only --version; the server arrives in H1)."""

from __future__ import annotations

import argparse
import sys

from mpvd import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mpvd", description="MPV-UOS companion daemon")
    parser.add_argument("--version", action="version", version=f"mpvd {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
