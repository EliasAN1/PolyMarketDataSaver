"""Walk saved windows, fill one hold-to-expiry trade, report PnL."""

from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from pmdsaver.backtest.fees import CRYPTO_TAKER_FEE_RATE, shares_for_stake, taker_fee
from pmdsaver.backtest.replay import (
    Snapshot,
    WindowTape,
    iter_snapshots,
    load_closed_windows,
    resolve_window,
)
from pmdsaver.backtest.strategies import Strategy, build_strategy


@dataclass(slots=True)
class BacktestConfig:
    strategy: str = "combo"
    stake: float = 1.0
    fill: str = "ask"
    fee_rate: float = CRYPTO_TAKER_FEE_RATE
    entry_after_s: float = 15.0
    min_distance: float = 10.0
    max_distance: float | None = None
    max_ask: float = 0.75
    cheap_ask: float = 0.55
    hit_odds: float = 0.25
    odds_lo: float | None = None
    odds_hi: float | None = None
    last_minutes: float = 3.0
    use_last_minutes: bool = True
    elapsed_from_min: float | None = None
    elapsed_to_min: float | None = None
    use_odds: bool = True
    use_spot: bool = False
    use_twap: bool = False
    use_volume: bool = False
    min_volume: float = 0.0
    use_venues: bool = False
    min_venues: int = 2
    workers: int = 0
    slug: str | None = None
    start_ts: int | None = None
    end_ts: int | None = None


@dataclass(slots=True)
class TradeRow:
    window_id: int
    slug: str
    side: str | None
    fill: float | None
    shares: float
    cost: float | None
    fee: float | None
    outcome: str | None
    pnl: float | None
    entry_ts_ms: int | None
    ptb: str | None
    final_price: str | None
    resolution_source: str | None
    ptb_source: str | None
    status: str
    skip_reason: str | None = None
    btc_minus_ptb: float | None = None
    twap_minus_ptb: float | None = None
    up_mid: float | None = None
    volume: float | None = None
    elapsed_s: float | None = None


@dataclass(slots=True)
class BacktestReport:
    strategy: str
    fill: str
    stake: float
    fee_rate: float
    windows: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    skipped: int = 0
    no_trade: int = 0
    fees_paid: float = 0.0
    net_pnl: float = 0.0
    win_rate: float | None = None
    avg_pnl: float | None = None
    rows: list[TradeRow] = field(default_factory=list)
    equity: list[dict[str, Any]] = field(default_factory=list)
    skip_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["net_pnl"] = round(self.net_pnl, 6)
        payload["avg_pnl"] = None if self.avg_pnl is None else round(self.avg_pnl, 6)
        payload["win_rate"] = None if self.win_rate is None else round(self.win_rate, 4)
        payload["fees_paid"] = round(self.fees_paid, 6)
        return payload


def _snapshot_at(snap: Snapshot, tape: WindowTape, ts_ms: int) -> Snapshot:
    return replace(
        snap,
        ts_ms=ts_ms,
        elapsed_s=max(0.0, ts_ms / 1000.0 - tape.window_start),
        seconds_left=max(0.0, tape.window_end - ts_ms / 1000.0),
    )


def _watch_span(config: BacktestConfig, tape: WindowTape) -> tuple[bool, float, float]:
    duration = float(tape.window_end - tape.window_start)
    if config.elapsed_from_min is not None or config.elapsed_to_min is not None:
        lo_m = 0.0 if config.elapsed_from_min is None else float(config.elapsed_from_min)
        hi_m = duration / 60.0 if config.elapsed_to_min is None else float(config.elapsed_to_min)
        lo_s, hi_s = lo_m * 60.0, hi_m * 60.0
        if lo_s > hi_s:
            lo_s, hi_s = hi_s, lo_s
        return True, max(0.0, lo_s), min(duration, hi_s)
    if _uses_last_minutes(config):
        return True, max(0.0, duration - float(config.last_minutes) * 60.0), duration
    return False, 0.0, duration


def _iter_watch(conn: sqlite3.Connection, tape: WindowTape, from_s: float, to_s: float):
    from_ms = int((tape.window_start + from_s) * 1000)
    to_ms = int((tape.window_start + to_s) * 1000)
    last: Snapshot | None = None
    emitted_from = from_s <= 0
    for snap in iter_snapshots(conn, tape):
        if snap.ts_ms > to_ms:
            if last is not None and last.ts_ms < to_ms:
                yield _snapshot_at(last, tape, to_ms)
            return
        if (
            not emitted_from
            and last is not None
            and last.ts_ms < from_ms <= snap.ts_ms
        ):
            emitted_from = True
            yield _snapshot_at(last, tape, from_ms)
        last = snap
        if snap.ts_ms >= from_ms:
            emitted_from = True
            yield snap
    end_ms = tape.window_end * 1000
    if last is None:
        return
    if not emitted_from and last.ts_ms < from_ms <= end_ms:
        yield _snapshot_at(last, tape, min(from_ms, to_ms))
        emitted_from = True
    if last.ts_ms < to_ms <= end_ms:
        yield _snapshot_at(last, tape, to_ms)


def run_window(
    conn: sqlite3.Connection,
    tape: WindowTape,
    config: BacktestConfig,
    strategy: Strategy,
) -> TradeRow:
    if tape.skip_reason:
        return TradeRow(
            window_id=tape.window_id,
            slug=tape.slug,
            side=None,
            fill=None,
            shares=0.0,
            cost=None,
            fee=None,
            outcome=tape.outcome or None,
            pnl=None,
            entry_ts_ms=None,
            ptb=tape.ptb or None,
            final_price=tape.final_price or None,
            resolution_source=tape.resolution_source or None,
            ptb_source=tape.ptb_source,
            status="skipped",
            skip_reason=tape.skip_reason,
        )

    mode = config.fill if config.fill in ("ask", "mid") else "ask"
    stake = max(0.0, float(config.stake))
    fee_rate = max(0.0, float(config.fee_rate))
    side: str | None = None
    fill: float | None = None
    entry_ts: int | None = None
    fill_snap: Snapshot | None = None
    use_watch, from_s, to_s = _watch_span(config, tape)
    ticks = _iter_watch(conn, tape, from_s, to_s) if use_watch else iter_snapshots(conn, tape)

    for snap in ticks:
        signal = strategy.on_tick(snap)
        if signal is None:
            continue
        px = snap.fill_price(signal, mode)
        if px is None or px <= 0 or px >= 1:
            continue
        side = signal
        fill = px
        entry_ts = snap.ts_ms
        fill_snap = snap
        break

    if side is None or fill is None:
        return TradeRow(
            window_id=tape.window_id,
            slug=tape.slug,
            side=None,
            fill=None,
            shares=0.0,
            cost=None,
            fee=None,
            outcome=tape.outcome,
            pnl=None,
            entry_ts_ms=None,
            ptb=tape.ptb,
            final_price=tape.final_price,
            resolution_source=tape.resolution_source,
            ptb_source=tape.ptb_source,
            status="no_trade",
        )

    shares = shares_for_stake(stake, fill)
    cost = stake
    fee = taker_fee(shares, fill, fee_rate)
    won = side == tape.outcome
    payout = shares if won else 0.0
    pnl = payout - cost - fee
    btc_minus = None
    twap_minus = None
    up_mid = None
    volume = None
    elapsed_s = None
    if fill_snap is not None:
        if fill_snap.btc is not None:
            btc_minus = round(fill_snap.btc - fill_snap.ptb, 4)
        if fill_snap.twap is not None:
            twap_minus = round(fill_snap.twap - fill_snap.ptb, 4)
        if fill_snap.up_mid is not None:
            up_mid = round(fill_snap.up_mid, 6)
        if fill_snap.volume_base is not None:
            volume = round(fill_snap.volume_base, 6)
        elapsed_s = round(fill_snap.elapsed_s, 3)
    return TradeRow(
        window_id=tape.window_id,
        slug=tape.slug,
        side=side,
        fill=round(fill, 6),
        shares=round(shares, 6),
        cost=round(cost, 6),
        fee=round(fee, 6),
        outcome=tape.outcome,
        pnl=round(pnl, 6),
        entry_ts_ms=entry_ts,
        ptb=tape.ptb,
        final_price=tape.final_price,
        resolution_source=tape.resolution_source,
        ptb_source=tape.ptb_source,
        status="win" if won else "loss",
        btc_minus_ptb=btc_minus,
        twap_minus_ptb=twap_minus,
        up_mid=up_mid,
        volume=volume,
        elapsed_s=elapsed_s,
    )


def _uses_last_minutes(config: BacktestConfig) -> bool:
    if config.strategy in ("hit_odds", "hit_75", "hit_25"):
        return True
    if config.strategy == "combo":
        return bool(config.use_last_minutes)
    return False


def _strategy_from_config(config: BacktestConfig) -> Strategy:
    return build_strategy(
        config.strategy,
        entry_after_s=config.entry_after_s,
        min_distance=config.min_distance,
        max_distance=config.max_distance,
        max_ask=config.max_ask,
        cheap_ask=config.cheap_ask,
        hit_odds=config.hit_odds,
        odds_lo=config.odds_lo,
        odds_hi=config.odds_hi,
        last_minutes=config.last_minutes,
        use_last_minutes=config.use_last_minutes,
        elapsed_from_min=config.elapsed_from_min,
        elapsed_to_min=config.elapsed_to_min,
        use_odds=config.use_odds,
        use_spot=config.use_spot,
        use_twap=config.use_twap,
        use_volume=config.use_volume,
        min_volume=config.min_volume,
        use_venues=config.use_venues,
        min_venues=config.min_venues,
    )


def _worker_count(requested: int, total: int) -> int:
    if total <= 1:
        return 1
    if requested <= 0:
        cpu = os.cpu_count() or 4
        # Tick replay is CPU-bound Python; too many processes thrash the SQLite file.
        requested = max(2, min(4, cpu))
    return max(1, min(requested, total, 16))


def _db_file(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None:
        raise RuntimeError("SQLite has no main database")
    path = row["file"] if isinstance(row, sqlite3.Row) else row[2]
    if not path:
        raise RuntimeError("Backtest parallel replay needs a file-backed SQLite database")
    return str(path)


_WORKER_CONN: sqlite3.Connection | None = None


def _init_worker(db_path: str) -> None:
    """One read-only SQLite connection per process, reused across windows."""
    global _WORKER_CONN
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA cache_size = -65536")
    _WORKER_CONN = conn


def _replay_window_job(window_id: int, config: BacktestConfig) -> tuple[int, int, TradeRow]:
    conn = _WORKER_CONN
    if conn is None:
        raise RuntimeError("Worker SQLite connection was not initialized")
    row = conn.execute("SELECT * FROM windows WHERE id = ?", (window_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Window {window_id} not found")
    tape = resolve_window(conn, row)
    trade = run_window(conn, tape, config, _strategy_from_config(config))
    return tape.window_start, tape.window_end, trade


def _progress(done: int, total: int, workers: int, trade: TradeRow) -> dict[str, Any]:
    return {
        "type": "progress",
        "done": done,
        "left": total - done,
        "total": total,
        "workers": workers,
        "slug": trade.slug,
        "status": trade.status,
        "skip_reason": trade.skip_reason,
    }


def _apply_trade(
    report: BacktestReport,
    trade: TradeRow,
    window_end: int,
    skip_counts: dict[str, int],
    equity: float,
) -> float:
    report.windows += 1
    report.rows.append(trade)
    if trade.status == "skipped":
        report.skipped += 1
        reason = trade.skip_reason or "skipped"
        skip_counts[reason] = skip_counts.get(reason, 0) + 1
        return equity
    if trade.status == "no_trade":
        report.no_trade += 1
        return equity
    report.trades += 1
    if trade.status == "win":
        report.wins += 1
    else:
        report.losses += 1
    report.fees_paid += float(trade.fee or 0.0)
    equity += float(trade.pnl or 0.0)
    report.equity.append(
        {
            "t": window_end,
            "slug": trade.slug,
            "pnl": trade.pnl,
            "equity": round(equity, 6),
        }
    )
    return equity


def iter_backtest(conn: sqlite3.Connection, config: BacktestConfig):
    """Yield start/progress/done events while building the same report as run_backtest."""
    strategy = _strategy_from_config(config)
    report = BacktestReport(
        strategy=strategy.name,
        fill=config.fill,
        stake=float(config.stake),
        fee_rate=float(config.fee_rate),
    )
    windows = load_closed_windows(
        conn,
        slug=config.slug,
        start_ts=config.start_ts,
        end_ts=config.end_ts,
    )
    total = len(windows)
    workers = _worker_count(config.workers, total)
    yield {
        "type": "start",
        "done": 0,
        "left": total,
        "total": total,
        "workers": workers,
    }
    completed: list[tuple[int, int, TradeRow]] = []
    if workers == 1:
        for row in windows:
            tape = resolve_window(conn, row)
            trade = run_window(conn, tape, config, strategy)
            completed.append((tape.window_start, tape.window_end, trade))
            yield _progress(len(completed), total, workers, trade)
    else:
        db_path = _db_file(conn)
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_worker,
            initargs=(db_path,),
        ) as pool:
            futures = [
                pool.submit(_replay_window_job, int(row["id"]), config)
                for row in windows
            ]
            for fut in as_completed(futures):
                item = fut.result()
                completed.append(item)
                yield _progress(len(completed), total, workers, item[2])

    completed.sort(key=lambda item: (item[0], item[1], item[2].window_id))
    skip_counts: dict[str, int] = {}
    equity = 0.0
    for _start, window_end, trade in completed:
        equity = _apply_trade(report, trade, window_end, skip_counts, equity)
    report.net_pnl = equity
    report.skip_counts = skip_counts
    if report.trades:
        report.win_rate = report.wins / report.trades
        report.avg_pnl = report.net_pnl / report.trades
    yield {"type": "done", "report": report}


def run_backtest(conn: sqlite3.Connection, config: BacktestConfig) -> BacktestReport:
    report: BacktestReport | None = None
    for event in iter_backtest(conn, config):
        if event["type"] == "done":
            report = event["report"]
    if report is None:
        raise RuntimeError("backtest produced no report")
    return report
