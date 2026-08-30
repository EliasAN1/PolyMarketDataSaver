"""python -m pmtrader.ui — analyzer only (no trader)."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from pmtrader.config import load_dotenv
from pmtrader.orders import OrderClient
from pmtrader.ui.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="pmtrader Trade Analyzer")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--log-file", type=Path, default=Path("logs/trades.jsonl"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3848)
    args = parser.parse_args()
    load_dotenv(args.env)

    client = None
    try:
        orders = OrderClient(dry_run=False, tick_size="0.01", log_path=args.log_file)
        orders.connect()
        client = orders.client
    except SystemExit:
        client = None

    app = create_app(log_path=args.log_file, order_client=client)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
