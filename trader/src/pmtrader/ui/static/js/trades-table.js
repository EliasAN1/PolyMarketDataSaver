import { compactTradeHtml } from "./compact-ui.js?v=26";
import { fmtTs, fmtOdds, slugUrl, slugLabel, outcomeLabel, pnlAtStake, outcomeBadge } from "./format.js?v=19";
import { tradePnl, isStatClosed, effectiveWon, countsInStats } from "./parse.js?v=19";

function attrEsc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;");
}

const COLUMNS = [
  { key: "entryTs", label: "Time", fmt: (t) => fmtTs(t.entryTs) },
  { key: "slug", label: "Market", link: true },
  { key: "side", label: "Side", customSide: true },
  { key: "fillPrice", label: "Fill", fmt: (t) => fmtOdds(t.fillPrice), mono: true },
  { key: "outcome", label: "Result", customOutcome: true },
  { key: "netPnl", label: "Net P&L", customPnl: true, mono: true },
];

let filter = "all";
let visibleCount = 30;

function matchesFilter(t) {
  if (filter === "all") return true;
  if (filter === "unfilled") return t.rejected;
  if (!countsInStats(t)) return false;
  if (filter === "open") return !isStatClosed(t);
  return isStatClosed(t) && effectiveWon(t) === (filter === "won");
}

let sortKey = "entryTs";
let sortDir = -1;

function sortValue(t, key) {
  if (key === "netPnl") return tradePnl(t) ?? 0;
  if (key === "entryTs") return t.entryTs ?? 0;
  if (key === "fillPrice") return t.fillPrice ?? -1;
  if (key === "slug") return t.slug ?? "";
  if (key === "side") return (t.side ?? "").toLowerCase();
  if (key === "outcome") return outcomeLabel(t);
  return t[key] ?? "";
}

function compare(a, b, key) {
  const va = sortValue(a, key);
  const vb = sortValue(b, key);
  if (typeof va === "number" && typeof vb === "number") return va - vb;
  return String(va).localeCompare(String(vb));
}

export function renderTradesTable(trades, root = document) {
  const tbody = root.getElementById("trades-tbody");
  const thead = root.getElementById("trades-thead");
  const countEl = root.getElementById("trades-count");
  const mobileList = root.getElementById("mobile-trades-list");
  if (!tbody || !thead) return;

  // Render Table Headers
  thead.innerHTML = `<tr>${COLUMNS.map((c) => {
    const sorted = c.key === sortKey;
    const arrow = sorted ? (sortDir > 0 ? " ▲" : " ▼") : "";
    return `<th data-sort="${c.key}">${c.label}${arrow}</th>`;
  }).join("")}</tr>`;

  const sorted = trades.filter(matchesFilter).sort((a, b) => sortDir * compare(a, b, sortKey));
  const frag = document.createDocumentFragment();

  // 1. Table Rows (Desktop)
  for (const t of sorted.slice(0, visibleCount)) {
    const tr = document.createElement("tr");
    tr.className = "trade-row";
    if (t.oid) tr.dataset.oid = t.oid;
    if (!t.resolved) tr.classList.add("open");

    for (const col of COLUMNS) {
      const td = document.createElement("td");
      if (col.mono) td.classList.add("mono");

      if (col.link) {
        const a = document.createElement("a");
        a.href = slugUrl(t.slug);
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = slugLabel(t.slug);
        td.appendChild(a);
      } else if (col.customSide) {
        const sideLower = (t.side || "up").toLowerCase();
        td.innerHTML = `<span class="badge-side ${sideLower}">${sideLower.toUpperCase()}</span>`;
      } else if (col.customOutcome) {
        const badge = outcomeBadge(t);
        const title = t.error || "";
        td.innerHTML = `<span class="badge-outcome ${badge.cls}"${title ? ` title="${attrEsc(title)}"` : ""}>${badge.label}</span>`;
      } else if (col.customPnl) {
        if (t.rejected) {
          td.textContent = t.errorShort || "rejected";
          td.title = t.error || t.errorShort || "Order rejected";
          td.classList.add("down");
        } else if (!t.resolved) {
          td.innerHTML = `<span class="badge-outcome open">OPEN</span>`;
        } else {
          const pnl = tradePnl(t);
          td.textContent = pnlAtStake(t);
          if (pnl != null) td.classList.add(pnl >= 0 ? "up" : "down");
        }
      } else {
        td.textContent = col.fmt ? col.fmt(t) : String(t[col.key] ?? "—");
      }
      tr.appendChild(td);
    }

    frag.appendChild(tr);
  }

  tbody.replaceChildren(frag);
  if (countEl) countEl.textContent = `${trades.length} ${trades.length === 1 ? "trade" : "trades"}`;

  if (countEl) countEl.textContent = `${sorted.length} of ${trades.length} orders`;
  if (mobileList) mobileList.innerHTML = sorted.length ? sorted.slice(0, visibleCount).map(compactTradeHtml).join("") : '<p class="compact-empty">No trades match this filter.</p>';
  const more = root.getElementById("trades-load-more");
  if (more) { more.hidden = sorted.length <= visibleCount; more.textContent = `Show more · ${Math.max(0, sorted.length - visibleCount)} remaining`; }
  root.querySelectorAll("[data-trade-filter]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.tradeFilter === filter)));
}

export function bindTradesTableSort(root = document, onSort) {
  root.querySelectorAll("[data-trade-filter]").forEach(button => button.addEventListener("click", () => {
    filter = button.dataset.tradeFilter;
    visibleCount = 30;
    onSort?.();
  }));
  root.getElementById("trades-load-more")?.addEventListener("click", () => { visibleCount += 30; onSort?.(); });
  root.getElementById("trades-table")?.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    if (sortKey === key) sortDir *= -1;
    else {
      sortKey = key;
      sortDir = -1;
    }
    if (onSort) onSort();
  });
}
