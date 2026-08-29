"""Launch the dashboard only (no collector). Safe to run beside a recording process."""

from __future__ import annotations

import argparse
import os

import uvicorn

from pmdsaver.ui.server import app as ui_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve the live/history dashboard and backtest page without starting the collector.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("UI_HOST", "127.0.0.1"),
        help="Bind address (default 127.0.0.1, or UI_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("UI_PORT", "8080")),
        help="Bind port (default 8080, or UI_PORT). Use 8081 if the collector already owns 8080.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    uvicorn.run(
        ui_app,
        host=args.host,
        port=args.port,
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
