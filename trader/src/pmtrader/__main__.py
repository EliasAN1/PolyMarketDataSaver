"""python -m pmtrader [--dry-run] | python -m pmtrader close-positions."""

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
    if len(sys.argv) > 1 and sys.argv[1] == "close-positions":
        raise SystemExit(_close_positions_main(sys.argv[2:]))

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
    trader = Trader(cfg=cfg, orders=orders, config_path=args.config.resolve())

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


def _close_positions_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pmtrader close-positions",
        description="Redeem settled positions and FAK-sell anything still open",
    )
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--log-file", type=Path, default=Path("logs/trades.jsonl"))
    parser.add_argument("--scan-only", action="store_true", help="List the close queue without executing")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be redeemed/sold; do not post",
    )
    args = parser.parse_args(argv)

    load_dotenv(args.env)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    from pmtrader.close import run_close_cycle

    orders: OrderClient | None = None
    if not args.scan_only:
        orders = OrderClient(dry_run=args.dry_run, tick_size="0.01", log_path=args.log_file)
        if not args.dry_run:
            orders.connect()
    report = run_close_cycle(orders=orders, dry_run=args.dry_run, scan_only=args.scan_only)
    return 1 if report.failed else 0


if __name__ == "__main__":
    main()
