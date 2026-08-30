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

  function clampBand(lo, hi) {
    lo = Math.min(0.99, Math.max(0.01, Number(lo)));
    hi = Math.min(0.99, Math.max(0.01, Number(hi)));
    if (!(lo <= hi)) {
      const tmp = lo;
      lo = hi;
      hi = tmp;
    }
    return [lo, hi];
  }

  function inBand(value, lo, hi) {
    return value != null && value >= lo && value <= hi;
  }

  function enteredBand(prev, curr, lo, hi) {
    if (curr == null || prev == null) return false;
    if (lo < hi) return !inBand(prev, lo, hi) && inBand(curr, lo, hi);
    return (prev < lo && curr >= lo) || (prev > lo && curr <= lo);
  }

  function pickEnteredSide(prevUp, up, prevDown, down, lo, hi) {
    const upIn = enteredBand(prevUp, up, lo, hi);
    const downIn = enteredBand(prevDown, down, lo, hi);
    if (upIn && downIn) {
      const mid = (lo + hi) / 2;
      return Math.abs((up ?? mid) - mid) <= Math.abs((down ?? mid) - mid) ? "up" : "down";
    }
    if (upIn) return "up";
    if (downIn) return "down";
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
    let fromIdx = 0;
    let toIdx = duration;
    if (params.useLastMinutes) {
      if (params.elapsedFromMin != null || params.elapsedToMin != null) {
        fromIdx = Math.max(0, Math.round(Number(params.elapsedFromMin ?? 0) * 60));
        toIdx = Math.min(duration, Math.round(Number(params.elapsedToMin ?? duration / 60) * 60));
      } else if (params.lastMinutes != null) {
        fromIdx = Math.max(0, Math.round(duration - params.lastMinutes * 60));
      }
      if (fromIdx > toIdx) {
        const tmp = fromIdx;
        fromIdx = toIdx;
        toIdx = tmp;
      }
    }

    const [lo, hi] = clampBand(
      params.oddsLo != null ? params.oddsLo : params.hitOdds != null ? params.hitOdds : 0.2,
      params.oddsHi != null ? params.oddsHi : params.hitOdds != null ? params.hitOdds : 0.3,
    );
    let prevUp = null;
    let prevDown = null;

    function remember(up, down) {
      if (up != null) prevUp = up;
      if (down != null) prevDown = down;
    }

    for (let t = fromIdx; t <= toIdx; t++) {
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
      const up = upMid != null ? upMid : upAsk;
      const down = downMid != null ? downMid : downAsk;

      let side = null;
      if (params.useSpot) {
        const absDist = btcMinusPtb == null ? null : Math.abs(btcMinusPtb);
        const maxDist = params.maxDistance;
        if (
          absDist == null
          || absDist < params.minDistance
          || (maxDist != null && absDist > maxDist)
        ) {
          remember(up, down);
          continue;
        }
        side = btcMinusPtb > 0 ? "up" : "down";
        if (params.useOdds) {
          const curr = side === "up" ? up : down;
          const prev = side === "up" ? prevUp : prevDown;
          if (!enteredBand(prev, curr, lo, hi)) {
            remember(up, down);
            continue;
          }
        }
      } else if (params.useOdds) {
        side = pickEnteredSide(prevUp, up, prevDown, down, lo, hi);
        if (!side) {
          remember(up, down);
          continue;
        }
      } else {
        continue;
      }

      if (params.useTwap) {
        if (twapMinusPtb == null) {
          remember(up, down);
          continue;
        }
        if (side === "up" ? !(twapMinusPtb > 0) : !(twapMinusPtb < 0)) {
          remember(up, down);
          continue;
        }
      }
      if (params.useVolume) {
        if (volume == null || volume < params.minVolume) {
          remember(up, down);
          continue;
        }
      }
      if (params.useVenues) {
        const venues = side === "up" ? venuesUp : venuesDown;
        if (venues == null || venues < params.minVenues) {
          remember(up, down);
          continue;
        }
      }

      const fillPrice = params.fillMode === "mid"
        ? (side === "up" ? upMid : downMid)
        : (side === "up" ? upAsk : downAsk);
      if (fillPrice == null || fillPrice <= 0 || fillPrice >= 1) {
        remember(up, down);
        continue;
      }

      return { t, side, fillPrice, btcMinusPtb, twapMinusPtb, upMid, volume };
    }
    return null;
  }

  function localDayKey(ts) {
    const d = new Date(Number(ts) * 1000);
    if (!Number.isFinite(d.getTime())) return null;
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function median(values) {
    if (!values.length) return null;
    const sorted = values.slice().sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    if (sorted.length % 2) return sorted[mid];
    return (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function evaluate(windows, params) {
    const trades = [];
    const equity = [];
    const byDay = new Map();
    let eq = 0;
    let wins = 0;
    let losses = 0;
    let noTrade = 0;
    let feesPaid = 0;
    let peak = 0;
    let maxDrawdown = 0;
    let grossWin = 0;
    let grossLoss = 0;
    let fillSum = 0;
    let upTrades = 0;
    let downTrades = 0;
    let upWins = 0;
    let downWins = 0;
    let streak = 0;
    let maxWinStreak = 0;
    let maxLossStreak = 0;

    function touchDay(ts) {
      const key = localDayKey(ts);
      if (!key) return null;
      if (!byDay.has(key)) byDay.set(key, { pnl: 0, trades: 0 });
      return key;
    }

    for (let i = 0; i < windows.length; i++) {
      const win = windows[i];
      touchDay(win.start);
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
      fillSum += entry.fillPrice;
      if (entry.side === "up") {
        upTrades++;
        if (won) upWins++;
      } else {
        downTrades++;
        if (won) downWins++;
      }
      if (won) {
        wins++;
        grossWin += pnl;
        streak = streak > 0 ? streak + 1 : 1;
        if (streak > maxWinStreak) maxWinStreak = streak;
      } else {
        losses++;
        grossLoss += -pnl;
        streak = streak < 0 ? streak - 1 : -1;
        if (-streak > maxLossStreak) maxLossStreak = -streak;
      }
      const dayKey = touchDay(win.start);
      if (dayKey) {
        const day = byDay.get(dayKey);
        day.pnl += pnl;
        day.trades += 1;
      }
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
    const days = byDay.size;
    const dayPnls = [];
    let bestDay = null;
    let worstDay = null;
    let winningDays = 0;
    let losingDays = 0;
    byDay.forEach((day, key) => {
      dayPnls.push(day.pnl);
      if (bestDay == null || day.pnl > bestDay.pnl) bestDay = { day: key, pnl: day.pnl, trades: day.trades };
      if (worstDay == null || day.pnl < worstDay.pnl) worstDay = { day: key, pnl: day.pnl, trades: day.trades };
      if (day.pnl > 0) winningDays++;
      else if (day.pnl < 0) losingDays++;
    });
    const avgWin = wins ? grossWin / wins : null;
    const avgLoss = losses ? grossLoss / losses : null;

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
        days,
        tradesPerDay: days ? totalTrades / days : null,
        avgDayPnl: days ? eq / days : null,
        medianDayPnl: median(dayPnls),
        bestDay,
        worstDay,
        winningDays,
        losingDays,
        profitFactor: grossLoss > 0 ? grossWin / grossLoss : (grossWin > 0 ? Infinity : null),
        avgWin,
        avgLoss,
        payoff: avgWin != null && avgLoss ? avgWin / avgLoss : null,
        avgFill: totalTrades ? fillSum / totalTrades : null,
        participation: windows.length ? totalTrades / windows.length : null,
        maxWinStreak,
        maxLossStreak,
        upTrades,
        downTrades,
        upWinRate: upTrades ? upWins / upTrades : null,
        downWinRate: downTrades ? downWins / downTrades : null,
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

  const LabEngine = { ROW, evaluate, sweep, findEntry, takerFee, enteredBand, pickEnteredSide, clampBand };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = LabEngine;
  } else {
    global.LabEngine = LabEngine;
  }
})(typeof self !== "undefined" ? self : this);
