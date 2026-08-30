const WINDOW_KEY = "pm-centionaire.analyzer.equity.window";

export let windowState = loadWindowState();

export function loadWindowState() {
  try {
    const raw = localStorage.getItem(WINDOW_KEY);
    if (!raw) return { mode: "all", anchor: null };
    const o = JSON.parse(raw);
    return { mode: o.mode ?? "all", anchor: o.anchor ?? null };
  } catch {
    return { mode: "all", anchor: null };
  }
}

export function saveWindowState() {
  localStorage.setItem(WINDOW_KEY, JSON.stringify(windowState));
}

function localMidnightTs(dayKey) {
  const [y, m, d] = dayKey.split("-").map(Number);
  return Math.floor(new Date(y, m - 1, d).getTime() / 1000);
}

function dayKeyFromTs(ts) {
  const d = new Date((ts ?? 0) * 1000);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function weekStartDayKey(dayKey) {
  const d = new Date(`${dayKey}T12:00:00`);
  const dow = d.getDay() || 7;
  d.setDate(d.getDate() - (dow - 1));
  return dayKeyFromTs(d.getTime() / 1000);
}

function monthKeyFromDayKey(dayKey) {
  return dayKey.slice(0, 7);
}

export function collectPeriods(resolved) {
  const days = new Set();
  for (const t of resolved) {
    if (t.dayKey) days.add(t.dayKey);
  }
  const dayList = [...days].sort();
  const weeks = [...new Set(dayList.map(weekStartDayKey))].sort();
  const months = [...new Set(dayList.map(monthKeyFromDayKey))].sort();
  return { days: dayList.reverse(), weeks: weeks.reverse(), months: months.reverse() };
}

export function boundsForWindow(mode, anchor, periods) {
  if (mode === "all") {
    return { startTs: null, endTs: null, label: "All time" };
  }

  if (mode === "day") {
    const dk = anchor || periods.days[0];
    if (!dk) return null;
    const startTs = localMidnightTs(dk);
    const d = new Date(`${dk}T12:00:00`);
    const label = d.toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
    return { startTs, endTs: startTs + 86400, label, anchor: dk };
  }

  if (mode === "week") {
    const wk = anchor || periods.weeks[0];
    if (!wk) return null;
    const startTs = localMidnightTs(wk);
    const endDay = dayKeyFromTs(startTs + 6 * 86400);
    const d0 = new Date(`${wk}T12:00:00`);
    const d1 = new Date(`${endDay}T12:00:00`);
    const label = `${d0.toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${d1.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
    return { startTs, endTs: startTs + 7 * 86400, label, anchor: wk };
  }

  if (mode === "month") {
    const ym = anchor || periods.months[0];
    if (!ym) return null;
    const [y, m] = ym.split("-").map(Number);
    const startTs = Math.floor(new Date(y, m - 1, 1).getTime() / 1000);
    const endTs = Math.floor(new Date(y, m, 1).getTime() / 1000);
    const label = new Date(y, m - 1, 1).toLocaleDateString(undefined, {
      month: "long",
      year: "numeric",
    });
    return { startTs, endTs, label, anchor: ym };
  }

  return null;
}

export function resolveWindowBounds(resolved, periods) {
  const base = boundsForWindow(windowState.mode, windowState.anchor, periods);
  if (!base) return null;

  if (windowState.mode === "all") {
    const ts = resolved.map((t) => t.entryTs ?? 0).filter(Boolean);
    if (!ts.length) return null;
    const min = Math.min(...ts);
    const max = Math.max(...ts);
    const span = Math.max(max - min, 3600);
    return {
      mode: "all",
      startTs: min - span * 0.03,
      endTs: max + span * 0.03,
      label: "All time",
      anchor: null,
    };
  }

  return { mode: windowState.mode, ...base };
}

export function filterTradesInWindow(resolved, bounds) {
  return resolved.filter((t) => {
    const ts = t.entryTs ?? 0;
    return ts >= bounds.startTs && ts < bounds.endTs;
  });
}

export function generateTicks(mode, startTs, endTs) {
  const ticks = [];
  if (mode === "day") {
    let t = startTs - (startTs % 3600);
    while (t <= endTs) {
      if (t >= startTs) ticks.push(t);
      t += 3600;
    }
    return ticks;
  }
  if (mode === "week") {
    for (let i = 0; i < 7; i++) ticks.push(startTs + i * 86400 + 43200);
    return ticks;
  }
  if (mode === "month") {
    let t = startTs;
    while (t < endTs) {
      ticks.push(t + 43200);
      t += 86400;
    }
    return ticks;
  }

  const span = endTs - startTs;
  let step;
  if (span <= 86400 * 2) step = 3600 * 4;
  else if (span <= 86400 * 8) step = 86400;
  else if (span <= 86400 * 35) step = 86400 * 2;
  else if (span <= 86400 * 100) step = 86400 * 7;
  else step = 86400 * 14;

  let t = startTs;
  while (t <= endTs) {
    ticks.push(t);
    t += step;
  }
  return ticks;
}

export function formatTickLabel(mode, ts, spanSec) {
  const d = new Date(ts * 1000);
  if (mode === "day") {
    return d.toLocaleTimeString(undefined, { hour: "numeric" });
  }
  if (mode === "week") {
    return d.toLocaleDateString(undefined, { weekday: "short" });
  }
  if (mode === "month") {
    return String(d.getDate());
  }
  if (spanSec <= 86400 * 2) {
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric" });
  }
  if (spanSec <= 86400 * 45) {
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" });
}

const MIN_LABEL_GAP = 72;

export function buildXAxisLabels(mode, startTs, endTs, xOf, labelY, padTop = 16) {
  const span = endTs - startTs;
  const ticks = generateTicks(mode, startTs, endTs);
  const chosen = [];
  let lastX = -Infinity;

  for (const ts of ticks) {
    const x = xOf(ts);
    if (chosen.length && x - lastX < MIN_LABEL_GAP) continue;
    chosen.push({ ts, x, label: formatTickLabel(mode, ts, span) });
    lastX = x;
  }

  if (chosen.length >= 2) {
    const first = chosen[0];
    const last = chosen[chosen.length - 1];
    const endX = xOf(endTs);
    if (endX - last.x >= MIN_LABEL_GAP * 0.75) {
      if (Math.abs(last.ts - endTs) > span * 0.05) {
        chosen.push({ ts: endTs, x: endX, label: formatTickLabel(mode, endTs, span) });
      }
    }
    if (first.x - xOf(startTs) > MIN_LABEL_GAP * 0.5 && first.ts !== startTs) {
      // keep first tick
    }
  }

  if (!chosen.length) {
    chosen.push({ ts: startTs, x: xOf(startTs), label: formatTickLabel(mode, startTs, span) });
    chosen.push({ ts: endTs, x: xOf(endTs), label: formatTickLabel(mode, endTs, span) });
  }

  let grid = "";
  let labels = "";
  for (const t of chosen) {
    grid += `<line x1="${t.x.toFixed(1)}" y1="${padTop}" x2="${t.x.toFixed(1)}" y2="${(labelY - 14).toFixed(1)}" stroke="#152019" stroke-width="1"/>`;
    let anchor = "middle";
    let x = t.x;
    if (t === chosen[0] && chosen.length > 1) anchor = "start";
    if (t === chosen[chosen.length - 1] && chosen.length > 1) anchor = "end";
    labels += `<text x="${x.toFixed(1)}" y="${labelY}" text-anchor="${anchor}" fill="#5f7468" font-size="10">${t.label}</text>`;
  }
  return { grid, labels };
}

export function anchorsForMode(mode, periods) {
  if (mode === "day") return periods.days;
  if (mode === "week") return periods.weeks;
  if (mode === "month") return periods.months;
  return [];
}

export function anchorLabel(mode, anchor) {
  if (mode === "day") {
    const d = new Date(`${anchor}T12:00:00`);
    return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  }
  if (mode === "week") {
    const end = dayKeyFromTs(localMidnightTs(anchor) + 6 * 86400);
    const d0 = new Date(`${anchor}T12:00:00`);
    const d1 = new Date(`${end}T12:00:00`);
    return `${d0.toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${d1.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  }
  if (mode === "month") {
    const [y, m] = anchor.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  }
  return anchor;
}

export function shiftAnchor(mode, anchor, dir, periods) {
  const list = anchorsForMode(mode, periods);
  const idx = list.indexOf(anchor);
  if (idx < 0) return list[0] ?? null;
  const next = idx + dir;
  if (next < 0 || next >= list.length) return anchor;
  return list[next];
}

export function ensureAnchor(mode, periods) {
  if (mode === "all") {
    windowState.anchor = null;
    return;
  }
  const list = anchorsForMode(mode, periods);
  if (!list.length) {
    windowState.anchor = null;
    return;
  }
  if (!windowState.anchor || !list.includes(windowState.anchor)) {
    windowState.anchor = list[0];
  }
}
