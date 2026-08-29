"""Feature snapshots from stored odds, spot, TWAP, and volume."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any

from pmdsaver.analyse.summary import calibration, distance_buckets, scatter_points, summarize
from pmdsaver.backtest import engine as bt_engine
from pmdsaver.backtest.replay import (
    Snapshot,
    WindowTape,
    iter_snapshots,
    load_closed_windows,
    resolve_window,
)

SAMPLE_TIMES_S = (60, 120, 180, 240, 300)


@dataclass(slots=True)
class FeatureRow:
    slug: str
    window_id: int
    window_start: int
    window_end: int
    at_s: int
    elapsed_s: float | None
    seconds_left: float | None
    ptb: float
    ptb_source: str | None
    final_price: str
    outcome: str
    resolution_source: str
    up_bid: float | None = None
    up_ask: float | None = None
    up_mid: float | None = None
    down_bid: float | None = None
    down_ask: float | None = None
    down_mid: float | None = None
    btc: float | None = None
    binance_spot: float | None = None
    coinbase_spot: float | None = None
    bybit_spot: float | None = None
    binance_futures: float | None = None
    btc_minus_ptb: float | None = None
    btc_minus_ptb_pct: float | None = None
    twap: float | None = None
    twap_minus_ptb: float | None = None
    volume_base: float | None = None
    volume_coinbase: float | None = None
    spot_side: str | None = None
    odds_side: str | None = None
    odds_agree_spot: bool | None = None
    venues_up: int = 0
    venues_down: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def feature_from_snapshot(tape: WindowTape, snap: Snapshot, at_s: int) -> FeatureRow:
    btc_minus = None if snap.btc is None else snap.btc - snap.ptb
    pct = None if btc_minus is None or snap.ptb == 0 else 100.0 * btc_minus / snap.ptb
    twap_minus = None if snap.twap is None else snap.twap - snap.ptb
    spot_side = None
    if snap.btc is not None:
        if snap.btc > snap.ptb:
            spot_side = "up"
        elif snap.btc < snap.ptb:
            spot_side = "down"
    odds_side = None
    if snap.up_mid is not None:
        if snap.up_mid > 0.5:
            odds_side = "up"
        elif snap.up_mid < 0.5:
            odds_side = "down"
    agree = None
    if spot_side is not None and odds_side is not None:
        agree = spot_side == odds_side
    return FeatureRow(
        slug=tape.slug,
        window_id=tape.window_id,
        window_start=tape.window_start,
        window_end=tape.window_end,
        at_s=at_s,
        elapsed_s=_round(snap.elapsed_s, 3),
        seconds_left=_round(snap.seconds_left, 3),
        ptb=snap.ptb,
        ptb_source=tape.ptb_source,
        final_price=tape.final_price,
        outcome=tape.outcome,
        resolution_source=tape.resolution_source,
        up_bid=_round(snap.up_bid),
        up_ask=_round(snap.up_ask),
        up_mid=_round(snap.up_mid),
        down_bid=_round(snap.down_bid),
        down_ask=_round(snap.down_ask),
        down_mid=_round(snap.down_mid),
        btc=_round(snap.btc, 4),
        binance_spot=_round(snap.binance_spot, 4),
        coinbase_spot=_round(snap.coinbase_spot, 4),
        bybit_spot=_round(snap.bybit_spot, 4),
        binance_futures=_round(snap.binance_futures, 4),
        btc_minus_ptb=_round(btc_minus, 4),
        btc_minus_ptb_pct=_round(pct, 5),
        twap=_round(snap.twap, 4),
        twap_minus_ptb=_round(twap_minus, 4),
        volume_base=_round(snap.volume_base, 6),
        volume_coinbase=_round(snap.volume_coinbase, 6),
        spot_side=spot_side,
        odds_side=odds_side,
        odds_agree_spot=agree,
        venues_up=snap.venues_on_side("up"),
        venues_down=snap.venues_on_side("down"),
    )


def sample_window(conn, tape: WindowTape, at_s: int) -> FeatureRow | None:
    if tape.skip_reason:
        return None
    target = 300 if at_s >= 300 else max(0, int(at_s))
    last: Snapshot | None = None
    chosen: Snapshot | None = None
    for snap in iter_snapshots(conn, tape):
        last = snap
        if snap.elapsed_s <= target:
            chosen = snap
        elif chosen is not None:
            break
    snap = last if target >= 300 else chosen
    if snap is None:
        return None
    return feature_from_snapshot(tape, snap, target)


def _sample_window_job(window_id: int, at_s: int) -> tuple[int, FeatureRow | None, str | None]:
    conn = bt_engine._WORKER_CONN
    if conn is None:
        raise RuntimeError("Worker SQLite connection was not initialized")
    row = conn.execute("SELECT * FROM windows WHERE id = ?", (window_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Window {window_id} not found")
    tape = resolve_window(conn, row)
    return tape.window_start, sample_window(conn, tape, at_s), tape.skip_reason


def collect_features(
    conn,
    at_s: int = 180,
    *,
    slug: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    workers: int = 0,
) -> tuple[list[FeatureRow], dict[str, int]]:
    windows = load_closed_windows(conn, slug=slug, start_ts=start_ts, end_ts=end_ts)
    total = len(windows)
    n_workers = bt_engine._worker_count(workers, total)
    sampled: list[tuple[int, FeatureRow | None, str | None]] = []
    if n_workers == 1:
        for row in windows:
            tape = resolve_window(conn, row)
            sampled.append((tape.window_start, sample_window(conn, tape, at_s), tape.skip_reason))
    else:
        db_path = bt_engine._db_file(conn)
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=bt_engine._init_worker,
            initargs=(db_path,),
        ) as pool:
            futures = [
                pool.submit(_sample_window_job, int(row["id"]), at_s) for row in windows
            ]
            for fut in as_completed(futures):
                sampled.append(fut.result())
    sampled.sort(key=lambda item: (item[0], 0 if item[1] is None else item[1].window_id))
    skip_counts: dict[str, int] = {}
    rows: list[FeatureRow] = []
    for _start, feature, skip_reason in sampled:
        if feature is None:
            reason = skip_reason or "no_snapshot"
            skip_counts[reason] = skip_counts.get(reason, 0) + 1
            continue
        rows.append(feature)
    return rows, skip_counts


def build_analyse_report(
    conn,
    at_s: int = 180,
    *,
    slug: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    workers: int = 0,
) -> dict[str, Any]:
    target = 300 if at_s >= 300 else max(0, int(at_s))
    rows, skip_counts = collect_features(
        conn,
        target,
        slug=slug,
        start_ts=start_ts,
        end_ts=end_ts,
        workers=workers,
    )
    summary = summarize(rows)
    summary["skip_counts"] = skip_counts
    summary["skipped"] = sum(skip_counts.values())
    summary["at_s"] = target
    return {
        "at_s": target,
        "windows": [row.to_dict() for row in rows],
        "buckets": distance_buckets(rows),
        "calibration": calibration(rows),
        "scatter": scatter_points(rows),
        "summary": summary,
    }
