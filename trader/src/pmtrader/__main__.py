"""python -m pmtrader [--dry-run] [--config path]."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from pmtrader.config import load_config, load_dotenv
from pmtrader.orders import OrderClient
from pmtrader.runner import Trader


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket BTC 5m CLOB trader")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to config.toml (default: ./config.toml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log the FAK that would be sent; do not post to the CLOB",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path(".env"),
        help="Optional .env file (default: ./.env)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("logs/trades.jsonl"),
        help="JSONL trade log (default: ./logs/trades.jsonl)",
    )
    args = parser.parse_args()

    load_dotenv(args.env)
    if not args.config.is_file():
        raise SystemExit(f"Config not found: {args.config.resolve()}")
    cfg = load_config(args.config)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    orders = OrderClient(dry_run=args.dry_run, tick_size=cfg.tick_size, log_path=args.log_file)
    trader = Trader(cfg=cfg, orders=orders)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _stop(*_args: object) -> None:
        trader.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _stop())

    try:
        loop.run_until_complete(trader.run())
    except KeyboardInterrupt:
        trader.request_stop()
        loop.run_until_complete(trader.shutdown())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
