import { fmtUsd } from "./stats.js";
import { fmtTs, slugLabel, slugUrl } from "./format.js";
import { effectiveWon, isStatClosed, tradePnl, tradeStratLabel } from "./parse.js";

export function computeRecap(trades, filterFn = () => true) {
  const list = trades.filter(filterFn);
  const recent = [...list].sort((a, b) => (b.entryTs ?? 0) - (a.entryTs ?? 0)).slice(0, 5);
  const closed = list
    .filter(isStatClosed)
    .sort((a, b) => (a.entryTs ?? 0) - (b.entryTs ?? 0));

  let streakType = null;
  let streakCount = 0;
  if (closed.length) {
    const last = closed[closed.length - 1];
    const lastWon = effectiveWon(last);
    streakType = lastWon ? "win" : "loss";
    streakCount = 1;
    for (let i = closed.length - 2; i >= 0; i--) {
      const won = effectiveWon(closed[i]);
      if (won === lastWon) streakCount++;
      else break;
    }
  }

  const last5Closed = recent.filter(isStatClosed);
  const last5Net = last5Closed.reduce((sum, t) => sum + (tradePnl(t) ?? 0), 0);
  const last5Wins = last5Closed.filter((t) => effectiveWon(t)).length;
  const last5Losses = last5Closed.length - last5Wins;

  return {
    recent,
    streakType,
    streakCount,
    last5Net,
    last5Wins,
    last5Losses,
  };
}

function remainingSeconds(windowEnd) {
  const end = Number(windowEnd);
  if (!Number.isFinite(end) || end <= 0) return null;
  return Math.max(0, Math.ceil(end - Date.now() / 1000));
}

function fmtLeft(sec) {
  return `${sec}s`;
}

let countdownTimer = null;

function paintCountdown(root = document) {
  for (const el of root.querySelectorAll(".recap-left[data-window-end]")) {
    const sec = remainingSeconds(el.dataset.windowEnd);
    if (sec == null) continue;
    el.textContent = fmtLeft(sec);
    el.classList.toggle("recap-left-urgent", sec <= 30);
  }
}

export function startRecapCountdown(root = document) {
  if (countdownTimer) clearInterval(countdownTimer);
  paintCountdown(root);
  countdownTimer = setInterval(() => {
    if (document.hidden) return;
    paintCountdown(root);
  }, 1_000);
}

function streakHtml(type, count) {
  if (!type || count < 1) {
    return `<strong class="recap-streak neutral">—</strong>`;
  }
  const cls = type === "win" ? "up" : "down";
  const label = type === "win" ? "W" : "L";
  return `<strong class="recap-streak ${cls}">${count}${label} streak</strong>`;
}

export function renderRecapHtml(recap) {
  if (!recap.recent.length) return "";

  const streak = streakHtml(recap.streakType, recap.streakCount);
  const last5Tone = recap.last5Net >= 0 ? "up" : "down";
  const last5Record =
    recap.last5Wins + recap.last5Losses > 0
      ? `${recap.last5Wins}W · ${recap.last5Losses}L`
      : "—";

  const rows = recap.recent
    .map((t) => {
      const side = (t.side ?? "—").toUpperCase();
      const strat = tradeStratLabel(t);
      const closed = isStatClosed(t);
      const won = effectiveWon(t);
      const outcome = !closed ? "O" : won ? "W" : "L";
      const tone = outcome === "W" ? "win" : outcome === "L" ? "loss" : "open";
      const time = fmtTs(t.entryTs);
      const market = slugLabel(t.slug);
      const href = t.slug ? slugUrl(t.slug) : "";
      const pnlContent = closed ? fmtUsd(tradePnl(t)) : "open";
      const leftSec = tone === "open" ? remainingSeconds(t.windowEnd) : null;
      const timeHtml =
        leftSec != null
          ? `<span class="recap-time recap-left" data-window-end="${t.windowEnd}">${fmtLeft(leftSec)}</span>`
          : `<span class="recap-time">${time}</span>`;
      const inner =
        `<span class="recap-trade-body">` +
        `<span class="recap-outcome">${outcome}</span>` +
        `<span class="recap-pnl">${pnlContent}</span>` +
        `<span class="recap-meta">${side} · ${strat}</span>` +
        timeHtml +
        `<span class="recap-market">${market}</span>` +
        `</span>`;
      const rowClass = `recap-trade ${tone}`;
      if (!href) {
        return `<li class="${rowClass}">${inner}</li>`;
      }
      return `<li><a class="${rowClass}" href="${href}" target="_blank" rel="noopener noreferrer" aria-label="Open ${market} on Polymarket">${inner}</a></li>`;
    })
    .join("");

  return `<div class="recap-grid">
    <article class="recap-card">
      <span class="recap-label">Current streak</span>
      ${streak}
    </article>
    <article class="recap-card">
      <span class="recap-label">Last 5 net</span>
      <strong class="recap-value ${last5Tone}">${fmtUsd(recap.last5Net)}</strong>
      <span class="recap-sub">${last5Record}</span>
    </article>
  </div>
  <ol class="recap-trades" aria-label="Last 5 trades">${rows}</ol>`;
}
