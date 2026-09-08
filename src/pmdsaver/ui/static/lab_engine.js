/* Strategy Lab evaluation engine — pure functions, no DOM.
 * Loaded both on the main thread (<script>) and inside lab_worker.js
 * (importScripts), so it must stay dependency-free.
 *
 * Row layout (see tape.py): [up_ask, down_ask, up_mid, down_mid,
 * btc_minus_ptb (median of 3 spot venues), twap_minus_ptb, volume,
 * venues_up, venues_down, binance_minus_ptb, coinbase_minus_ptb,
 * bybit_minus_ptb]
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
    BINANCE_MINUS_PTB: 9,
    COINBASE_MINUS_PTB: 10,
    BYBIT_MINUS_PTB: 11,
  };

  const BTC_SOURCE_COLUMN = {
    binance_spot: ROW.BINANCE_MINUS_PTB,
    coinbase_spot: ROW.COINBASE_MINUS_PTB,
    bybit_spot: ROW.BYBIT_MINUS_PTB,
    median: ROW.BTC_MINUS_PTB,
  };
  const DEFAULT_BTC_SOURCE = "binance_spot";

  function btcSourceColumn(source) {
    const col = BTC_SOURCE_COLUMN[source];
    return col == null ? BTC_SOURCE_COLUMN[DEFAULT_BTC_SOURCE] : col;
  }

  // Rows written by older tapes are shorter than 12 entries; treat a missing
  // column as "no price" instead of throwing.
  function cell(row, idx) {
    const value = row[idx];
    return value === undefined ? null : value;
  }

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
    const btcCol = btcSourceColumn(params.btcSource);
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
      const btcMinusPtb = cell(row, btcCol);
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

  function emptyBucket() {
    return { pnl: 0, trades: 0, wins: 0 };
  }

  function addBucket(map, key, pnl, won) {
    let bucket = map.get(key);
    if (!bucket) {
      bucket = emptyBucket();
      map.set(key, bucket);
    }
    bucket.pnl += pnl;
    bucket.trades += 1;
    if (won) bucket.wins += 1;
  }

  function finalizeBuckets(map, keys, labels) {
    let maxAbs = 0;
    for (let i = 0; i < keys.length; i++) {
      const bucket = map.get(keys[i]);
      if (bucket && Math.abs(bucket.pnl) > maxAbs) maxAbs = Math.abs(bucket.pnl);
    }
    if (maxAbs <= 0) maxAbs = 1;
    return keys.map((key, i) => {
      const bucket = map.get(key) || emptyBucket();
      return {
        key,
        label: labels[i],
        pnl: bucket.pnl,
        trades: bucket.trades,
        wins: bucket.wins,
        winRate: bucket.trades ? bucket.wins / bucket.trades : null,
        frac: bucket.pnl / maxAbs,
      };
    });
  }

  function median(values) {
    if (!values.length) return null;
    const sorted = values.slice().sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    if (sorted.length % 2) return sorted[mid];
    return (sorted[mid - 1] + sorted[mid]) / 2;
  }

  // Cash-market windows in UTC, widened so DST does not drop the open.
  // Tokyo has no DST. London open covers GMT and BST. Wall St open covers
  // EST and EDT first hours.
  const SESSIONS = [
    { key: "tokyo_open", label: "Tokyo open", short: "Tokyo", start: 0, end: 150 },
    { key: "london_open", label: "London open", short: "Lon open", start: 7 * 60, end: 10 * 60 },
    { key: "wall_open", label: "Wall St open", short: "NY open", start: 13 * 60 + 30, end: 16 * 60 + 30 },
    { key: "asia", label: "Asia", short: "Asia", start: 0, end: 8 * 60 },
    { key: "london", label: "London", short: "London", start: 7 * 60, end: 16 * 60 + 30 },
    { key: "wall", label: "Wall Street", short: "Wall St", start: 13 * 60, end: 21 * 60 },
    { key: "overlap", label: "London–NY", short: "Overlap", start: 13 * 60, end: 16 * 60 + 30 },
    { key: "off", label: "Off hours", short: "Off", start: 21 * 60, end: 0 },
  ];

  function clockUtc(mins) {
    const wrapped = ((mins % 1440) + 1440) % 1440;
    const h = Math.floor(wrapped / 60);
    const m = wrapped % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }

  function inSessionRange(mins, start, end) {
    if (start === end) return false;
    if (start < end) return mins >= start && mins < end;
    return mins >= start || mins < end;
  }

  function utcMinutesOf(date) {
    return date.getUTCHours() * 60 + date.getUTCMinutes();
  }

  function sessionsFor(date) {
    const mins = utcMinutesOf(date);
    const keys = [];
    for (let i = 0; i < SESSIONS.length; i++) {
      const session = SESSIONS[i];
      if (inSessionRange(mins, session.start, session.end)) keys.push(session.key);
    }
    return keys;
  }

  function asKeyList(value, numeric) {
    if (value == null) return null;
    if (!Array.isArray(value) || !value.length) return null;
    const out = [];
    const seen = new Set();
    for (let i = 0; i < value.length; i++) {
      const key = numeric ? Number(value[i]) : String(value[i]);
      if (numeric && !Number.isFinite(key)) continue;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(key);
    }
    return out.length ? out : null;
  }

  function listHas(list, key) {
    if (!list) return true;
    for (let i = 0; i < list.length; i++) {
      if (list[i] === key) return true;
    }
    return false;
  }

  function passesWhen(date, params, ignore) {
    if (!date || !Number.isFinite(date.getTime())) return false;
    const hours = ignore === "hours" ? null : asKeyList(params && params.hours, true);
    const weekdays = ignore === "weekdays" ? null : asKeyList(params && params.weekdays, true);
    const sessions = ignore === "sessions" ? null : asKeyList(params && params.sessions, false);
    if (hours && !listHas(hours, date.getHours())) return false;
    if (weekdays && !listHas(weekdays, date.getDay())) return false;
    if (sessions) {
      const hit = sessionsFor(date);
      let ok = false;
      for (let i = 0; i < sessions.length; i++) {
        if (listHas(hit, sessions[i])) {
          ok = true;
          break;
        }
      }
      if (!ok) return false;
    }
    return true;
  }

  function evaluate(windows, params) {
    const trades = [];
    const equity = [];
    const byDay = new Map();
    const byHour = new Map();
    const byWeekday = new Map();
    const bySession = new Map();
    let considered = 0;
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
      const when = new Date(Number(win.start) * 1000);
      const whenOk = Number.isFinite(when.getTime());
      const pass = whenOk && passesWhen(when, params);
      if (pass) {
        considered++;
        touchDay(win.start);
      }
      const entry = findEntry(win, params);
      if (!entry) {
        if (pass) noTrade++;
        continue;
      }
      const shares = params.stake / entry.fillPrice;
      const fee = takerFee(shares, entry.fillPrice, params.feeRate);
      const won = entry.side === win.outcome;
      const payout = won ? shares : 0;
      const pnl = payout - params.stake - fee;
      if (whenOk) {
        if (passesWhen(when, params, "hours")) {
          addBucket(byHour, when.getHours(), pnl, won);
        }
        if (passesWhen(when, params, "weekdays")) {
          addBucket(byWeekday, when.getDay(), pnl, won);
        }
        if (passesWhen(when, params, "sessions")) {
          const hit = sessionsFor(when);
          for (let s = 0; s < hit.length; s++) addBucket(bySession, hit[s], pnl, won);
        }
      }
      if (!pass) continue;
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

    const hourKeys = [];
    const hourLabels = [];
    for (let h = 0; h < 24; h++) {
      hourKeys.push(h);
      hourLabels.push(String(h).padStart(2, "0"));
    }
    const weekdayOrder = [1, 2, 3, 4, 5, 6, 0];
    const weekdayLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const sessionKeys = SESSIONS.map((session) => session.key);
    const sessionLabels = SESSIONS.map((session) => session.short);

    return {
      trades,
      equity,
      byHour: finalizeBuckets(byHour, hourKeys, hourLabels),
      byWeekday: finalizeBuckets(byWeekday, weekdayOrder, weekdayLabels),
      bySession: finalizeBuckets(bySession, sessionKeys, sessionLabels),
      summary: {
        windows: considered,
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
        participation: considered ? totalTrades / considered : null,
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

  const LabEngine = {
    ROW,
    DEFAULT_BTC_SOURCE,
    SESSIONS,
    clockUtc,
    sessionsFor,
    passesWhen,
    btcSourceColumn,
    evaluate,
    sweep,
    findEntry,
    takerFee,
    enteredBand,
    pickEnteredSide,
    clampBand,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = LabEngine;
  } else {
    global.LabEngine = LabEngine;
  }
})(typeof self !== "undefined" ? self : this);
