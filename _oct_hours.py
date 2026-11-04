"""Oct 1-20 local: per hour skipped, filled, and P&L under both filters."""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, r"C:\Users\Elias\Desktop\Programming\PolyMarketDataSaver\trader\src")
from pmtrader.atr import Candle, assess

TRADES = Path(r"C:\Users\Elias\Desktop\Programming\PolyMarketDataSaver\data\trades.jsonl")
LOCAL = timezone(timedelta(hours=2))
START = datetime(2026, 10, 1, 0, 0, tzinfo=LOCAL)
END = datetime(2026, 10, 21, 0, 0, tzinfo=LOCAL)


def fetch(start_ms: int, end_ms: int) -> list[Candle]:
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol=BTCUSDT&interval=5m&startTime={cursor}&endTime={end_ms - 1}&limit=1000"
        )
        batch = None
        err = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    batch = json.load(resp)
                break
            except Exception as exc:  # noqa: BLE001
                err = exc
                time.sleep(1.0 * (attempt + 1))
        if batch is None:
            raise SystemExit(err)
        if not batch:
            break
        for row in batch:
            rows.append(Candle(int(row[0]) // 1000, float(row[2]), float(row[3]), float(row[4])))
        nxt = int(batch[-1][0]) + 300_000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 1000:
            break
    dedup = {c.open_time_s: c for c in rows}
    return [dedup[k] for k in sorted(dedup)]


def main():
    entries, resolves = {}, {}
    for line in TRADES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        oid = rec.get("order_id")
        if rec.get("event") == "entry" and oid:
            entries[oid] = rec
        elif rec.get("event") == "resolve" and oid:
            resolves[oid] = rec

    rows = []
    paper = 0
    for oid, ent in entries.items():
        res = resolves.get(oid)
        if res is None or res.get("net_pnl_usd") is None:
            continue
        if ent.get("unmatched") or res.get("unmatched"):
            paper += 1
            continue
        ts = float(ent["ts"])
        local = datetime.fromtimestamp(ts, tz=LOCAL)
        if not (START <= local < END):
            continue
        window_end = int(round(float(ent["window_end_ts"])))
        window_start = window_end - 300
        rows.append(
            {
                "local": local,
                "window_start": window_start,
                "delay": ts - window_start,
                "pnl": float(res["net_pnl_usd"]),
            }
        )
    rows.sort(key=lambda r: r["local"])
    start_ms = (min(r["window_start"] for r in rows) - 520 * 300) * 1000
    end_ms = (max(r["window_start"] for r in rows) + 600) * 1000
    candles = fetch(start_ms, end_ms)
    cache: dict[int, object] = {}
    for r in rows:
        ws = r["window_start"]
        if ws not in cache:
            cache[ws] = assess(candles, ws, now_s=ws + 1)
        result = cache[ws]
        atr_ok = hasattr(result, "ratio") and result.ratio <= 1.125
        r["keep"] = r["delay"] > 10 and atr_ok

    hours: dict[tuple[str, int], list] = defaultdict(list)
    for r in rows:
        hours[(r["local"].date().isoformat(), r["local"].hour)].append(r)

    grand_fill = grand_skip = 0
    grand_pnl = 0.0
    current = None
    day_pnl = 0.0
    day_fill = day_skip = 0
    for (day, hour), items in sorted(hours.items()):
        if day != current:
            if current is not None:
                print(f"  DAY {current}  pnl={day_pnl:.2f}  filled={day_fill} skipped={day_skip}")
            current = day
            day_pnl = 0.0
            day_fill = day_skip = 0
            print(f"\n{day}")
        filled = [r for r in items if r["keep"]]
        skipped = [r for r in items if not r["keep"]]
        pnl = sum(r["pnl"] for r in filled)
        print(f"{hour:02d}:00  skipped={len(skipped):3d}  filled={len(filled):3d}  pnl={pnl:9.2f}")
        day_pnl += pnl
        day_fill += len(filled)
        day_skip += len(skipped)
        grand_pnl += pnl
        grand_fill += len(filled)
        grand_skip += len(skipped)
    print(f"  DAY {current}  pnl={day_pnl:.2f}  filled={day_fill} skipped={day_skip}")
    print(f"\nTOTAL filled={grand_fill} skipped={grand_skip} pnl={grand_pnl:.2f} paper_excluded={paper} nodata={sum(1 for r in rows if not r['keep'] and r['delay']>10)}")
    # nodata check
    missing = 0
    for r in rows:
        result = cache[r["window_start"]]
        if not hasattr(result, "ratio"):
            missing += 1
    print(f"ATR_NODATA {missing}")


if __name__ == "__main__":
    main()
