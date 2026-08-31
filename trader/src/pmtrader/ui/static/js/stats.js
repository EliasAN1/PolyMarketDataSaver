/** Aggregate stats from real fills only. */

import { tradePnl, effectiveWon, isStatClosed } from "./parse.js";

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
  const todayKey = localDateKeyNow();

  for (const t of trades) {
    if (!filterFn(t)) continue;
    totalTrades++;
    if (!isStatClosed(t)) {
      open++;
      continue;
    }
    resolved++;
    const pnl = tradePnl(t) ?? 0;
    net += pnl;

    if (t.dayKey === todayKey) todayNet += pnl;

    const won = effectiveWon(t);
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
    maxWinStreak,
    maxLossStreak,
    currentWinStreak: winStreak,
    currentLossStreak: lossStreak,
    tradingDays: new Set(
      trades.filter((t) => isStatClosed(t) && filterFn(t)).map((t) => t.dayKey),
    ).size,
  };
}

function localDateKeyNow() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
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
