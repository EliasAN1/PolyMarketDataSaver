import { effectiveWon, formatDayLabel, isStatClosed, tradePnl } from "./parse.js?v=15";
import { fmtUsd } from "./stats.js?v=15";
import { slugLabel } from "./format.js?v=15";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const state = {
  year: new Date().getFullYear(),
  month: new Date().getMonth(),
  selectedDay: null,
  selectedHour: null,
};

let root = null;
let trades = [];
let byDay = new Map();
let lastPaintKey = "";

export function initCalendar(container) {
  root = container;
  if (!root) return;
  root.addEventListener("click", onClick);
}

export function renderCalendar(list) {
  trades = Array.isArray(list) ? list : [];
  byDay = buildDayMap(trades);
  if (state.selectedDay && !byDay.has(state.selectedDay)) {
    const [y, m] = state.selectedDay.split("-").map(Number);
    if (Number.isFinite(y) && Number.isFinite(m)) {
      state.year = y;
      state.month = m - 1;
    }
  }
  const key = paintKey();
  if (key === lastPaintKey && root?.innerHTML) return;
  paint();
}

function paintKey() {
  let n = 0;
  let net = 0;
  for (const row of byDay.values()) {
    n += row.trades.length;
    net += row.net;
  }
  return `${state.year}-${state.month}-${state.selectedDay || ""}-${state.selectedHour ?? ""}-${n}-${net.toFixed(4)}`;
}

function buildDayMap(list) {
  const map = new Map();
  for (const t of list) {
    if (!isStatClosed(t) || !t.dayKey) continue;
    let row = map.get(t.dayKey);
    if (!row) {
      row = { dayKey: t.dayKey, net: 0, wins: 0, losses: 0, trades: [] };
      map.set(t.dayKey, row);
    }
    row.net += tradePnl(t) ?? 0;
    row.trades.push(t);
    if (effectiveWon(t)) row.wins += 1;
    else row.losses += 1;
  }
  return map;
}

function paint() {
  if (!root) return;
  lastPaintKey = paintKey();
  root.innerHTML = monthHtml() + (state.selectedDay ? dayDetailHtml(state.selectedDay) : "");
}

function monthHtml() {
  const label = new Date(state.year, state.month, 1).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });
  const monthPrefix = `${state.year}-${String(state.month + 1).padStart(2, "0")}`;
  const daysInMonth = new Date(state.year, state.month + 1, 0).getDate();
  const startDow = new Date(state.year, state.month, 1).getDay();

  let monthNet = 0;
  let monthWins = 0;
  let monthLosses = 0;
  let monthTrades = 0;
  for (const [key, row] of byDay) {
    if (!key.startsWith(monthPrefix)) continue;
    monthNet += row.net;
    monthWins += row.wins;
    monthLosses += row.losses;
    monthTrades += row.trades.length;
  }

  const cells = [];
  for (let i = 0; i < startDow; i++) cells.push(`<div class="cal-cell is-pad"></div>`);
  for (let d = 1; d <= daysInMonth; d++) {
    const key = `${monthPrefix}-${String(d).padStart(2, "0")}`;
    const row = byDay.get(key);
    const tone = !row ? "" : row.net >= 0 ? " is-up" : " is-down";
    const selected = state.selectedDay === key ? " is-selected" : "";
    const net = row ? `<span class="cal-net mono">${fmtUsd(row.net)}</span>` : "";
    const rec = row ? `<span class="cal-rec">${row.wins}W ${row.losses}L</span>` : "";
    cells.push(
      `<button type="button" class="cal-cell${tone}${row ? " has-trades" : ""}${selected}" data-cal-day="${key}" ${row ? "" : "disabled"}>
        <span class="cal-num">${d}</span>${net}${rec}
      </button>`,
    );
  }

  const summary = monthTrades
    ? `${monthTrades} closed · ${monthWins}W / ${monthLosses}L · ${fmtUsd(monthNet)}`
    : "No closed trades this month";

  return `
    <div class="cal-head">
      <button type="button" class="cal-nav" data-cal-shift="-1" aria-label="Previous month">‹</button>
      <h3 class="cal-title">${label}</h3>
      <button type="button" class="cal-nav" data-cal-shift="1" aria-label="Next month">›</button>
    </div>
    <p class="cal-summary mono">${summary}</p>
    <div class="cal-weekdays">${WEEKDAYS.map((d) => `<span>${d}</span>`).join("")}</div>
    <div class="cal-grid">${cells.join("")}</div>
  `;
}

function dayDetailHtml(dayKey) {
  const row = byDay.get(dayKey) || { net: 0, wins: 0, losses: 0, trades: [] };
  const hours = hoursForDay(row.trades);
  const maxAbs = Math.max(0.01, ...hours.map((h) => Math.abs(h.net)));
  const hour = state.selectedHour;
  const hourRow = hour == null ? null : hours[hour];
  const list = hourRow ? hourRow.trades : row.trades;

  const bars = hours
    .map((h) => {
      const height = Math.max(4, Math.round((Math.abs(h.net) / maxAbs) * 100));
      const tone = h.n === 0 ? "is-empty" : h.net >= 0 ? "is-up" : "is-down";
      const on = hour === h.hour ? " is-active" : "";
      return `<button type="button" class="hour-bar ${tone}${on}" data-cal-hour="${h.hour}" ${h.n ? "" : "disabled"} title="${pad(h.hour)}:00 ${fmtUsd(h.net)} · ${h.n}">
        <span class="hour-fill" style="height:${h.n ? height : 0}%"></span>
        <span class="hour-label">${h.hour % 6 === 0 ? pad(h.hour) : ""}</span>
      </button>`;
    })
    .join("");

  const fills = list.length
    ? `<ul class="cal-fills">${list
        .slice()
        .sort((a, b) => (b.entryTs ?? 0) - (a.entryTs ?? 0))
        .map((t) => fillItem(t))
        .join("")}</ul>`
    : `<p class="cal-empty">No closed fills ${hour == null ? "this day" : `at ${pad(hour)}:00`}.</p>`;

  const hourNote =
    hour == null
      ? "Tap a bar to filter by hour"
      : `${pad(hour)}:00–${pad((hour + 1) % 24)}:00 · ${hourRow.n} · ${fmtUsd(hourRow.net)}`;

  return `
    <div class="cal-day-panel">
    <div class="cal-head">
      <button type="button" class="cal-nav" data-cal-back aria-label="Back to month">‹</button>
      <h3 class="cal-title">${formatDayLabel(dayKey)}</h3>
      <span class="cal-head-spacer"></span>
    </div>
    <div class="cal-day-hero">
      <strong class="mono ${row.net >= 0 ? "up" : "down"}">${fmtUsd(row.net)}</strong>
      <span>${row.wins}W · ${row.losses}L · ${row.trades.length} closed</span>
    </div>
    <p class="cal-summary">${hourNote}</p>
    <div class="hour-chart" role="img" aria-label="Hourly P and L">${bars}</div>
    ${fills}
    </div>
  `;
}

function fillItem(t) {
  const pnl = tradePnl(t);
  const won = effectiveWon(t);
  const side = (t.side || "?").toUpperCase();
  return `<li class="cal-fill ${won ? "up" : "down"}">
    <span class="cal-fill-side">${side}</span>
    <span class="cal-fill-market">${esc(slugLabel(t.slug))}</span>
    <span class="cal-fill-pnl mono">${fmtUsd(pnl)}</span>
  </li>`;
}

function hoursForDay(dayTrades) {
  const hours = Array.from({ length: 24 }, (_, hour) => ({ hour, net: 0, n: 0, trades: [] }));
  for (const t of dayTrades) {
    const h = Number.isInteger(t.hour) ? Math.min(23, Math.max(0, t.hour)) : 0;
    hours[h].net += tradePnl(t) ?? 0;
    hours[h].n += 1;
    hours[h].trades.push(t);
  }
  return hours;
}

function onClick(e) {
  const back = e.target.closest("[data-cal-back]");
  if (back) {
    state.selectedDay = null;
    state.selectedHour = null;
    paint();
    return;
  }
  const shift = e.target.closest("[data-cal-shift]");
  if (shift) {
    const delta = Number(shift.dataset.calShift) || 0;
    const d = new Date(state.year, state.month + delta, 1);
    state.year = d.getFullYear();
    state.month = d.getMonth();
    state.selectedDay = null;
    state.selectedHour = null;
    paint();
    return;
  }
  const day = e.target.closest("[data-cal-day]");
  if (day?.dataset.calDay) {
    if (state.selectedDay === day.dataset.calDay) {
      state.selectedDay = null;
      state.selectedHour = null;
    } else {
      state.selectedDay = day.dataset.calDay;
      state.selectedHour = null;
    }
    paint();
    if (state.selectedDay) {
      root.querySelector(".cal-day-panel")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    return;
  }
  const hourBtn = e.target.closest("[data-cal-hour]");
  if (hourBtn) {
    const h = Number(hourBtn.dataset.calHour);
    state.selectedHour = state.selectedHour === h ? null : h;
    paint();
  }
}

function pad(n) {
  return String(n).padStart(2, "0");
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
