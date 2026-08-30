import { parseJsonl } from "./parse.js";

const bookTicksBySlug = new Map();
let availableBookSlugs = new Set();

export function ingestBookRecords(records) {
  for (const r of records) {
    if (r && (r.type === "book" || r.kind === "book") && r.slug) {
      let arr = bookTicksBySlug.get(r.slug);
      if (!arr) {
        arr = [];
        bookTicksBySlug.set(r.slug, arr);
      }
      arr.push({
        ts: Number(r.ts ?? 0),
        upAsk: r.up_ask ?? null,
        downAsk: r.down_ask ?? null,
        upBid: r.up_bid ?? null,
        downBid: r.down_bid ?? null,
      });
    }
  }
  for (const arr of bookTicksBySlug.values()) arr.sort((a, b) => a.ts - b.ts);
}

export async function fetchBookSlugList() {
  try {
    const r = await fetch("/api/logs/books");
    if (!r.ok) return;
    const data = await r.json();
    availableBookSlugs = new Set(Array.isArray(data.slugs) ? data.slugs : []);
  } catch {
    availableBookSlugs = new Set();
  }
}

export async function loadBookForSlug(slug) {
  if (!slug || bookTicksBySlug.has(slug)) return;
  try {
    const r = await fetch(`/api/logs/book/${encodeURIComponent(slug)}`);
    if (!r.ok) return;
    ingestBookRecords(parseJsonl(await r.text()));
  } catch {
    /* optional */
  }
}

export function hasBookTicks(slug) {
  return bookTicksBySlug.has(slug) && bookTicksBySlug.get(slug).length > 0;
}

export function canExpandBook(t) {
  if (!t.slug) return false;
  if (hasBookTicks(t.slug)) return true;
  return availableBookSlugs.has(t.slug);
}

export function renderBookChart(t) {
  const ticks = bookTicksBySlug.get(t.slug);
  if (!ticks?.length) return "";

  const W = 760;
  const H = 160;
  const PADL = 40;
  const PADR = 12;
  const PADT = 14;
  const PADB = 22;
  const plotW = W - PADL - PADR;
  const plotH = H - PADT - PADB;

  const tsMin = Math.min(ticks[0].ts, t.entryTs ?? Infinity);
  const tsMax = Math.max(ticks[ticks.length - 1].ts, t.windowEnd ?? -Infinity);
  const span = tsMax - tsMin || 1;
  const xOf = (ts) => PADL + ((ts - tsMin) / span) * plotW;
  const yOf = (p) => PADT + (1 - Math.min(1, Math.max(0, p ?? 0))) * plotH;

  const path = (key) => {
    let d = "";
    let pen = false;
    for (const tk of ticks) {
      const v = tk[key];
      if (v == null) {
        pen = false;
        continue;
      }
      d += (pen ? " L" : "M") + xOf(tk.ts).toFixed(1) + " " + yOf(v).toFixed(1);
      pen = true;
    }
    return d;
  };

  let grid = "";
  for (const p of [0, 0.5, 1]) {
    const y = yOf(p);
    grid += `<line x1="${PADL}" y1="${y}" x2="${W - PADR}" y2="${y}" stroke="#1c2b27"/>`;
    grid += `<text x="${PADL - 6}" y="${y + 3}" text-anchor="end" fill="#5f7468" font-size="10">${p.toFixed(2)}</text>`;
  }

  let markers = "";
  if (t.entryTs != null && t.entryTs >= tsMin && t.entryTs <= tsMax) {
    const xe = xOf(t.entryTs);
    markers += `<line x1="${xe}" y1="${PADT}" x2="${xe}" y2="${PADT + plotH}" stroke="#7a8c84" stroke-dasharray="2 3"/>`;
  }

  const lines =
    `<path d="${path("upAsk")}" fill="none" stroke="#34d399" stroke-width="1.5"/>` +
    `<path d="${path("downAsk")}" fill="none" stroke="#fb7185" stroke-width="1.5"/>`;

  return `<svg viewBox="0 0 ${W} ${H}" class="book-chart" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Book chart">
    <rect width="${W}" height="${H}" fill="#0a100e" rx="6"/>${grid}${markers}${lines}
  </svg>`;
}
