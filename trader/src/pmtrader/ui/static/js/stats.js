/** Aggregate stats from real fills only. */

import { tradePnl, effectiveWon, isStatClosed } from "./parse.js?v=10";

export function computeSummary(trades, filterFn = () => true) {
  let wins = 0;
  let losses = 0;
  let open = 0;
  let net = 0;
  let resolved = 0;
  let lossStreak = 0;
  let maxLossStreak = 0;
  let winStreak = 0;
  let maxWinStreak = 0;
  let totalTrades = 0;
  let todayNet = 0;
  let todayWins = 0;
  let todayLosses = 0;
  const todayUtcKey = utcDateKeyNow();

  for (const t of trades) {
    if (!filterFn(t) || t.rejected) continue;
    totalTrades++;
    if (!isStatClosed(t)) {
      open++;
      continue;
    }
    resolved++;
    const pnl = tradePnl(t) ?? 0;
    net += pnl;
    const won = effectiveWon(t);

    if (utcDateKeyFromTs(t.windowEnd || t.entryTs) === todayUtcKey) {
      todayNet += pnl;
      if (won) todayWins++;
      else todayLosses++;
    }

    if (won) {
      wins++;
      winStreak++;
      lossStreak = 0;
      maxWinStreak = Math.max(maxWinStreak, winStreak);
    } else {
      losses++;
      lossStreak++;
      winStreak = 0;
      maxLossStreak = Math.max(maxLossStreak, lossStreak);
    }
  }

  const winRate = wins + losses > 0 ? (wins / (wins + losses)) * 100 : null;

  return {
    totalTrades,
    resolved,
    open,
    wins,
    losses,
    winRate,
    netPnl: net,
    todayNet,
    todayWins,
    todayLosses,
    maxWinStreak,
    maxLossStreak,
    currentWinStreak: winStreak,
    currentLossStreak: lossStreak,
    tradingDays: new Set(
      trades.filter((t) => isStatClosed(t) && filterFn(t)).map((t) => t.dayKey),
    ).size,
  };
}

/**
 * Resolved fills split into would-win and would-lose.
 * Expected P&L is the sum of those outcomes (count × average result).
 */
export function computeWouldResults(trades) {
  let wouldWin = 0;
  let wouldLose = 0;
  let winPnl = 0;
  let losePnl = 0;

  for (const t of trades) {
    if (!isStatClosed(t)) continue;
    const pnl = tradePnl(t) ?? 0;
    if (effectiveWon(t)) {
      wouldWin++;
      winPnl += pnl;
    } else {
      wouldLose++;
      losePnl += pnl;
    }
  }

  const n = wouldWin + wouldLose;
  const expected = winPnl + losePnl;
  return {
    wouldWin,
    wouldLose,
    winPnl,
    losePnl,
    expected,
    perTrade: n > 0 ? expected / n : null,
    avgWin: wouldWin > 0 ? winPnl / wouldWin : null,
    avgLose: wouldLose > 0 ? losePnl / wouldLose : null,
  };
}

function utcDateKeyFromTs(ts) {
  if (!ts) return null;
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return null;
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function utcDateKeyNow() {
  const d = new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** P&L formatting with + or - sign */
export function fmtUsd(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n >= 0 ? "+" : "−";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

/** Pure cash balance formatting (no + prefix) */
export function fmtCash(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n < 0 ? "−" : "";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

export function fmtPct(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(1)}%`;
}

export function greeting() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

/** Single compact session line */
export function formatRecordLine(s) {
  const open = s.open ? ` · ${s.open} open` : "";
  return `${s.wins}W · ${s.losses}L · ${s.resolved} resolved${open}`;
}
