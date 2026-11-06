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

  function windowBtcDelta(win, btcCol) {
    const rows = win && win.rows;
    if (!rows || !rows.length) return null;
    let first = null;
    let last = null;
    for (let t = 0; t < rows.length; t++) {
      const row = rows[t];
      if (!row) continue;
      const v = cell(row, btcCol);
      if (v != null) {
        if (first == null) first = v;
        last = v;
      }
    }
    if (first == null || last == null) return null;
    return last - first;
  }

  function windowSpreadMedian(win, col) {
    const rows = win && win.rows;
    if (!rows || !rows.length) return null;
    const diffs = [];
    for (let t = 0; t < rows.length; t++) {
      const row = rows[t];
      if (!row) continue;
      const v = cell(row, col);
      const tw = row[ROW.TWAP_MINUS_PTB];
      if (v != null && tw != null) diffs.push(v - tw);
    }
    return diffs.length >= 10 ? median(diffs) : null;
  }

  function buildBasisMap(windows) {
    const winBin = new Map();
    const winBy = new Map();
    for (let i = 0; i < windows.length; i++) {
      const w = windows[i];
      const b = windowSpreadMedian(w, ROW.BINANCE_MINUS_PTB);
      const y = windowSpreadMedian(w, ROW.BYBIT_MINUS_PTB);
      if (b != null) winBin.set(w.start, b);
      if (y != null) winBy.set(w.start, y);
    }
    const out = new Map();
    for (let i = 0; i < windows.length; i++) {
      const s = windows[i].start;
      const bs = [];
      const ys = [];
      for (let k = 1; k <= 12; k++) {
        const pb = winBin.get(s - 300 * k);
        const py = winBy.get(s - 300 * k);
        if (pb != null) bs.push(pb);
        if (py != null) ys.push(py);
      }
      if (bs.length >= 3 && ys.length >= 3) {
        out.set(s, {
          bin: median(bs),
          by: median(ys),
        });
      }
    }
    return out;
  }

  function normalizedBtc(row, btcCol, basis) {
    if (!basis) return cell(row, btcCol);
    if (btcCol === ROW.BINANCE_MINUS_PTB) {
      const v = cell(row, ROW.BINANCE_MINUS_PTB);
      return v == null ? null : v - basis.bin;
    }
    if (btcCol === ROW.BYBIT_MINUS_PTB) {
      const v = cell(row, ROW.BYBIT_MINUS_PTB);
      return v == null ? null : v - basis.by;
    }
    if (btcCol === ROW.BTC_MINUS_PTB) {
      const b = cell(row, ROW.BINANCE_MINUS_PTB);
      const c = cell(row, ROW.COINBASE_MINUS_PTB);
      const y = cell(row, ROW.BYBIT_MINUS_PTB);
      const vals = [];
      if (b != null) vals.push(b - basis.bin);
      if (c != null) vals.push(c);
      if (y != null) vals.push(y - basis.by);
      return vals.length ? median(vals) : null;
    }
    return cell(row, btcCol);
  }

  function normalizedVenuesCount(row, side, basis) {
    if (!basis) {
      return side === "up" ? row[ROW.VENUES_UP] : row[ROW.VENUES_DOWN];
    }
    const b = cell(row, ROW.BINANCE_MINUS_PTB);
    const c = cell(row, ROW.COINBASE_MINUS_PTB);
    const y = cell(row, ROW.BYBIT_MINUS_PTB);
    const bn = b == null ? null : b - basis.bin;
    const yn = y == null ? null : y - basis.by;
    const isUp = side === "up";
    let count = 0;
    if (bn != null && (isUp ? bn > 0 : bn < 0)) count++;
    if (c != null && (isUp ? c > 0 : c < 0)) count++;
    if (yn != null && (isUp ? yn > 0 : yn < 0)) count++;
    // When all 3 normalized spot venues agree, count normalized futures as the 4th venue.
    if (count === 3) count = 4;
    return count;
  }

  function findEntry(win, params, prevDelta, basis) {
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
      const btcMinusPtb = normalizedBtc(row, btcCol, basis);
      const twapMinusPtb = row[ROW.TWAP_MINUS_PTB];
      const volume = row[ROW.VOLUME];
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
        const venues = normalizedVenuesCount(row, side, basis);
        if (venues == null || venues < params.minVenues) {
          remember(up, down);
          continue;
        }
      }
      if (params.useFreshTurn) {
        if (prevDelta == null || (side === "up" ? prevDelta > 0 : prevDelta < 0)) {
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

    const btcCol = btcSourceColumn(params.btcSource);
    const byStart = params.useFreshTurn ? new Map(windows.map((w) => [w.start, w])) : null;
    const basisMap = params.normalizeBasis ? buildBasisMap(windows) : null;

    for (let i = 0; i < windows.length; i++) {
      const win = windows[i];
      const when = new Date(Number(win.start) * 1000);
      const whenOk = Number.isFinite(when.getTime());
      const pass = whenOk && passesWhen(when, params);
      if (pass) {
        considered++;
        touchDay(win.start);
      }
      const prevWin = byStart ? byStart.get(win.start - 300) : null;
      const prevDelta = prevWin ? windowBtcDelta(prevWin, btcCol) : null;
      const basis = basisMap ? basisMap.get(win.start) : null;
      const entry = findEntry(win, params, prevDelta, basis);
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

  /* ------------------------------------------------------------------ *
   * Grid search ("Find best settings")
   *
   * fastScoreOther reproduces evaluate()'s netPnl / trades / wins exactly,
   * bit for bit: the same entry rule as findEntry, the same per-trade PnL
   * expression, and the same left-to-right summation over windows. It is
   * faster because:
   *  - each window is reduced once to the seconds that could ever be an
   *    entry under the fixed toggles ("events");
   *  - one pass over a window's events answers every elapsed from/to pair
   *    (the elapsed band only picks which qualifying second comes first);
   *  - the When filter, Date objects and per-trade bookkeeping run once,
   *    not once per combination.
   * ------------------------------------------------------------------ */

  const ELAPSED_KEYS = ["elapsedFromMin", "elapsedToMin"];

  function buildCandidates(dim) {
    const values = [];
    const steps = Math.max(1, Math.round((dim.max - dim.min) / dim.step));
    for (let i = 0; i <= steps; i++) {
      values.push(Math.round((dim.min + i * dim.step) * 1000) / 1000);
    }
    return values;
  }

  function elapsedInvalid(p) {
    return p.elapsedFromMin != null && Number(p.elapsedFromMin) > Number(p.elapsedToMin);
  }

  function otherInvalid(p) {
    return (p.oddsLo != null && Number(p.oddsLo) > Number(p.oddsHi))
      || (p.minDistance != null && Number(p.minDistance) > Number(p.maxDistance));
  }

  function comboInvalid(p) {
    return elapsedInvalid(p) || otherInvalid(p);
  }

  /** Enumerate the grid as (elapsed pair) x (other filters). Deterministic. */
  function searchPlan(baseParams, dims) {
    const keys = Object.keys(dims || {});
    const grids = keys.map((k) => buildCandidates(dims[k]));
    const total = grids.reduce((acc, g) => acc * g.length, 1);
    const strides = new Array(keys.length);
    let stride = 1;
    for (let i = keys.length - 1; i >= 0; i--) {
      strides[i] = stride;
      stride *= grids[i].length;
    }

    const iFrom = keys.indexOf("elapsedFromMin");
    const iTo = keys.indexOf("elapsedToMin");
    const fromVals = iFrom >= 0 ? grids[iFrom] : [baseParams.elapsedFromMin];
    const toVals = iTo >= 0 ? grids[iTo] : [baseParams.elapsedToMin];
    const pairs = [];
    for (let a = 0; a < fromVals.length; a++) {
      for (let b = 0; b < toVals.length; b++) {
        const probe = { elapsedFromMin: fromVals[a], elapsedToMin: toVals[b] };
        if (elapsedInvalid(probe)) continue;
        pairs.push({
          values: iFrom >= 0 || iTo >= 0
            ? Object.assign({}, iFrom >= 0 ? { elapsedFromMin: fromVals[a] } : null, iTo >= 0 ? { elapsedToMin: toVals[b] } : null)
            : {},
          from: fromVals[a],
          to: toVals[b],
          idx: (iFrom >= 0 ? a * strides[iFrom] : 0) + (iTo >= 0 ? b * strides[iTo] : 0),
        });
      }
    }

    const otherPos = [];
    for (let i = 0; i < keys.length; i++) {
      if (!ELAPSED_KEYS.includes(keys[i])) otherPos.push(i);
    }
    const counts = otherPos.map((i) => grids[i].length);
    const nOther = counts.reduce((acc, c) => acc * c, 1);
    const others = [];
    for (let o = 0; o < nOther; o++) {
      let rem = o;
      let idx = 0;
      const values = {};
      for (let j = otherPos.length - 1; j >= 0; j--) {
        const c = counts[j];
        const ii = rem % c;
        rem = (rem - ii) / c;
        values[keys[otherPos[j]]] = grids[otherPos[j]][ii];
        idx += ii * strides[otherPos[j]];
      }
      if (otherInvalid(Object.assign({}, baseParams, values))) continue;
      others.push({ values, idx });
    }

    const useLast = !!baseParams.useLastMinutes;
    const fIdxOf = pairs.map((p) => (useLast ? Math.max(0, Math.round(Number(p.from ?? 0) * 60)) : 0));
    const tRaw = pairs.map((p) => (useLast ? Math.round(Number(p.to) * 60) : Infinity));
    const F = Array.from(new Set(fIdxOf)).sort((x, y) => x - y);
    const pairK = fIdxOf.map((f) => F.indexOf(f));
    const byK = F.map(() => []);
    pairK.forEach((k, pi) => byK[k].push(pi));
    byK.forEach((list) => list.sort((x, y) => tRaw[x] - tRaw[y]));

    return {
      keys,
      grids,
      total,
      pairs,
      others,
      valid: pairs.length * others.length,
      fIdx: fIdxOf,
      tRaw,
      F,
      maxF: F.length ? F[F.length - 1] : 0,
      byK,
    };
  }

  function fastSearchSupported(params, plan) {
    if (!params.useSpot) return false;
    if (params.fillMode !== "ask" && params.fillMode !== "mid") return false;
    if (params.useLastMinutes && plan.pairs.some((p) => p.from == null || p.to == null)) return false;
    return true;
  }

  function comboParams(baseParams, plan, oi, pi) {
    return Object.assign({}, baseParams, plan.pairs[pi].values, plan.others[oi].values);
  }

  /** Reduce the tape to candidate entry seconds, in window order. */
  function prepareSearch(windows, params, plan) {
    const btcCol = btcSourceColumn(params.btcSource);
    const useOdds = !!params.useOdds;
    const useTwap = !!params.useTwap;
    const useVolume = !!params.useVolume;
    const useVenues = !!params.useVenues;
    const useFreshTurn = !!params.useFreshTurn;
    const normalizeBasis = !!params.normalizeBasis;
    const fillMid = params.fillMode === "mid";
    const byStart = useFreshTurn ? new Map(windows.map((w) => [w.start, w])) : null;
    const basisMap = normalizeBasis ? buildBasisMap(windows) : null;

    // Loosest threshold over the whole grid: a second failing it fails every combo.
    let minDistFloor = Infinity;
    let maxDistCeil = -Infinity;
    let noDistCap = false;
    let minVolFloor = Infinity;
    let minVenFloor = Infinity;
    for (const o of plan.others) {
      const p = Object.assign({}, params, o.values);
      minDistFloor = Math.min(minDistFloor, Number(p.minDistance));
      if (p.maxDistance == null) noDistCap = true;
      else maxDistCeil = Math.max(maxDistCeil, Number(p.maxDistance));
      minVolFloor = Math.min(minVolFloor, Number(p.minVolume));
      minVenFloor = Math.min(minVenFloor, Number(p.minVenues));
    }
    if (!Number.isFinite(minDistFloor)) minDistFloor = -Infinity;
    if (noDistCap || !Number.isFinite(maxDistCeil)) maxDistCeil = Infinity;
    if (!Number.isFinite(minVolFloor)) minVolFloor = -Infinity;
    if (!Number.isFinite(minVenFloor)) minVenFloor = -Infinity;

    const offsets = [0];
    const durations = [];
    const evT = [];
    const evKey = [];
    const evPrev = [];
    const evCurr = [];
    const evAbs = [];
    const evVol = [];
    const evVen = [];
    const evPnl = [];
    const evWon = [];

    for (let i = 0; i < windows.length; i++) {
      const win = windows[i];
      const when = new Date(Number(win.start) * 1000);
      if (!Number.isFinite(when.getTime()) || !passesWhen(when, params)) continue;
      const rows = win.rows;
      if (!rows || !rows.length) continue;
      const prevWin = byStart ? byStart.get(win.start - 300) : null;
      const prevDelta = prevWin ? windowBtcDelta(prevWin, btcCol) : null;
      if (useFreshTurn && prevDelta == null) continue;
      const basis = basisMap ? basisMap.get(win.start) : null;
      const duration = win.end - win.start;
      const before = evT.length;
      let lastUp = -1;
      let lastDown = -1;
      let lastUpV = null;
      let lastDownV = null;
      // All stored rows: findEntry's from/to swap can reach past `duration`.
      for (let t = 0; t < rows.length; t++) {
        const row = rows[t];
        if (!row) continue;
        const upAsk = row[ROW.UP_ASK];
        const downAsk = row[ROW.DOWN_ASK];
        const upMid = row[ROW.UP_MID];
        const downMid = row[ROW.DOWN_MID];
        const up = upMid != null ? upMid : upAsk;
        const down = downMid != null ? downMid : downAsk;
        const btc = normalizedBtc(row, btcCol, basis);
        emit: if (btc != null) {
          const isUp = btc > 0;
          const absDist = Math.abs(btc);
          if (absDist < minDistFloor || absDist > maxDistCeil) break emit;
          const curr = isUp ? up : down;
          const pIdx = isUp ? lastUp : lastDown;
          const prev = isUp ? lastUpV : lastDownV;
          if (useOdds && (curr == null || pIdx < 0 || prev === curr)) break emit;
          if (useTwap) {
            const twap = row[ROW.TWAP_MINUS_PTB];
            if (twap == null || (isUp ? !(twap > 0) : !(twap < 0))) break emit;
          }
          const volume = row[ROW.VOLUME];
          if (useVolume && (volume == null || volume < minVolFloor)) break emit;
          const venues = normalizedVenuesCount(row, isUp ? "up" : "down", basis);
          if (useVenues && (venues == null || venues < minVenFloor)) break emit;
          if (useFreshTurn && (isUp ? prevDelta > 0 : prevDelta < 0)) break emit;
          const fill = fillMid ? (isUp ? upMid : downMid) : (isUp ? upAsk : downAsk);
          if (fill == null || fill <= 0 || fill >= 1) break emit;
          const shares = params.stake / fill;
          const fee = takerFee(shares, fill, params.feeRate);
          const won = (isUp ? "up" : "down") === win.outcome;
          const payout = won ? shares : 0;
          evT.push(t);
          evKey.push(useOdds ? pIdx : t);
          evPrev.push(useOdds ? prev : 0);
          evCurr.push(useOdds ? curr : 0);
          evAbs.push(absDist);
          evVol.push(useVolume ? volume : 0);
          evVen.push(useVenues ? venues : 0);
          evPnl.push(payout - params.stake - fee);
          evWon.push(won ? 1 : 0);
        }
        if (up != null) {
          lastUp = t;
          lastUpV = up;
        }
        if (down != null) {
          lastDown = t;
          lastDownV = down;
        }
      }
      if (evT.length > before) {
        durations.push(duration);
        offsets.push(evT.length);
      }
    }

    return {
      useOdds,
      useVolume,
      useVenues,
      offsets: Int32Array.from(offsets),
      durations: Int32Array.from(durations),
      evT: Int32Array.from(evT),
      evKey: Int32Array.from(evKey),
      evPrev: Float64Array.from(evPrev),
      evCurr: Float64Array.from(evCurr),
      evAbs: Float64Array.from(evAbs),
      evVol: Float64Array.from(evVol),
      evVen: Float64Array.from(evVen),
      evPnl: Float64Array.from(evPnl),
      evWon: Uint8Array.from(evWon),
    };
  }

  function prepTransferables(prep) {
    return [
      prep.offsets.buffer, prep.durations.buffer, prep.evT.buffer, prep.evKey.buffer,
      prep.evPrev.buffer, prep.evCurr.buffer, prep.evAbs.buffer, prep.evVol.buffer,
      prep.evVen.buffer, prep.evPnl.buffer, prep.evWon.buffer,
    ];
  }

  function newScoreBuffers(plan) {
    const P = plan.pairs.length;
    const K = plan.F.length;
    return {
      pnl: new Float64Array(P),
      trades: new Int32Array(P),
      wins: new Int32Array(P),
      firstT: new Int32Array(K),
      firstPnl: new Float64Array(K),
      firstWon: new Uint8Array(K),
    };
  }

  /** netPnl / trades / wins for other-combo `oi` under every elapsed pair. */
  function fastScoreOther(prep, plan, baseParams, oi, buf) {
    const params = Object.assign({}, baseParams, plan.others[oi].values);
    const [lo, hi] = clampBand(
      params.oddsLo != null ? params.oddsLo : params.hitOdds != null ? params.hitOdds : 0.2,
      params.oddsHi != null ? params.oddsHi : params.hitOdds != null ? params.hitOdds : 0.3,
    );
    const minD = params.minDistance;
    const maxD = params.maxDistance;
    const minVol = params.minVolume;
    const minVen = params.minVenues;
    const { useOdds, useVolume, useVenues, offsets, durations, evT, evKey, evPrev, evCurr, evAbs, evVol, evVen, evPnl, evWon } = prep;
    const { F, maxF, byK, tRaw, fIdx } = plan;
    const K = F.length;
    const P = plan.pairs.length;
    const { pnl, trades, wins, firstT, firstPnl, firstWon } = buf;
    pnl.fill(0);
    trades.fill(0);
    wins.fill(0);
    const nWin = durations.length;

    for (let w = 0; w < nWin; w++) {
      const a = offsets[w];
      const b = offsets[w + 1];
      const dur = durations[w];
      if (dur >= maxF) {
        let m = 0;
        for (let e = a; e < b; e++) {
          const key = evKey[e];
          if (key < F[m]) continue;
          const d = evAbs[e];
          if (d < minD || (maxD != null && d > maxD)) continue;
          if (useOdds && !enteredBand(evPrev[e], evCurr[e], lo, hi)) continue;
          if (useVolume && evVol[e] < minVol) continue;
          if (useVenues && evVen[e] < minVen) continue;
          do {
            firstT[m] = evT[e];
            firstPnl[m] = evPnl[e];
            firstWon[m] = evWon[e];
            m++;
          } while (m < K && F[m] <= key);
          if (m === K) break;
        }
        for (let k = 0; k < m; k++) {
          const ft = firstT[k];
          const list = byK[k];
          for (let j = list.length - 1; j >= 0; j--) {
            const pi = list[j];
            if (Math.min(dur, tRaw[pi]) < ft) break;
            pnl[pi] += firstPnl[k];
            trades[pi]++;
            if (firstWon[k]) wins[pi]++;
          }
        }
      } else {
        for (let pi = 0; pi < P; pi++) {
          let f = fIdx[pi];
          let to = Math.min(dur, tRaw[pi]);
          if (f > to) {
            const tmp = f;
            f = to;
            to = tmp;
          }
          for (let e = a; e < b; e++) {
            if (evKey[e] < f) continue;
            const d = evAbs[e];
            if (d < minD || (maxD != null && d > maxD)) continue;
            if (useOdds && !enteredBand(evPrev[e], evCurr[e], lo, hi)) continue;
            if (useVolume && evVol[e] < minVol) continue;
            if (useVenues && evVen[e] < minVen) continue;
            if (evT[e] <= to) {
              pnl[pi] += evPnl[e];
              trades[pi]++;
              if (evWon[e]) wins[pi]++;
            }
            break;
          }
        }
      }
    }
    return buf;
  }

  function betterThan(a, b) {
    return a.netPnl > b.netPnl || (a.netPnl === b.netPnl && a.idx < b.idx);
  }

  function pushTop(top, item, limit) {
    if (top.length >= limit && !betterThan(item, top[top.length - 1])) return;
    let i = top.length;
    while (i > 0 && betterThan(item, top[i - 1])) i--;
    top.splice(i, 0, item);
    if (top.length > limit) top.pop();
  }

  /** Score other-combos start, start+step, ... and keep the best `limit`. */
  function fastSearchChunk(prep, plan, baseParams, opts) {
    const { start = 0, step = 1, minTrades = 0, limit = 10, onProgress } = opts || {};
    const buf = newScoreBuffers(plan);
    const top = [];
    const P = plan.pairs.length;
    let done = 0;
    let lastReport = 0;
    for (let oi = start; oi < plan.others.length; oi += step) {
      fastScoreOther(prep, plan, baseParams, oi, buf);
      const baseIdx = plan.others[oi].idx;
      for (let pi = 0; pi < P; pi++) {
        if (buf.trades[pi] < minTrades) continue;
        pushTop(top, {
          netPnl: buf.pnl[pi],
          trades: buf.trades[pi],
          wins: buf.wins[pi],
          idx: baseIdx + plan.pairs[pi].idx,
          oi,
          pi,
        }, limit);
      }
      done += P;
      if (onProgress) {
        const now = Date.now();
        if (now - lastReport > 200) {
          lastReport = now;
          onProgress(done);
        }
      }
    }
    return { top, done };
  }

  function mergeTop(lists, limit) {
    const top = [];
    for (const list of lists) {
      for (const item of list) pushTop(top, item, limit);
    }
    return top;
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
    buildCandidates,
    comboInvalid,
    searchPlan,
    fastSearchSupported,
    comboParams,
    prepareSearch,
    prepTransferables,
    newScoreBuffers,
    fastScoreOther,
    fastSearchChunk,
    mergeTop,
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
