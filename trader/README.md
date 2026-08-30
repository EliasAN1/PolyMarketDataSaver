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

[`config.toml`](config.toml) knobs (AND together; volume is not used):

| Knob | Meaning |
| --- | --- |
| `use_entry_last` + `entry_last_minutes` | Only arm in the last N minutes of the window |
| `odds_min` / `odds_max` | Chosen side's **ask** must **cross into** this band *after* last-N starts. Already sitting there when the period opens is ignored. FAK limit = `odds_max` |
| `use_btc_distance` + `min_btc_away` | BTC (Binance spot, else Coinbase, else Bybit) at least X from PTB. Side follows BTC vs PTB |
| `use_twap` | 60s TWAP must agree with that side |
| `use_venues` + `min_venues` | At least N of {binance_spot, coinbase_spot, bybit_spot, binance_futures} agree |
| `stake_usd` | pUSD to spend on the FAK |
| `min_seconds_left` | Do not send if the window is about to close |

A single “enter at 0.25” is `odds_min = 0.01`, `odds_max = 0.25`. A favorite band is e.g. `0.75`–`0.99`.

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
