import { fmtUsd } from "./stats.js";
import { fmtTs, fmtOdds, slugUrl, slugLabel, outcomeLabel, pnlAtStake, fmtSide } from "./format.js";
import { tradePnl } from "./parse.js";

const COLUMNS = [
  { key: "entryTs", label: "Time", fmt: (t) => fmtTs(t.entryTs) },
  { key: "slug", label: "Market", link: true },
  { key: "side", label: "Side", fmt: (t) => fmtSide(t) },
  { key: "fillPrice", label: "Fill", fmt: (t) => fmtOdds(t.fillPrice), mono: true },
  { key: "outcome", label: "Result", fmt: (t) => outcomeLabel(t) },
  { key: "netPnl", label: "Net", mono: true },
];

let sortKey = "entryTs";
let sortDir = -1;

function sortValue(t, key) {
  if (key === "netPnl") return tradePnl(t) ?? 0;
  if (key === "entryTs") return t.entryTs ?? 0;
  if (key === "fillPrice") return t.fillPrice ?? -1;
  if (key === "slug") return t.slug ?? "";
  if (key === "side") return t.side ?? "";
  if (key === "outcome") return outcomeLabel(t);
  return t[key] ?? "";
}

function compare(a, b, key) {
  const va = sortValue(a, key);
  const vb = sortValue(b, key);
  if (typeof va === "number" && typeof vb === "number") return va - vb;
  return String(va).localeCompare(String(vb));
}

export function renderTradesTable(trades, root) {
  const tbody = root.getElementById("trades-tbody");
  const thead = root.getElementById("trades-thead");
  const countEl = root.getElementById("trades-count");
  if (!tbody || !thead) return;

  thead.innerHTML = `<tr>${COLUMNS.map((c) => {
    const sorted = c.key === sortKey;
    const arrow = sorted ? (sortDir > 0 ? " ▲" : " ▼") : "";
    return `<th data-sort="${c.key}">${c.label}${arrow}</th>`;
  }).join("")}</tr>`;

  const sorted = [...trades].sort((a, b) => sortDir * compare(a, b, sortKey));
  const frag = document.createDocumentFragment();

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
        a.rel = "noopener";
        a.textContent = slugLabel(t.slug);
        td.appendChild(a);
      } else if (col.key === "netPnl") {
        if (!t.resolved) {
          td.textContent = "open";
        } else {
          const pnl = tradePnl(t);
          td.textContent = pnlAtStake(t);
          if (pnl != null) td.classList.add(pnl >= 0 ? "up" : "down");
        }
      } else {
        td.textContent = col.fmt ? col.fmt(t) : String(t[col.key] ?? "—");
      }
      if (col.key === "outcome") td.classList.add(outcomeLabel(t));
      tr.appendChild(td);
    }

    frag.appendChild(tr);
  }

  tbody.replaceChildren(frag);
  if (countEl) countEl.textContent = `${trades.length} trades`;
}

export function bindTradesTableSort(root, onSort) {
  root.getElementById("trades-table")?.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    if (sortKey === key) sortDir *= -1;
    else {
      sortKey = key;
      sortDir = -1;
    }
    onSort();
  });
}
