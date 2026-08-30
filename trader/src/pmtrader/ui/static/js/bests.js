import { fmtUsd } from "./stats.js";
import { passesFilters } from "./filters.js";
import { tradePnl } from "./parse.js";

function formatDayLabel(dayKey) {
  const d = new Date(`${dayKey}T12:00:00`);
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function isoWeekKey(dayKey) {
  const d = new Date(`${dayKey}T12:00:00`);
  const day = d.getDay() || 7;
  d.setDate(d.getDate() + 4 - day);
  const year = d.getFullYear();
  const jan1 = new Date(year, 0, 1);
  const week = Math.ceil(((d - jan1) / 86400000 + jan1.getDay() + 1) / 7);
  return `${year}-W${String(week).padStart(2, "0")}`;
}

export function computePersonalBests(trades, filters) {
  const match = (t) => passesFilters(t, filters);
  const resolved = trades.filter((t) => t.resolved && t.won != null && match(t));
  if (!resolved.length) return null;

  const byDay = new Map();
  const byWeek = new Map();
  for (const t of resolved) {
    let day = byDay.get(t.dayKey);
    if (!day) {
      day = { dayKey: t.dayKey, net: 0 };
      byDay.set(t.dayKey, day);
    }
    day.net += tradePnl(t) ?? 0;

    const wk = isoWeekKey(t.dayKey);
    let week = byWeek.get(wk);
    if (!week) {
      week = { key: wk, net: 0 };
      byWeek.set(wk, week);
    }
    week.net += tradePnl(t) ?? 0;
  }

  let bestDay = null;
  for (const row of byDay.values()) {
    if (!bestDay || row.net > bestDay.net) bestDay = row;
  }

  let bestWeek = null;
  for (const row of byWeek.values()) {
    if (!bestWeek || row.net > bestWeek.net) bestWeek = row;
  }

  const sorted = [...resolved].sort((a, b) => (a.entryTs ?? 0) - (b.entryTs ?? 0));
  let cum = 0;
  let peak = 0;
  let maxDrawdown = 0;
  let winStreak = 0;
  let maxWinStreak = 0;

  for (const t of sorted) {
    cum += tradePnl(t) ?? 0;
    if (cum > peak) peak = cum;
    maxDrawdown = Math.min(maxDrawdown, cum - peak);

    if (t.won) {
      winStreak++;
      maxWinStreak = Math.max(maxWinStreak, winStreak);
    } else {
      winStreak = 0;
    }
  }

  return {
    bestDay,
    bestWeek,
    peakEquity: peak,
    currentEquity: cum,
    athDelta: cum - peak,
    maxWinStreak,
    maxDrawdown,
  };
}

function bestCard(label, value, sub, tone, { id, labelId } = {}) {
  const valueId = id ? ` id="${id}"` : "";
  const labId = labelId ? ` id="${labelId}"` : "";
  const valueClass = id ? "best-value metric-value" : "best-value";
  return `<article class="best-card${tone ? ` ${tone}` : ""}">
    <span class="best-label"${labId}>${label}</span>
    <strong class="${valueClass}"${valueId}>${value}</strong>
    <span class="best-sub">${sub}</span>
  </article>`;
}

export function renderBestsHtml(b, extras = {}) {
  const today = extras.today;
  const todayLabel = extras.todayLabel ?? "Today";
  const todayTone =
    today == null || Number.isNaN(today) ? "" : today >= 0 ? "up" : "down";
  const cards = [
    bestCard(
      todayLabel,
      today == null ? "—" : fmtUsd(today),
      extras.todaySub ?? "Net P&amp;L",
      todayTone,
      { id: "stat-today", labelId: "stat-today-label" },
    ),
    bestCard(
      "Days",
      extras.days != null ? String(extras.days) : "—",
      extras.daysSub ?? "Sessions traded",
      "",
      { id: "stat-days" },
    ),
  ];

  if (b) {
    const athSub =
      b.athDelta >= -0.001 ? "At peak" : `${fmtUsd(b.athDelta)} from peak`;
    const ddTone = b.maxDrawdown < -0.001 ? "down" : "";
    cards.push(
      bestCard(
        "Best day",
        fmtUsd(b.bestDay?.net),
        b.bestDay ? formatDayLabel(b.bestDay.dayKey) : "—",
        b.bestDay?.net >= 0 ? "up" : "down",
      ),
      bestCard(
        "Best week",
        fmtUsd(b.bestWeek?.net),
        b.bestWeek ? b.bestWeek.key : "—",
        b.bestWeek?.net >= 0 ? "up" : "down",
      ),
      bestCard("Peak equity", fmtUsd(b.peakEquity), athSub, b.peakEquity >= 0 ? "up" : "down"),
      bestCard(
        "Best streak",
        b.maxWinStreak > 1 ? `${b.maxWinStreak}W` : "—",
        "Consecutive wins",
      ),
      bestCard("Max drawdown", fmtUsd(b.maxDrawdown), "From peak", ddTone),
    );
  }

  return `<div class="bests-grid">${cards.join("")}</div>`;
}

const ATH_KEY = "pm-centionaire.analyzer.v2.ath";

export function maybeCelebratePeak(peak) {
  if (peak == null || !Number.isFinite(peak)) return;
  let prev = 0;
  try {
    prev = parseFloat(localStorage.getItem(ATH_KEY) || "0");
  } catch {
    /* ignore */
  }
  if (peak > prev + 0.009 && prev > 0) {
    showToast("New peak equity");
  }
  if (peak >= prev) {
    localStorage.setItem(ATH_KEY, String(peak));
  }
}

function showToast(message) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    el.setAttribute("role", "status");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("is-visible");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.remove("is-visible"), 3200);
}
