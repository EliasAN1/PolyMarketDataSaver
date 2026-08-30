import { bandLabel, formatDayLabel, tradePnl } from "./parse.js";
import { fmtUsd, fmtPct } from "./stats.js";
import { passesFilters } from "./filters.js";

function strategyLabel(t) {
  const band = bandLabel(t);
  if (band === "flip") {
    const against =
      t.flipBuy?.side && t.entrySide && t.flipBuy.side !== t.entrySide;
    return against ? "Flipped" : t.flipSell ? "Flipped" : "Added";
  }
  if (band === "inv") return "Inverted";
  return "Original";
}

const SEC_BUCKETS = [
  { label: "240–300s", test: (v) => v >= 240 },
  { label: "180–240s", test: (v) => v >= 180 && v < 240 },
  { label: "120–180s", test: (v) => v >= 120 && v < 180 },
  { label: "60–120s", test: (v) => v >= 60 && v < 120 },
  { label: "0–60s", test: (v) => v >= 0 && v < 60 },
];

export function computeDailySessions(trades, filters) {
  const byDay = new Map();
  const withoutDay = { ...filters, dayKey: null };
  for (const t of trades) {
    if (!passesFilters(t, withoutDay)) continue;
    if (!t.resolved || t.won == null) continue;
    let row = byDay.get(t.dayKey);
    if (!row) {
      row = { dayKey: t.dayKey, trades: 0, wins: 0, losses: 0, net: 0 };
      byDay.set(t.dayKey, row);
    }
    row.trades++;
    row.net += tradePnl(t) ?? 0;
    if (t.won) row.wins++;
    else row.losses++;
  }
  return [...byDay.values()].sort((a, b) => b.dayKey.localeCompare(a.dayKey));
}

function bucketRows(trades, buckets, valOf) {
  return buckets
    .map((b) => {
      const g = trades.filter((t) => {
        const v = valOf(t);
        return v != null && Number.isFinite(v) && b.test(v);
      });
      if (!g.length) return null;
      const wins = g.filter((t) => t.won).length;
      const net = g.reduce((s, t) => s + (tradePnl(t) ?? 0), 0);
      return { label: b.label, n: g.length, wins, net };
    })
    .filter(Boolean);
}

function groupRows(trades, keyOf) {
  const map = new Map();
  for (const t of trades) {
    const k = keyOf(t);
    let g = map.get(k);
    if (!g) {
      g = { label: k, n: 0, wins: 0, net: 0 };
      map.set(k, g);
    }
    g.n++;
    g.net += tradePnl(t) ?? 0;
    if (t.won) g.wins++;
  }
  return [...map.values()].sort((a, b) => a.label.localeCompare(b.label));
}

function table(title, rows) {
  if (!rows.length) return "";
  const body = rows
    .map((r) => {
      const wr = r.n ? ((r.wins / r.n) * 100).toFixed(0) : "0";
      const dim = r.n < 5 ? " class=\"dim\"" : "";
      return `<tr${dim}><td>${r.label}</td><td>${r.n}</td><td>${wr}%</td><td class="${r.net >= 0 ? "up" : "down"}">${fmtUsd(r.net)}</td></tr>`;
    })
    .join("");
  return `<div class="breakdown"><h3>${title}</h3><table><thead><tr><th>Bucket</th><th>n</th><th>WR</th><th>Net</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

export function renderDailySessionsHtml(sessions, selectedDayKey = null) {
  if (!sessions.length) return "";
  const rows = sessions
    .map((s) => {
      const label = formatDayLabel(s.dayKey);
      const selected = s.dayKey === selectedDayKey;
      const cls = selected ? " class=\"daily-row is-selected\"" : " class=\"daily-row\"";
      return `<tr${cls} data-day="${s.dayKey}" tabindex="0" role="button" aria-pressed="${selected ? "true" : "false"}"><td>${label}</td><td>${s.trades}</td><td>${s.wins}</td><td>${s.losses}</td><td class="${s.net >= 0 ? "up" : "down"}">${fmtUsd(s.net)}</td></tr>`;
    })
    .join("");
  return `<table class="data-table daily-table"><thead><tr><th>Day</th><th>Trades</th><th>W</th><th>L</th><th>Net</th></tr></thead><tbody>${rows}</tbody></table>`;
}

export function renderBreakdownsHtml(resolved, filters) {
  const filtered = resolved.filter((t) => passesFilters(t, filters));
  if (!filtered.length) return "";
  return (
    table("Seconds left", bucketRows(filtered, SEC_BUCKETS, (t) => t.secLeft)) +
    table("Side", groupRows(filtered, (t) => (t.side ?? "—").toUpperCase())) +
    table(
      "Strategy",
      groupRows(filtered, strategyLabel),
    ) +
    table(
      "Hour",
      groupRows(filtered, (t) => `${String(t.hour).padStart(2, "0")}:00`).sort(
        (a, b) => parseInt(a.label) - parseInt(b.label),
      ),
    )
  );
}

export { fmtPct };
