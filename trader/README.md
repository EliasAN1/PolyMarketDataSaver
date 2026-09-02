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
| `odds_min` / `odds_max` | **Trigger band**: chosen side's **mid** must *enter* this range during the elapsed window (first tick is baseline only). Point underdog: set both to the same level (e.g. `0.35`). Range trigger: e.g. `0.30`–`0.35`. |
| `fak_limit` | **Hard cap on the ask** (optional). The entry is skipped when the ask on the trigger sample is above it; the order is sent one tick above that ask, never above the cap. Defaults to `odds_max` if omitted. The Lab fills at the ask, which is usually a few ticks above the mid, so `0.40` with `odds_max = 0.35` keeps those fills. |
| `use_btc_distance` + `min_btc_away` + `max_btc_away` | \|BTC−PTB\| between min and max dollars. Omit `max_btc_away` for “at least min”. Side follows BTC vs PTB |
| `btc_source` | Spot venue that stands in for BTC: `binance_spot` (default), `coinbase_spot`, `bybit_spot`, or `median` of the three. Matches the Lab's **BTC reference** select. A venue is never silently swapped for another: no price from it means no entry. |
| `use_twap` | 60s TWAP must agree with that side |
| `use_venues` + `min_venues` | At least N of {binance_spot, coinbase_spot, bybit_spot, binance_futures} agree |
| `stake_usd` | pUSD to spend on the FAK |
| `min_seconds_left` | Do not send if the window is about to close |

If you omit both elapsed keys, `use_entry_last` + `entry_last_minutes` still means “last N minutes”.

A band of `0.20`–`0.30` is the Lab default. A point underdog is `odds_min = odds_max = 0.35` (cross 0.35). A favorite band is e.g. `0.80`–`0.90`. Use `fak_limit` when you want a higher pay cap than the trigger top (e.g. trigger `0.35`, pay up to `0.40`).

If BTC-distance is off, the side is whichever book sits in the odds band (cheaper side when `odds_max < 0.5`).

## Entry logic mirrors Strategy Lab

The trader is a live port of the Lab's `findEntry` (`lab_engine.js`), so the same filters give the same trades:

- **Once-per-second sampling.** Ticks only update the snapshot; the entry is evaluated once per second at the Lab's tape boundary (`k + 0.45s`, i.e. the state just before the `t + 0.5` bucket edge of tape row `t`). The watch window is `round(elapsed_from_min * 60)`–`round(elapsed_to_min * 60)` seconds, inclusive, like the Lab's `fromIdx`/`toIdx`.
- **Same-sample band cross.** The side's mid must be outside `[odds_min, odds_max]` on the previous sample and inside on this one, with every other enabled filter (|BTC−PTB|, TWAP, venues) true on the same sample. Nothing is armed and fired later.
- **Fill at the ask.** The fill is this sample's ask; the FAK is sent one tick above it, capped by `fak_limit`. An ask above the cap skips the entry (`ask_above_cap`).
- **BTC reference.** `btc_source` (default `binance_spot`) picks the venue whose price is compared with the price-to-beat; the Lab's **BTC reference** select is the same setting.

## Run

Live is the default once `POLYMARKET_PRIVATE_KEY` is set. Paper-trade with `--dry-run`:

```bash
python -m pmtrader --dry-run
python -m pmtrader --config config.toml
```

One attempt per window. Partial fills count; it will not retry. Winning shares redeem on Polymarket at expiry — this process does not sell.

Fills go to `logs/trades.jsonl` in the same entry/resolve format as the Trade Analyzer.

## Trade Analyzer + profile

While the trader is running, open **http://127.0.0.1:3848** on this PC.

### Phone (Tailscale)

The UI stays on localhost. Tailscale Serve publishes it only to your tailnet (not the public internet).

1. Install Tailscale on the phone and sign in with the same account.
2. On this PC, with the trader (or `python -m pmtrader.ui`) running:

```powershell
.\tailscale-ui.ps1
```

3. On the phone open **https://drelias.tail86f11c.ts.net/**

To stop sharing: `tailscale serve reset`

Analyzer only (no collector):

```bash
python -m pmtrader.ui
```

## Android APK

See [`android/README.md`](android/README.md). The phone app loads this same UI over Tailscale and notifies on fills and settlement.
