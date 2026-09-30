import { effectiveWon, formatDayLabel, isStatClosed, tradePnl } from "./parse.js?v=19";
import { fmtUsd } from "./stats.js?v=19";
import { slugLabel } from "./format.js?v=19";

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
  const scroller = root.querySelector(".hour-pills");
  const active = scroller?.querySelector(".hour-pill.is-active");
  if (scroller && active) {
    const left = active.offsetLeft - scroller.clientWidth / 2 + active.offsetWidth / 2;
    scroller.scrollLeft = Math.max(0, left);
  }
}

function monthHtml() {
  const label = new Date(state.year, state.month, 1).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });
  const monthPrefix = `${state.year}-${String(state.month + 1).padStart(2, "0")}`;
  const daysInMonth = new Date(state.year, state.month + 1, 0).getDate();

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

  const cursor = new Date(state.year, state.month, 1);
  cursor.setDate(1 - cursor.getDay());
  const last = new Date(state.year, state.month, daysInMonth);
  const weeks = [];
  while (cursor <= last) {
    const keys = [];
    const cells = [];
    for (let i = 0; i < 7; i++) {
      const key = dateKey(cursor);
      keys.push(key);
      cells.push(cellHtml(new Date(cursor), key));
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(`<section class="cal-week">${cells.join("")}${weekResultHtml(keys)}</section>`);
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
    <div class="cal-weeks">${weeks.join("")}</div>
  `;
}

function cellHtml(date, key) {
  const inMonth = date.getFullYear() === state.year && date.getMonth() === state.month;
  const row = byDay.get(key);
  if (!inMonth && !row) return `<div class="cal-cell is-pad"></div>`;
  const tone = !row ? "" : row.net >= 0 ? " is-up" : " is-down";
  const selected = state.selectedDay === key ? " is-selected" : "";
  const outside = inMonth ? "" : " is-outside";
  const net = row ? `<span class="cal-net mono">${fmtUsd(row.net)}</span>` : "";
  const rec = row ? `<span class="cal-rec">${row.wins}W ${row.losses}L</span>` : "";
  return `<button type="button" class="cal-cell${tone}${row ? " has-trades" : ""}${selected}${outside}" data-cal-day="${key}" ${row ? "" : "disabled"}>
    <span class="cal-num">${date.getDate()}</span>${net}${rec}
  </button>`;
}

function weekResultHtml(keys) {
  const stats = summarizeKeys(keys);
  const start = new Date(`${keys[0]}T12:00:00`);
  const end = new Date(`${keys[6]}T12:00:00`);
  const range = `${shortDate(start)} – ${shortDate(end)}`;
  if (!stats.n) {
    return `<div class="cal-week-result is-empty"><span>${range}</span><span>No closed trades</span></div>`;
  }
  const tone = stats.net >= 0 ? "is-up" : "is-down";
  return `<div class="cal-week-result ${tone}">
    <span class="cal-week-range">${range}</span>
    <span class="cal-week-rec">${stats.n} closed · ${stats.wins}W ${stats.losses}L</span>
    <strong class="mono ${stats.net >= 0 ? "up" : "down"}">${fmtUsd(stats.net)}</strong>
  </div>`;
}

function summarizeKeys(keys) {
  let net = 0;
  let wins = 0;
  let losses = 0;
  let n = 0;
  for (const key of keys) {
    const row = byDay.get(key);
    if (!row) continue;
    net += row.net;
    wins += row.wins;
    losses += row.losses;
    n += row.trades.length;
  }
  return { net, wins, losses, n };
}

function dayDetailHtml(dayKey) {
  const row = byDay.get(dayKey) || { net: 0, wins: 0, losses: 0, trades: [] };
  const hours = hoursForDay(row.trades);
  const traded = hours.filter((h) => h.n > 0);
  const hour = state.selectedHour;
  const hourRow = hour == null ? null : hours[hour];
  const list = hourRow ? hourRow.trades : row.trades;
  const markets = marketsFor(list);

  const hourIdx = hour == null ? -1 : traded.findIndex((h) => h.hour === hour);
  const prevDisabled = hour == null ? "disabled" : "";
  const nextDisabled = traded.length === 0 || hourIdx === traded.length - 1 ? "disabled" : "";

  const pills = [
    `<button type="button" class="hour-pill is-all${hour == null ? " is-active" : ""}" data-cal-hour="all">All<span class="hour-pill-net">${row.trades.length}</span></button>`,
    ...traded.map((h) => {
      const tone = h.net >= 0 ? "is-up" : "is-down";
      const on = hour === h.hour ? " is-active" : "";
      return `<button type="button" class="hour-pill ${tone}${on}" data-cal-hour="${h.hour}">
        <span class="hour-pill-time">${pad(h.hour)}:00</span>
        <span class="hour-pill-net mono">${fmtUsd(h.net)}</span>
      </button>`;
    }),
  ].join("");

  const hourNote =
    hour == null
      ? `All hours · ${markets.length} market${markets.length === 1 ? "" : "s"}`
      : `${pad(hour)}:00–${pad((hour + 1) % 24)}:00 · ${markets.length} market${markets.length === 1 ? "" : "s"} · ${fmtUsd(hourRow?.net ?? 0)}`;

  const fills = markets.length
    ? `<ul class="cal-fills">${markets.map((m) => marketItem(m)).join("")}</ul>`
    : `<p class="cal-empty">No closed markets ${hour == null ? "this day" : `at ${pad(hour)}:00`}.</p>`;

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
    <div class="hour-nav">
      <button type="button" class="hour-step" data-cal-hour-step="-1" aria-label="Previous hour" ${prevDisabled}>‹</button>
      <div class="hour-pills" role="tablist" aria-label="Hours with trades">${pills}</div>
      <button type="button" class="hour-step" data-cal-hour-step="1" aria-label="Next hour" ${nextDisabled}>›</button>
    </div>
    <div class="cal-markets-head">
      <h4 class="cal-markets-title">Markets</h4>
      <span class="cal-summary">${hourNote}</span>
    </div>
    ${fills}
    </div>
  `;
}

function marketItem(m) {
  const won = m.losses === 0 && m.wins > 0;
  const lost = m.wins === 0 && m.losses > 0;
  const tone = m.net >= 0 ? "up" : "down";
  const result = won ? "WON" : lost ? "LOST" : `${m.wins}W ${m.losses}L`;
  const side = m.side ? m.side.toUpperCase() : "—";
  return `<li class="cal-fill ${tone}">
    <span class="cal-fill-time mono">${m.clock}</span>
    <span class="cal-fill-side">${esc(side)}</span>
    <span class="cal-fill-market">${esc(m.label)}</span>
    <span class="cal-fill-result">${result}</span>
    <span class="cal-fill-pnl mono">${fmtUsd(m.net)}</span>
  </li>`;
}

function marketsFor(list) {
  const map = new Map();
  for (const t of list) {
    const key = t.slug || t.oid || `${t.entryTs}`;
    let row = map.get(key);
    if (!row) {
      row = {
        slug: t.slug,
        label: slugLabel(t.slug),
        side: t.side || null,
        trades: [],
        net: 0,
        wins: 0,
        losses: 0,
        ts: t.windowEnd || t.entryTs || 0,
      };
      map.set(key, row);
    }
    row.trades.push(t);
    row.net += tradePnl(t) ?? 0;
    if (effectiveWon(t)) row.wins += 1;
    else row.losses += 1;
    const ts = t.windowEnd || t.entryTs || 0;
    if (ts >= row.ts) {
      row.ts = ts;
      row.side = t.side || row.side;
    }
  }
  return [...map.values()]
    .map((row) => ({ ...row, clock: clockLabel(marketTs(row.trades[0]) || row.ts) }))
    .sort((a, b) => b.ts - a.ts);
}

function marketTs(t) {
  const match = String(t.slug || "").match(/-(\d{10,})$/);
  if (match) return Number(match[1]);
  if (t.windowEnd) return t.windowEnd - 300;
  return t.entryTs || 0;
}

function hoursForDay(dayTrades) {
  const hours = Array.from({ length: 24 }, (_, hour) => ({ hour, net: 0, n: 0, trades: [] }));
  for (const t of dayTrades) {
    const ts = marketTs(t);
    const h = ts ? new Date(ts * 1000).getHours() : 0;
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
  const step = e.target.closest("[data-cal-hour-step]");
  if (step && state.selectedDay && !step.disabled) {
    shiftSelectedHour(Number(step.dataset.calHourStep) || 0);
    paint();
    return;
  }
  const day = e.target.closest("[data-cal-day]");
  if (day?.dataset.calDay) {
    const key = day.dataset.calDay;
    if (state.selectedDay === key) {
      state.selectedDay = null;
      state.selectedHour = null;
    } else {
      const [y, m] = key.split("-").map(Number);
      if (Number.isFinite(y) && Number.isFinite(m)) {
        state.year = y;
        state.month = m - 1;
      }
      state.selectedDay = key;
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
    const raw = hourBtn.dataset.calHour;
    state.selectedHour = raw === "all" ? null : Number(raw);
    paint();
  }
}

function shiftSelectedHour(delta) {
  const row = byDay.get(state.selectedDay);
  if (!row) return;
  const traded = hoursForDay(row.trades).filter((h) => h.n > 0);
  if (!traded.length) return;
  const ids = traded.map((h) => h.hour);
  if (state.selectedHour == null) {
    if (delta > 0) state.selectedHour = ids[0];
    return;
  }
  const idx = ids.indexOf(state.selectedHour);
  const next = idx + delta;
  if (next < 0) {
    state.selectedHour = null;
    return;
  }
  if (next >= ids.length) return;
  state.selectedHour = ids[next];
}

function dateKey(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function shortDate(date) {
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function clockLabel(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
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
