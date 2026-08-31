import { fmtUsd } from "./stats.js";
import { fmtTs, fmtOdds, slugUrl, slugLabel, outcomeLabel, pnlAtStake, fmtSide } from "./format.js";
import { tradePnl, effectiveWon, isStatClosed } from "./parse.js";

const COLUMNS = [
  { key: "entryTs", label: "Time", fmt: (t) => fmtTs(t.entryTs) },
  { key: "slug", label: "Market", link: true },
  { key: "side", label: "Side", customSide: true },
  { key: "fillPrice", label: "Fill", fmt: (t) => fmtOdds(t.fillPrice), mono: true },
  { key: "outcome", label: "Result", customOutcome: true },
  { key: "netPnl", label: "Net P&L", customPnl: true, mono: true },
];

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

  const sorted = [...trades].sort((a, b) => sortDir * compare(a, b, sortKey));
  const frag = document.createDocumentFragment();

  // 1. Table Rows (Desktop)
  for (const t of sorted) {
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
        const closed = isStatClosed(t);
        const won = effectiveWon(t);
        const label = !closed ? "OPEN" : won ? "WON" : "LOST";
        const cls = !closed ? "open" : won ? "won" : "lost";
        td.innerHTML = `<span class="badge-outcome ${cls}">${label}</span>`;
      } else if (col.customPnl) {
        if (!t.resolved) {
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

  // 2. Mobile Cards View (Narrow Screens)
  if (mobileList) {
    if (!trades.length) {
      mobileList.innerHTML = "";
    } else {
      mobileList.innerHTML = sorted.map((t) => {
        const sideLower = (t.side || "up").toLowerCase();
        const closed = isStatClosed(t);
        const won = effectiveWon(t);
        const outcomeTag = !closed ? "OPEN" : won ? "WON" : "LOST";
        const outcomeCls = !closed ? "open" : won ? "won" : "lost";
        const pnl = tradePnl(t);
        const pnlText = !t.resolved ? "open" : pnlAtStake(t);
        const pnlTone = pnl != null ? (pnl >= 0 ? "up" : "down") : "";
        const time = fmtTs(t.entryTs);
        const fillPrice = fmtOdds(t.fillPrice);
        const marketName = slugLabel(t.slug);
        const marketUrl = slugUrl(t.slug);

        return `
          <div class="mobile-trade-card">
            <div class="mobile-card-top">
              <a href="${marketUrl}" target="_blank" rel="noopener noreferrer" class="mobile-card-slug">${marketName}</a>
              <span class="badge-outcome ${outcomeCls}">${outcomeTag}</span>
            </div>
            <div class="mobile-card-grid">
              <div class="mobile-stat-col">
                <span class="mobile-stat-k">Side / Time</span>
                <span class="mobile-stat-v"><span class="badge-side ${sideLower}">${sideLower.toUpperCase()}</span> ${time}</span>
              </div>
              <div class="mobile-stat-col">
                <span class="mobile-stat-k">Fill Price</span>
                <span class="mobile-stat-v">${fillPrice}</span>
              </div>
              <div class="mobile-stat-col">
                <span class="mobile-stat-k">Net P&L</span>
                <span class="mobile-stat-v ${pnlTone}">${pnlText}</span>
              </div>
            </div>
          </div>
        `;
      }).join("");
    }
  }
}

export function bindTradesTableSort(root = document, onSort) {
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
