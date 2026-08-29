"""CLI: python -m pmdsaver.backtest --strategy combo --stake 1 --fill ask"""

from __future__ import annotations

import argparse
import json
import sys

from pmdsaver.backtest.engine import BacktestConfig, iter_backtest
from pmdsaver.ui.queries import connect, db_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay saved Polymarket BTC 5m windows through a hold-to-expiry strategy.",
    )
    parser.add_argument(
        "--strategy",
        choices=("combo", "hit_odds", "hit_75", "hit_25", "spot_lead", "odds_lag"),
        default="combo",
    )
    parser.add_argument("--stake", type=float, default=1.0, help="USDC spent on shares (shares = stake / fill)")
    parser.add_argument("--shares", dest="stake", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--fill", choices=("ask", "mid"), default="ask")
    parser.add_argument(
        "--fee-rate",
        dest="fee_rate",
        type=float,
        default=0.07,
        help="Polymarket taker feeRate in fee = C × feeRate × p × (1-p). Crypto default 0.07.",
    )
    parser.add_argument("--fee", dest="fee_rate", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--entry-after", dest="entry_after_s", type=float, default=15.0)
    parser.add_argument("--min-distance", dest="min_distance", type=float, default=10.0)
    parser.add_argument("--max-ask", dest="max_ask", type=float, default=0.75)
    parser.add_argument("--cheap-ask", dest="cheap_ask", type=float, default=0.55)
    parser.add_argument("--hit-odds", dest="hit_odds", type=float, default=0.25)
    parser.add_argument("--last-minutes", dest="last_minutes", type=float, default=3.0)
    parser.add_argument("--no-last-minutes", dest="use_last_minutes", action="store_false")
    parser.add_argument("--no-odds", dest="use_odds", action="store_false")
    parser.add_argument("--use-spot", dest="use_spot", action="store_true")
    parser.add_argument("--use-twap", dest="use_twap", action="store_true")
    parser.add_argument("--use-volume", dest="use_volume", action="store_true")
    parser.add_argument("--min-volume", dest="min_volume", type=float, default=0.0)
    parser.add_argument("--use-venues", dest="use_venues", action="store_true")
    parser.add_argument("--min-venues", dest="min_venues", type=int, default=2)
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Parallel windows (0 = auto, up to 8). 1 = sequential.",
    )
    parser.add_argument("--slug", default=None)
    parser.add_argument("--from-ts", dest="start_ts", type=int, default=None)
    parser.add_argument("--to-ts", dest="end_ts", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON")
    parser.set_defaults(
        stake=1.0,
        fee_rate=0.07,
        use_last_minutes=True,
        use_odds=True,
        use_spot=False,
        use_twap=False,
        use_volume=False,
        use_venues=False,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = connect()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        report = None
        config = BacktestConfig(
            strategy=args.strategy,
            stake=args.stake,
            fill=args.fill,
            fee_rate=args.fee_rate,
            entry_after_s=args.entry_after_s,
            min_distance=args.min_distance,
            max_ask=args.max_ask,
            cheap_ask=args.cheap_ask,
            hit_odds=args.hit_odds,
            last_minutes=args.last_minutes,
            use_last_minutes=args.use_last_minutes,
            use_odds=args.use_odds,
            use_spot=args.use_spot,
            use_twap=args.use_twap,
            use_volume=args.use_volume,
            min_volume=args.min_volume,
            use_venues=args.use_venues,
            min_venues=args.min_venues,
            workers=args.workers,
            slug=args.slug,
            start_ts=args.start_ts,
            end_ts=args.end_ts,
        )
        for event in iter_backtest(conn, config):
            if event["type"] in ("start", "progress") and not args.json:
                print(
                    f"\r{event['done']} backtested, {event['left']} left"
                    + (f" ({event['workers']} workers)" if event.get("workers") else ""),
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
            elif event["type"] == "done":
                report = event["report"]
        if not args.json:
            print(file=sys.stderr)
    finally:
        conn.close()

    if report is None:
        print("backtest produced no report", file=sys.stderr)
        return 1

    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    win_rate = f"{report.win_rate:.1%}" if report.win_rate is not None else "-"
    print(f"db {db_path()}")
    print(
        f"{report.strategy} fill={report.fill} stake={report.stake:g} fee_rate={report.fee_rate:g} "
        f"windows={report.windows} trades={report.trades} "
        f"win={win_rate} pnl={report.net_pnl:+.4f} fees={report.fees_paid:.4f} "
        f"skipped={report.skipped} no_trade={report.no_trade}"
    )
    if report.skip_counts:
        skipped = ", ".join(f"{k}={v}" for k, v in sorted(report.skip_counts.items()))
        print(f"skip reasons: {skipped}")
    print(f"{'slug':<32} {'side':<5} {'fill':>6} {'out':<4} {'pnl':>10} status")
    for row in report.rows:
        if row.status == "skipped":
            continue
        side = row.side or "-"
        fill = "-" if row.fill is None else f"{row.fill:.3f}"
        outcome = row.outcome or "-"
        pnl = "-" if row.pnl is None else f"{row.pnl:+.4f}"
        print(f"{row.slug:<32} {side:<5} {fill:>6} {outcome:<4} {pnl:>10} {row.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
