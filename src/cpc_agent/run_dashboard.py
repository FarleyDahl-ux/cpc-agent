"""Runs the local review dashboard: python -m cpc_agent.run_dashboard

Binds to 127.0.0.1 only, by design — this is a local, single-user
prototype (see docs/decisions.md), never meant to be reachable from
another machine.
"""
from __future__ import annotations

import argparse

from .webapp import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cpc_agent.run_dashboard",
        description="Local review dashboard for the CPC Preparation Agent (127.0.0.1 only).",
    )
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000).")
    parser.add_argument("--debug", action="store_true", help="Enable Flask's debug/reload mode (development only).")
    args = parser.parse_args(argv)

    app = create_app()
    print(f"CPC Preparation Agent dashboard: http://127.0.0.1:{args.port} (local only — not reachable from other machines)")
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
