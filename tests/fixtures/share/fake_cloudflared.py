#!/usr/bin/env python3
"""A fake ``cloudflared`` for the H25 tunnel tests: no network, no Cloudflare account.

It imitates what the real one does on stderr (a banner with the trycloudflare address) and then stays alive until it is
killed, writing its pid where the test asks so the test can check it really dies with the room.

Env:
  FAKE_CF_URL        address to announce (default https://fake-tunel.trycloudflare.com); empty = announce none
  FAKE_CF_DELAY      seconds before announcing it (default 0.1)
  FAKE_CF_PIDFILE    file to write the pid into
  FAKE_CF_EXIT       exit with this code instead of waiting (after the delay)
  FAKE_CF_ARGSFILE   file to append the received argv to
"""

from __future__ import annotations

import os
import sys
import time

URL = os.environ.get("FAKE_CF_URL", "https://fake-tunel.trycloudflare.com")
DELAY = float(os.environ.get("FAKE_CF_DELAY", "0.1"))


def main() -> int:
    if args_file := os.environ.get("FAKE_CF_ARGSFILE"):
        with open(args_file, "a", encoding="utf-8") as fh:
            fh.write(" ".join(sys.argv[1:]) + "\n")
    if pid_file := os.environ.get("FAKE_CF_PIDFILE"):
        with open(pid_file, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    print("2026-10-01T00:00:00Z INF Thank you for trying Cloudflare Tunnel.", file=sys.stderr, flush=True)
    time.sleep(DELAY)
    if URL:
        print("2026-10-01T00:00:00Z INF +------------------------------------------------+", file=sys.stderr)
        print(f"2026-10-01T00:00:00Z INF |  {URL}  |", file=sys.stderr)
        print("2026-10-01T00:00:00Z INF +------------------------------------------------+", file=sys.stderr, flush=True)
    if (code := os.environ.get("FAKE_CF_EXIT")) is not None:
        print("2026-10-01T00:00:00Z ERR failed to connect to the edge", file=sys.stderr, flush=True)
        return int(code)
    while True:                       # the real one lives as long as the tunnel
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
