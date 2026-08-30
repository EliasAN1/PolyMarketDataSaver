# pmtrader

Standalone live trader for Polymarket's rolling **BTC UP/DOWN 5m** market. It watches the same public feeds as the collector (CLOB book, 60s TWAP, Binance / Coinbase / Bybit), and when every enabled filter is true it places **one FAK buy** on that window.

This package does not import `pmdsaver` and does not write SQLite.

## Setup

```bash
cd trader
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env
```

Edit `.env` with the **Polymarket proxy / funder** address that holds **pUSD**, and the private key that can sign for it. Approve the CLOB exchange on polymarket.com before going live. Deposit-wallet accounts usually need `POLYMARKET_SIGNATURE_TYPE=3` and API creds filled in by hand.

## Config

[`config.toml`](config.toml) knobs match **Strategy Lab** (AND together; volume is not streamed in the trader):

| Knob | Meaning |
| --- | --- |
| `elapsed_from_min` / `elapsed_to_min` | Minutes from window open. `2.5`–`3.0` is 2:30–3:00. Last 3 minutes of a 5m window is `2.0`–`5.0`. |
| `odds_min` / `odds_max` | Chosen side's **mid** must *enter* this band during the elapsed window (first tick is baseline only). **Ask** must also sit in the band (point underdog: ask ≤ level). FAK limit = `odds_max` so you do not pay 0.70 on a 0.35 cross. |
| `use_btc_distance` + `min_btc_away` + `max_btc_away` | \|BTC−PTB\| between min and max dollars. Omit `max_btc_away` for “at least min”. Side follows BTC vs PTB |
| `use_twap` | 60s TWAP must agree with that side |
| `use_venues` + `min_venues` | At least N of {binance_spot, coinbase_spot, bybit_spot, binance_futures} agree |
| `stake_usd` | pUSD to spend on the FAK |
| `min_seconds_left` | Do not send if the window is about to close |

If you omit both elapsed keys, `use_entry_last` + `entry_last_minutes` still means “last N minutes”.

A band of `0.20`–`0.30` is the Lab default. A point underdog is `odds_min = odds_max = 0.35` (cross 0.35, pay at most 0.35). A favorite band is e.g. `0.80`–`0.90`.

If BTC-distance is off, the side is whichever book sits in the odds band (cheaper side when `odds_max < 0.5`).

## Run

Live is the default once `POLYMARKET_PRIVATE_KEY` is set. Paper-trade with `--dry-run`:

```bash
python -m pmtrader --dry-run
python -m pmtrader --config config.toml
```

One attempt per window. Partial fills count; it will not retry. Winning shares redeem on Polymarket at expiry — this process does not sell.

Fills go to `logs/trades.jsonl` in the same entry/resolve format as the Trade Analyzer.

## Trade Analyzer + profile

While the trader is running, open **http://127.0.0.1:3848**. That page is a 1:1 copy of the pm-centionaire Trade Analyzer (P&L hero, recap, equity, daily, trades table). **Profile** in the header opens a window with CLOB + Data API account data: balance/allowance, open orders, positions, recent fills, activity, notifications.

Analyzer only (no collector):

```bash
python -m pmtrader.ui
```
