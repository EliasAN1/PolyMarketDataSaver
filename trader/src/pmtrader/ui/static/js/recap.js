import { fmtUsd } from "./stats.js";
import { fmtTs, slugLabel, slugUrl, fmtOdds } from "./format.js";
import { effectiveWon, isStatClosed, tradePnl } from "./parse.js";

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
  for (const el of root.querySelectorAll(".trade-time-sub[data-window-end]")) {
    const sec = remainingSeconds(el.dataset.windowEnd);
    if (sec == null) continue;
    el.textContent = `${fmtLeft(sec)} left`;
    el.classList.toggle("is-urgent", sec <= 30);
  }
}

export function startRecapCountdown(root = document) {
  if (countdownTimer) clearInterval(countdownTimer);
  paintCountdown(root);
  countdownTimer = setInterval(() => {
    if (document.hidden) return;
    paintCountdown(root);
  }, 1000);
}

export function renderRecapHtml(recap) {
  if (!recap.recent.length) return "";

  const rows = recap.recent
    .map((t) => {
      const side = (t.side ?? "up").toUpperCase();
      const closed = isStatClosed(t);
      const won = effectiveWon(t);
      const outcomeText = !closed ? "OPEN" : won ? "WON" : "LOST";
      const outcomeCls = !closed ? "open" : won ? "win" : "loss";
      const outcomeBadge = !closed ? "OPEN" : won ? "WIN" : "LOSS";
      const time = fmtTs(t.entryTs);
      const market = slugLabel(t.slug);
      const href = t.slug ? slugUrl(t.slug) : "#";
      const pnlVal = tradePnl(t);
      const pnlFormatted = closed ? fmtUsd(pnlVal) : "In Progress";
      const pnlCls = pnlVal != null ? (pnlVal >= 0 ? "up" : "down") : "";
      const fill = fmtOdds(t.fillPrice);
      const leftSec = !closed ? remainingSeconds(t.windowEnd) : null;
      const timeHtml =
        leftSec != null
          ? `<span class="trade-time-sub" data-window-end="${t.windowEnd}">${fmtLeft(leftSec)} left</span>`
          : `<span class="trade-time-sub">${time}</span>`;

      return `
        <a class="execution-card" href="${href}" target="_blank" rel="noopener noreferrer">
          <div class="execution-card-left">
            <span class="outcome-pill ${outcomeCls}">${outcomeBadge}</span>
            <div class="execution-meta">
              <strong class="execution-market">${market}</strong>
              <div class="execution-subline">
                <span class="side-tag ${side.toLowerCase()}">${side}</span>
                <span class="dot-sep">·</span>
                <span class="fill-tag">@ ${fill}</span>
                <span class="dot-sep">·</span>
                ${timeHtml}
              </div>
            </div>
          </div>
          <div class="execution-card-right">
            <span class="execution-pnl ${pnlCls}">${pnlFormatted}</span>
            <span class="external-arrow">↗</span>
          </div>
        </a>
      `;
    })
    .join("");

  const last5Tone = recap.last5Net >= 0 ? "up" : "down";
  const last5Record =
    recap.last5Wins + recap.last5Losses > 0
      ? `${recap.last5Wins}W · ${recap.last5Losses}L`
      : "—";

  return `
    <div class="executions-summary-strip">
      <div class="summary-pill">
        <span class="summary-label">Last 5 Net P&L</span>
        <strong class="summary-val mono ${last5Tone}">${fmtUsd(recap.last5Net)}</strong>
      </div>
      <div class="summary-pill">
        <span class="summary-label">5-Trade Record</span>
        <span class="summary-val mono">${last5Record}</span>
      </div>
    </div>
    <div class="executions-feed">${rows}</div>
  `;
}
