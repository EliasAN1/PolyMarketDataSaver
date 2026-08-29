/* Strategy Lab evaluation engine — pure functions, no DOM.
 * Loaded both on the main thread (<script>) and inside lab_worker.js
 * (importScripts), so it must stay dependency-free.
 *
 * Row layout (see tape.py): [up_ask, down_ask, up_mid, down_mid,
 * btc_minus_ptb, twap_minus_ptb, volume, venues_up, venues_down]
 */
(function (global) {
  "use strict";

  const ROW = {
    UP_ASK: 0,
    DOWN_ASK: 1,
    UP_MID: 2,
    DOWN_MID: 3,
    BTC_MINUS_PTB: 4,
    TWAP_MINUS_PTB: 5,
    VOLUME: 6,
    VENUES_UP: 7,
    VENUES_DOWN: 8,
  };

  function oddsHit(value, hitOdds) {
    if (value == null) return false;
    return hitOdds >= 0.5 ? value >= hitOdds : value <= hitOdds;
  }

  function pickOddsSide(upMid, upAsk, downMid, downAsk, hitOdds) {
    const up = upMid != null ? upMid : upAsk;
    const down = downMid != null ? downMid : downAsk;
    const upHit = oddsHit(up, hitOdds);
    const downHit = oddsHit(down, hitOdds);
    if (upHit && downHit) {
      const buyFavorite = hitOdds >= 0.5;
      if (buyFavorite) return up >= down ? "up" : "down";
      return up <= down ? "up" : "down";
    }
    if (upHit) return "up";
    if (downHit) return "down";
    return null;
  }

  // Polymarket CLOB taker fee: fee = shares * feeRate * price * (1 - price).
  function takerFee(shares, price, rate) {
    if (shares <= 0 || rate <= 0 || price <= 0 || price >= 1) return 0;
    const fee = Math.round(shares * rate * price * (1 - price) * 1e5) / 1e5;
    return fee >= 1e-5 ? fee : 0;
  }

  function findEntry(win, params) {
    const duration = win.end - win.start;
    const rows = win.rows;
    if (!rows || !rows.length) return null;
    const cutoffIdx = params.useLastMinutes
      ? Math.max(0, Math.round(duration - params.lastMinutes * 60))
      : 0;

    for (let t = cutoffIdx; t <= duration; t++) {
      const row = rows[t];
      if (!row) continue;
      const upAsk = row[ROW.UP_ASK];
      const downAsk = row[ROW.DOWN_ASK];
      const upMid = row[ROW.UP_MID];
      const downMid = row[ROW.DOWN_MID];
      const btcMinusPtb = row[ROW.BTC_MINUS_PTB];
      const twapMinusPtb = row[ROW.TWAP_MINUS_PTB];
      const volume = row[ROW.VOLUME];
      const venuesUp = row[ROW.VENUES_UP];
      const venuesDown = row[ROW.VENUES_DOWN];

      let side = null;
      if (params.useSpot) {
        if (btcMinusPtb == null || Math.abs(btcMinusPtb) < params.minDistance) continue;
        side = btcMinusPtb > 0 ? "up" : "down";
        if (params.useOdds) {
          const odds = side === "up" ? (upMid != null ? upMid : upAsk) : (downMid != null ? downMid : downAsk);
          if (!oddsHit(odds, params.hitOdds)) continue;
        }
      } else if (params.useOdds) {
        side = pickOddsSide(upMid, upAsk, downMid, downAsk, params.hitOdds);
        if (!side) continue;
      } else {
        continue;
      }

      if (params.useTwap) {
        if (twapMinusPtb == null) continue;
        if (side === "up" ? !(twapMinusPtb > 0) : !(twapMinusPtb < 0)) continue;
      }
      if (params.useVolume) {
        if (volume == null || volume < params.minVolume) continue;
      }
      if (params.useVenues) {
        const venues = side === "up" ? venuesUp : venuesDown;
        if (venues == null || venues < params.minVenues) continue;
      }

      const fillPrice = params.fillMode === "mid"
        ? (side === "up" ? upMid : downMid)
        : (side === "up" ? upAsk : downAsk);
      if (fillPrice == null || fillPrice <= 0 || fillPrice >= 1) continue;

      return { t, side, fillPrice, btcMinusPtb, twapMinusPtb, upMid, volume };
    }
    return null;
  }

  function evaluate(windows, params) {
    const trades = [];
    const equity = [];
    let eq = 0;
    let wins = 0;
    let losses = 0;
    let noTrade = 0;
    let feesPaid = 0;
    let peak = 0;
    let maxDrawdown = 0;

    for (let i = 0; i < windows.length; i++) {
      const win = windows[i];
      const entry = findEntry(win, params);
      if (!entry) {
        noTrade++;
        continue;
      }
      const shares = params.stake / entry.fillPrice;
      const fee = takerFee(shares, entry.fillPrice, params.feeRate);
      const won = entry.side === win.outcome;
      const payout = won ? shares : 0;
      const pnl = payout - params.stake - fee;
      eq += pnl;
      feesPaid += fee;
      if (won) wins++;
      else losses++;
      trades.push({
        id: win.id,
        slug: win.slug,
        start: win.start,
        t: win.end,
        side: entry.side,
        fill: entry.fillPrice,
        shares,
        fee,
        outcome: win.outcome,
        pnl,
        elapsed: entry.t,
        upMid: entry.upMid,
        btcMinusPtb: entry.btcMinusPtb,
        twapMinusPtb: entry.twapMinusPtb,
        volume: entry.volume,
        status: won ? "win" : "loss",
      });
      equity.push({ t: win.end, equity: eq, pnl });
      if (eq > peak) peak = eq;
      const dd = peak - eq;
      if (dd > maxDrawdown) maxDrawdown = dd;
    }

    const totalTrades = wins + losses;
    return {
      trades,
      equity,
      summary: {
        windows: windows.length,
        trades: totalTrades,
        wins,
        losses,
        noTrade,
        winRate: totalTrades ? wins / totalTrades : null,
        netPnl: eq,
        avgPnl: totalTrades ? eq / totalTrades : null,
        feesPaid,
        maxDrawdown,
      },
    };
  }

  function sweep(windows, baseParams, key, values) {
    return values.map((value) => {
      const params = Object.assign({}, baseParams, { [key]: value });
      const { summary } = evaluate(windows, params);
      return { value, netPnl: summary.netPnl, winRate: summary.winRate, trades: summary.trades };
    });
  }

  const LabEngine = { ROW, evaluate, sweep, findEntry, takerFee, oddsHit, pickOddsSide };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = LabEngine;
  } else {
    global.LabEngine = LabEngine;
  }
})(typeof self !== "undefined" ? self : this);
