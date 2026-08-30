/** Live market prices — fetched from the viewer's browser (not the VPS).
 *  Paints a progress-bar background on open trade rows proportional to
 *  the current held-side price (0–1).
 *
 *  Uses Polymarket's public CLOB order-book API (midpoint of best bid/ask),
 *  the same source the trading engine itself reads from — not Gamma's
 *  `outcomePrices`, which only reflects the last *executed* trade and can
 *  sit stale for a long time if no one trades right before resolution. */

const GAMMA_EVENTS = "https://gamma-api.polymarket.com/events";
const CLOB_BASE = "https://clob.polymarket.com";
const ODDS_CACHE_TTL_MS = 200;
const TOKEN_ID_CACHE_TTL_MS = 10 * 60_000;
const ODDS_POLL_MS = 400;

const tokenIdCache = new Map(); // slug → { at, ids: { up, down } | null }
const oddsCache = new Map(); // `${slug}|${side}` → { at, odds }
const rowStates = new Map(); // key → { rows: Set<Element>, odds, targetPct, displayPct }
const lastKnownOdds = new Map(); // key(slug|side) → odds (0–1), survives full DOM re-renders
let pollTimer = null;
let rafId = null;

function parseStringArray(value) {
  if (value == null) return null;
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (Array.isArray(value)) return value.map(String);
  return null;
}

/** Resolve a slug's Up/Down CLOB token IDs via Gamma (cached — they never change). */
async function resolveTokenIds(slug) {
  const hit = tokenIdCache.get(slug);
  if (hit && Date.now() - hit.at < TOKEN_ID_CACHE_TTL_MS) {
    return hit.ids;
  }
  const res = await fetch(`${GAMMA_EVENTS}?slug=${encodeURIComponent(slug)}`, {
    mode: "cors",
    credentials: "omit",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`gamma ${res.status}`);
  const events = await res.json();
  const market = events?.[0]?.markets?.[0];
  const labels = parseStringArray(market?.outcomes);
  const tokenIds = parseStringArray(market?.clobTokenIds);
  let ids = null;
  if (labels?.length && tokenIds?.length && labels.length === tokenIds.length) {
    let up = null;
    let down = null;
    for (let i = 0; i < labels.length; i++) {
      const label = labels[i].toLowerCase();
      if (label.includes("up")) up = tokenIds[i];
      else if (label.includes("down")) down = tokenIds[i];
    }
    if (up || down) ids = { up, down };
  }
  tokenIdCache.set(slug, { at: Date.now(), ids });
  return ids;
}

/** Best-bid/best-ask midpoint for a single CLOB token — the live, tick-by-tick price. */
async function fetchMidpoint(tokenId) {
  const res = await fetch(`${CLOB_BASE}/midpoint?token_id=${encodeURIComponent(tokenId)}`, {
    mode: "cors",
    credentials: "omit",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`clob ${res.status}`);
  const data = await res.json();
  const mid = Number(data?.mid ?? data?.mid_price);
  if (!Number.isFinite(mid)) throw new Error("mid missing");
  return mid;
}

async function fetchOddsForSide(slug, side, { force = false } = {}) {
  const sideKey = String(side).toLowerCase();
  const key = `${slug}|${sideKey}`;
  const hit = oddsCache.get(key);
  if (!force && hit && Date.now() - hit.at < ODDS_CACHE_TTL_MS) {
    return hit.odds;
  }
  const ids = await resolveTokenIds(slug);
  const tokenId = sideKey === "up" ? ids?.up : ids?.down;
  if (!tokenId) throw new Error("token id missing");
  const odds = await fetchMidpoint(tokenId);
  oddsCache.set(key, { at: Date.now(), odds });
  return odds;
}

function rowKey(row) {
  return `${row.dataset.slug}|${row.dataset.side}`;
}

/**
 * Last known live odds for a slug/side, kept across full DOM re-renders
 * (recap + table rebuild their rows from scratch every refresh). Callers use
 * this to seed a freshly-rendered row's initial width so it doesn't flash
 * back to the entry price before the next poll corrects it.
 */
export function getLastKnownOdds(slug, side) {
  if (!slug || !side) return null;
  const key = `${slug}|${String(side).toLowerCase()}`;
  return lastKnownOdds.get(key) ?? null;
}

function paintRow(row, pct, odds) {
  row.style.setProperty("--odds-pct", `${pct}%`);
  row.dataset.displayPct = String(pct);
  row.dataset.odds = odds.toFixed(3);
  row.classList.toggle("odds-strong", odds >= 0.55);
  row.classList.toggle("odds-weak", odds < 0.45);
  row.classList.toggle("odds-neutral", odds >= 0.45 && odds < 0.55);
  const label = row.querySelector(".odds-label");
  if (label) label.textContent = `${Math.round(odds * 100)}¢`;
  lastKnownOdds.set(rowKey(row), odds);
}

function setRowTarget(row, odds) {
  const key = rowKey(row);
  const targetPct = Math.max(0, Math.min(100, odds * 100));
  const prev = rowStates.get(key);
  let displayPct = prev?.displayPct;
  if (displayPct == null) {
    const parsed = Number(row.dataset.displayPct);
    if (Number.isFinite(parsed) && parsed > 0) {
      displayPct = parsed;
    } else {
      const cssPct = parseFloat(
        getComputedStyle(row).getPropertyValue("--odds-pct"),
      );
      displayPct = Number.isFinite(cssPct) && cssPct > 0 ? cssPct : targetPct;
    }
  }
  const rows = prev?.rows ?? new Set();
  for (const r of [...rows]) {
    if (!r.isConnected) rows.delete(r);
  }
  rows.add(row);
  rowStates.set(key, { rows, odds, targetPct, displayPct });
  scheduleAnimation();
}

function scheduleAnimation() {
  if (rafId != null) return;
  rafId = requestAnimationFrame(animationFrame);
}

function animationFrame() {
  rafId = null;
  let stillMoving = false;
  for (const [key, state] of rowStates) {
    for (const r of [...state.rows]) {
      if (!r.isConnected) state.rows.delete(r);
    }
    if (!state.rows.size) {
      rowStates.delete(key);
      continue;
    }
    const diff = state.targetPct - state.displayPct;
    if (Math.abs(diff) < 0.06) {
      state.displayPct = state.targetPct;
    } else {
      state.displayPct += diff * 0.35;
      stillMoving = true;
    }
    for (const row of state.rows) {
      paintRow(row, state.displayPct, state.odds);
    }
  }
  if (stillMoving) scheduleAnimation();
}

/** Collect all open trade rows from both the recap strip and the main table. */
function collectOpenRows(root) {
  const recapRows = [...root.querySelectorAll(".recap-trade.open[data-slug][data-side]")];
  const tableRows = [...root.querySelectorAll("tr.trade-row.open[data-slug][data-side]")];
  return [...recapRows, ...tableRows];
}

/** Poll the CLOB book from this browser and paint open trade rows. */
export async function applyLiveOdds(root = document, { force = false } = {}) {
  const rows = collectOpenRows(root);
  if (!rows.length) return;

  const groups = new Map(); // `${slug}|${side}` → { slug, side }
  for (const row of rows) {
    const slug = row.dataset.slug;
    const side = row.dataset.side;
    if (!slug || !side) continue;
    groups.set(`${slug}|${side}`, { slug, side });
  }

  const oddsByKey = new Map();
  await Promise.all(
    [...groups.entries()].map(async ([key, { slug, side }]) => {
      try {
        oddsByKey.set(key, await fetchOddsForSide(slug, side, { force }));
      } catch {
        /* offline, CORS, or market already fully settled — leave row unchanged */
      }
    }),
  );

  for (const row of rows) {
    const odds = oddsByKey.get(rowKey(row));
    if (odds == null) continue;
    setRowTarget(row, odds);
  }

  // Drop cached state for windows that are no longer open anywhere in the DOM.
  const activeSlugs = new Set([...groups.values()].map((g) => g.slug));
  for (const key of lastKnownOdds.keys()) {
    const slug = key.slice(0, key.lastIndexOf("|"));
    if (!activeSlugs.has(slug)) lastKnownOdds.delete(key);
  }
  for (const key of oddsCache.keys()) {
    const slug = key.slice(0, key.lastIndexOf("|"));
    if (!activeSlugs.has(slug)) oddsCache.delete(key);
  }
  for (const slug of tokenIdCache.keys()) {
    if (!activeSlugs.has(slug)) tokenIdCache.delete(slug);
  }
}

export function startLiveOddsPoll(root = document, intervalMs = ODDS_POLL_MS) {
  stopLiveOddsPoll();
  const tick = () => {
    if (document.hidden) return;
    applyLiveOdds(root, { force: true });
  };
  tick();
  pollTimer = setInterval(tick, intervalMs);
  document.addEventListener("visibilitychange", onVisibility);
}

export function stopLiveOddsPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (rafId != null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
  document.removeEventListener("visibilitychange", onVisibility);
}

function onVisibility() {
  if (!document.hidden) {
    applyLiveOdds(document, { force: true });
  }
}
