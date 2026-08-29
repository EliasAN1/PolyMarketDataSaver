# Polymarket BTC 5m Data Saver

Async Python collector for Polymarket's rolling **BTC UP/DOWN 5m** market plus aligned exchange feeds. Data is stored in a local SQLite database.

## What it collects

| Data | Source |
| --- | --- |
| Price to beat | Polymarket RTDS 60s Chainlink TWAP at window open + Gamma `eventMetadata.priceToBeat` |
| UP/DOWN odds (tick-by-tick) | Polymarket CLOB market WebSocket |
| Binance spot price | Binance `aggTrade` |
| Binance USDT-M futures price | Binance futures `aggTrade` |
| Coinbase spot price | Coinbase Advanced Trade `market_trades` |
| Bybit spot price | Bybit spot `publicTrade` |
| 5m candle volume | Binance spot `kline_5m`, Coinbase `candles` |

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## Run

Collector **and** dashboard together (one process):

```bash
python -m pmdsaver
```

Then open **http://127.0.0.1:8080**. Live values are pushed over a WebSocket from in-memory ticks. SQLite is only for storage / history.

To open the dashboard or backtest page **without stopping a collector already on 8080**, start a second UI-only process:

```bash
python -m pmdsaver.ui --port 8081
```

Then use **http://127.0.0.1:8081/backtest**. That process only reads SQLite; it does not start another collector. The CLI backtest also leaves recording alone:

```bash
python -m pmdsaver.backtest --strategy hit_odds --hit-odds 0.25
```

Optional environment variables:

- `DATA_DIR` — directory for the SQLite file (default: `./data`, or a `data` folder next to the exe)
- `LOG_LEVEL` — logging level (default: `INFO`)
- `UI_HOST` / `UI_PORT` — dashboard bind (default `127.0.0.1:8080`)
- `UI_OPEN_BROWSER` — open the dashboard in a browser (`1` by default for the exe)

## Windows executable

Build on a Windows PC with Python 3.11+:

```powershell
.\build_exe.ps1
```

Copy the **entire** `dist\pmdsaver\` folder to the monitoring computer (not just `pmdsaver.exe`). Double-click `pmdsaver.exe`. A console window stays open with logs, the dashboard opens at **http://127.0.0.1:8080**, and SQLite is written to `data\pmdsaver.db` next to the exe.

To view the dashboard from another machine on the same LAN, start it with:

```powershell
$env:UI_HOST = "0.0.0.0"
.\pmdsaver.exe
```

Then open `http://<that-pc-ip>:8080` from the other computer. Keep the console window open while monitoring. Close it or press Ctrl+C to stop.

Database path: `DATA_DIR/pmdsaver.db`

## Dashboard UI

The live view is **not** a SQLite poller. Path:

```text
Exchange / Polymarket WS  ->  collector memory (LiveHub)  ->  browser WS
                              \-> SQLite (persist)
```

History (past 5m windows) still loads from SQLite via REST. `python -m pmdsaver.ui` can show history without the collector, but live cards need `python -m pmdsaver`.

## Running on multiple PCs & merging data

The Windows executable is fully portable, so you can run separate collectors on
different PCs (e.g. to cover more uptime, or collect from different networks)
and combine their SQLite databases afterwards:

1. Build once with `.\build_exe.ps1` and copy the whole `dist\pmdsaver\` folder to each PC.
2. Run `pmdsaver.exe` on each PC independently — they don't need to know about each other.
3. When you want to combine a PC's data into your main one, **stop its collector first** (clean shutdown), then copy its `data\pmdsaver.db` (and any `pmdsaver.db-wal` / `pmdsaver.db-shm` next to it — SQLite keeps recent writes there until checkpointed) onto the PC that should hold the combined dataset.
4. Merge it in:

   ```powershell
   .\pmdsaver.exe merge path\to\copied\pmdsaver.db
   ```

   or from source: `python -m pmdsaver.mergedb path\to\copied\pmdsaver.db`

This is safe to merge in either direction and safe to re-run: windows are matched by `slug`, which is deterministic from the fixed UTC 5-minute grid, so the *same* market window collected by two PCs merges into one row (any field one PC missed — e.g. `outcome`, `final_price` — gets filled in from the other) instead of duplicating it. Ticks (`odds_ticks`, `price_ticks`, `twap_ticks`, `candle_volume`) are copied over with their `window_id` remapped to match. The target database is backed up (`pmdsaver.db.bak-<timestamp>`) before every merge; pass `--no-backup` to skip that.

Note: if both PCs happened to record the *same* window while both were running (overlapping uptime), you'll get two independent tick streams for that window (harmless for backtesting, just extra data points) except for `candle_volume`, which is deduplicated by keeping whichever reading is more complete.

Do not copy or torrent `pmdsaver.db` while the collector is running, and do not send the main file without `-wal` / `-shm`. A truncated copy opens as `database disk image is malformed`. How that was diagnosed and salvaged (header `page_count` vs file size, named-column copy, id-range cutoffs) is in [docs/recovering-truncated-sqlite.md](docs/recovering-truncated-sqlite.md).

## SQLite tables

- `windows` — one row per 5m market window (slug, token IDs, price to beat, optional `final_price` / `outcome`)
- `odds_ticks` — CLOB order book / trade updates with UP/DOWN bid/ask/mid
- `price_ticks` — exchange trade ticks (`binance_spot`, `binance_futures`, `coinbase_spot`, `bybit_spot`)
- `candle_volume` — running 5m candle volume from Binance and Coinbase
- `twap_ticks` — Chainlink 60s TWAP prints from Polymarket RTDS

All prices and volumes are stored as TEXT decimals to avoid float rounding.

## Backtest

Replay closed 5m windows from SQLite. One entry per window, hold to expiry (winner pays $1/share).

Dashboard: **http://127.0.0.1:8080/backtest**

```bash
python -m pmdsaver.backtest --strategy hit_odds --hit-odds 0.25 --last-minutes 3 --fill ask
python -m pmdsaver.backtest --strategy hit_odds --hit-odds 0.75 --last-minutes 3 --fill ask
python -m pmdsaver.backtest --strategy spot_lead --shares 10 --fill ask
python -m pmdsaver.backtest --strategy odds_lag --cheap-ask 0.55 --fill mid
```

- `hit_odds` — in the last N minutes, buy the first side that hits **Buy at**. Use `0.75` for the favorite, `0.25` for the cheap side.
- `spot_lead` — after N seconds, if `|BTC − PTB|` is large enough and that side’s ask is still cheap, buy it
- `odds_lag` — if spot is on one side of PTB but that side’s ask is still cheap, buy it

Windows without PTB, a usable CLOB book, or a settlement print are skipped. The winning side is taken from Polymarket Gamma (`markets[0].outcomePrices` once `closed` and `umaResolutionStatus=resolved`). `python -m pmdsaver.backfill_outcomes` fills that in for older databases. Only windows still missing a verified outcome fall back to last TWAP near window end, then last Binance/Coinbase spot, comparing with Polymarket's rule (`TWAP >= price-to-beat` → UP).

The dashboard includes a **Replay** page (`/replay?window_id=N`) that plots every collected feed for one window and lists every tick.

Fill at `ask` is still optimistic versus a live CLOB (no queue or latency).

## Notes

- No API keys required; all feeds are public market data.
- The collector auto-rolls to the next 5m window about 15 seconds before close.
- High-frequency trade streams can produce large databases over time.
- This GitHub repo does **not** include `data/`, `dist/`, or `.venv`. Collect locally after clone; build the Windows exe with `.\build_exe.ps1`.
