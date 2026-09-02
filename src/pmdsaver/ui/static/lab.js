function $(id) {
  return document.getElementById(id);
}

function num(id, fallback) {
  const el = $(id);
  if (!el) return fallback;
  const value = Number(el.value);
  return Number.isFinite(value) ? value : fallback;
}

function fmtPnl(value) {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(4)}`;
}

function fmtWindowTime(ts) {
  if (ts == null) return "—";
  const d = new Date(Number(ts) * 1000);
  if (!Number.isFinite(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtElapsed(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

function polymarketUrl(slug) {
  return `https://polymarket.com/event/${encodeURIComponent(slug)}`;
}

function fmtTime(ts) {
  if (ts == null) return "—";
  const d = new Date(Number(ts) * 1000);
  if (!Number.isFinite(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

const state = {
  windows: [],
  skipCounts: {},
  worker: null,
  workerReady: false,
  lastResult: null,
  loading: false,
};

/* ------------------------------------------------------------------ *
 * Slider <-> number sync + enable/disable on toggle
 * ------------------------------------------------------------------ */

const FILTER_PAIRS = [
  { toggle: "useLastMinutes", extras: ["elapsedFromRange", "elapsedFromNum", "elapsedToRange", "elapsedToNum"], filter: "filterLastMinutes" },
  { toggle: "useOdds", extras: ["oddsLoRange", "oddsLoNum", "oddsHiRange", "oddsHiNum"], filter: "filterOdds" },
  { toggle: "useSpot", extras: ["minDistanceRange", "minDistanceNum", "maxDistanceRange", "maxDistanceNum", "btcSource"], filter: "filterSpot" },
  { toggle: "useTwap", range: null, numInput: null, filter: "filterTwap" },
  { toggle: "useVolume", range: "minVolumeRange", numInput: "minVolumeNum", filter: "filterVolume" },
  { toggle: "useVenues", range: "minVenuesRange", numInput: "minVenuesNum", filter: "filterVenues" },
];

function syncRangeAndNumber(rangeId, numId) {
  const range = $(rangeId);
  const numEl = $(numId);
  if (!range || !numEl) return;
  range.addEventListener("input", () => {
    numEl.value = range.value;
    scheduleRecompute();
  });
  numEl.addEventListener("input", () => {
    range.value = numEl.value;
    scheduleRecompute();
  });
}

function applyFilterEnabled(pair) {
  const toggle = $(pair.toggle);
  const filterEl = $(pair.filter);
  const enabled = toggle.checked;
  if (filterEl) filterEl.classList.toggle("disabled", !enabled);
  if (pair.range) $(pair.range).disabled = !enabled;
  if (pair.numInput) $(pair.numInput).disabled = !enabled;
  for (const id of pair.extras || []) {
    const el = $(id);
    if (el) el.disabled = !enabled;
  }
}

function setOrderedBound(loNum, hiNum, loRange, hiRange, which, raw, min, max, digits) {
  let lo = num(loNum, min);
  let hi = num(hiNum, max);
  let value = Number(raw);
  if (!Number.isFinite(value)) return;
  value = Math.min(max, Math.max(min, value));
  if (which === "lo") {
    lo = value;
    if (lo > hi) hi = lo;
  } else {
    hi = value;
    if (hi < lo) lo = hi;
  }
  const fmt = (v) => (digits === 0 ? String(Math.round(v)) : Number(v).toFixed(digits));
  $(loNum).value = fmt(lo);
  $(hiNum).value = fmt(hi);
  $(loRange).value = String(lo);
  $(hiRange).value = String(hi);
}

function wireOrderedBand(loRange, loNum, hiRange, hiNum, min, max, digits) {
  for (const [rangeId, numId, which] of [
    [loRange, loNum, "lo"],
    [hiRange, hiNum, "hi"],
  ]) {
    $(rangeId).addEventListener("input", () => {
      setOrderedBound(loNum, hiNum, loRange, hiRange, which, $(rangeId).value, min, max, digits);
      scheduleRecompute();
    });
    $(numId).addEventListener("input", () => {
      setOrderedBound(loNum, hiNum, loRange, hiRange, which, $(numId).value, min, max, digits);
      scheduleRecompute();
    });
  }
}

function wireFilters() {
  for (const pair of FILTER_PAIRS) {
    if (pair.range && pair.numInput) syncRangeAndNumber(pair.range, pair.numInput);
    $(pair.toggle).addEventListener("change", () => {
      applyFilterEnabled(pair);
      scheduleRecompute();
    });
    applyFilterEnabled(pair);
  }
  wireOrderedBand("elapsedFromRange", "elapsedFromNum", "elapsedToRange", "elapsedToNum", 0, 5, 1);
  wireOrderedBand("oddsLoRange", "oddsLoNum", "oddsHiRange", "oddsHiNum", 0.01, 0.99, 2);
  wireOrderedBand("minDistanceRange", "minDistanceNum", "maxDistanceRange", "maxDistanceNum", 0, 200, 0);
  for (const id of ["stake", "feeRate"]) {
    $(id).addEventListener("input", scheduleRecompute);
  }
  $("fillMode").addEventListener("change", scheduleRecompute);
  $("btcSource").addEventListener("change", scheduleRecompute);
}

const BTC_SOURCE_LABELS = {
  binance_spot: "Binance",
  coinbase_spot: "Coinbase",
  bybit_spot: "Bybit",
  median: "median of 3",
};

function btcSourceLabel(source) {
  return BTC_SOURCE_LABELS[source] || BTC_SOURCE_LABELS[LabEngine.DEFAULT_BTC_SOURCE];
}

/* ------------------------------------------------------------------ *
 * Params
 * ------------------------------------------------------------------ */

function getParams() {
  return {
    stake: Math.max(0.01, num("stake", 1)),
    feeRate: Math.max(0, num("feeRate", 0.07)),
    fillMode: $("fillMode").value,
    useLastMinutes: $("useLastMinutes").checked,
    elapsedFromMin: num("elapsedFromNum", 2),
    elapsedToMin: num("elapsedToNum", 5),
    useOdds: $("useOdds").checked,
    oddsLo: num("oddsLoNum", 0.2),
    oddsHi: num("oddsHiNum", 0.3),
    useSpot: $("useSpot").checked,
    minDistance: num("minDistanceNum", 5),
    maxDistance: num("maxDistanceNum", 10),
    btcSource: $("btcSource").value,
    useTwap: $("useTwap").checked,
    useVolume: $("useVolume").checked,
    minVolume: num("minVolumeNum", 50),
    useVenues: $("useVenues").checked,
    minVenues: Math.max(1, Math.round(num("minVenuesNum", 2))),
  };
}

function setParams(params) {
  const map = {
    stake: "stake",
    feeRate: "feeRate",
    elapsedFromMin: ["elapsedFromRange", "elapsedFromNum"],
    elapsedToMin: ["elapsedToRange", "elapsedToNum"],
    oddsLo: ["oddsLoRange", "oddsLoNum"],
    oddsHi: ["oddsHiRange", "oddsHiNum"],
    minDistance: ["minDistanceRange", "minDistanceNum"],
    maxDistance: ["maxDistanceRange", "maxDistanceNum"],
    minVolume: ["minVolumeRange", "minVolumeNum"],
    minVenues: ["minVenuesRange", "minVenuesNum"],
  };
  for (const [key, ids] of Object.entries(map)) {
    if (params[key] == null) continue;
    const targets = Array.isArray(ids) ? ids : [ids];
    for (const id of targets) {
      const el = $(id);
      if (el) el.value = params[key];
    }
  }
  if (params.fillMode) $("fillMode").value = params.fillMode;
  if (params.btcSource) $("btcSource").value = params.btcSource;
  const toggles = ["useLastMinutes", "useOdds", "useSpot", "useTwap", "useVolume", "useVenues"];
  for (const t of toggles) {
    if (params[t] != null) $(t).checked = params[t];
  }
  for (const pair of FILTER_PAIRS) applyFilterEnabled(pair);
  const lo = num("oddsLoNum", 0.2);
  const hi = num("oddsHiNum", 0.3);
  if (lo > hi) {
    $("oddsLoNum").value = hi.toFixed(2);
    $("oddsHiNum").value = lo.toFixed(2);
    $("oddsLoRange").value = String(hi);
    $("oddsHiRange").value = String(lo);
  }
  const fromM = num("elapsedFromNum", 2);
  const toM = num("elapsedToNum", 5);
  if (fromM > toM) {
    $("elapsedFromNum").value = toM.toFixed(1);
    $("elapsedToNum").value = fromM.toFixed(1);
    $("elapsedFromRange").value = String(toM);
    $("elapsedToRange").value = String(fromM);
  }
  const dLo = num("minDistanceNum", 5);
  const dHi = num("maxDistanceNum", 10);
  if (dLo > dHi) {
    $("minDistanceNum").value = String(Math.round(dHi));
    $("maxDistanceNum").value = String(Math.round(dLo));
    $("minDistanceRange").value = String(dHi);
    $("maxDistanceRange").value = String(dLo);
  }
}

/* ------------------------------------------------------------------ *
 * Recompute (debounced ~30ms so slider drags feel instant)
 * ------------------------------------------------------------------ */

let recomputeTimer = null;
function scheduleRecompute() {
  if (recomputeTimer) clearTimeout(recomputeTimer);
  recomputeTimer = setTimeout(recompute, 30);
  scheduleSweep();
}

function recompute() {
  if (!state.windows.length) return;
  const params = getParams();
  const result = LabEngine.evaluate(state.windows, params);
  state.lastResult = result;
  renderKpis(result.summary);
  drawEquity(result.equity);
  renderTrades(result.trades);
}

/* ------------------------------------------------------------------ *
 * KPI cards
 * ------------------------------------------------------------------ */

function renderKpis(summary) {
  const pnlEl = $("kpiNetPnl");
  pnlEl.textContent = fmtPnl(summary.netPnl);
  pnlEl.className = `value ${summary.netPnl >= 0 ? "up" : "down"}`;
  $("kpiNetPnlMeta").textContent = `stake $${num("stake", 1).toFixed(2)} · ${summary.windows} windows`;

  $("kpiWinRate").textContent = summary.winRate == null ? "—" : `${(summary.winRate * 100).toFixed(1)}%`;
  $("kpiWinRateMeta").textContent = `${summary.wins} win / ${summary.losses} loss`;

  $("kpiTrades").textContent = String(summary.trades);
  $("kpiTradesMeta").textContent = `${summary.noTrade} no entry`;

  $("kpiFees").textContent = summary.feesPaid.toFixed(4);
  $("kpiFeesMeta").textContent = summary.trades ? `${(summary.feesPaid / summary.trades).toFixed(4)} / trade` : "—";

  $("kpiAvg").textContent = summary.avgPnl == null ? "—" : fmtPnl(summary.avgPnl);
  const avgEl = $("kpiAvg");
  avgEl.className = `value ${summary.avgPnl == null ? "" : summary.avgPnl >= 0 ? "up" : "down"}`;
  $("kpiAvgMeta").textContent = "per filled window";

  $("kpiDrawdown").textContent = summary.maxDrawdown ? `-${summary.maxDrawdown.toFixed(4)}` : "0.0000";
  $("kpiDrawdownMeta").textContent = "peak-to-trough equity";

  const days = summary.days || 0;
  $("kpiTradesDay").textContent = summary.tradesPerDay == null ? "—" : summary.tradesPerDay.toFixed(1);
  $("kpiTradesDayMeta").textContent = days
    ? `${summary.participation == null ? "" : `${(summary.participation * 100).toFixed(0)}% of windows · `}${days} day${days === 1 ? "" : "s"}`
    : "local calendar days in range";

  const dayEl = $("kpiDayPnl");
  dayEl.textContent = summary.avgDayPnl == null ? "—" : fmtPnl(summary.avgDayPnl);
  dayEl.className = `value ${summary.avgDayPnl == null ? "" : summary.avgDayPnl >= 0 ? "up" : "down"}`;
  const med = summary.medianDayPnl;
  $("kpiDayPnlMeta").textContent = med == null
    ? "including days with no trade"
    : `median ${fmtPnl(med)} · ${summary.winningDays || 0}W / ${summary.losingDays || 0}L days`;

  const pf = summary.profitFactor;
  const pfEl = $("kpiPf");
  if (pf == null) {
    pfEl.textContent = "—";
    pfEl.className = "value";
    $("kpiPfMeta").textContent = "gross wins / gross losses";
  } else if (!Number.isFinite(pf)) {
    pfEl.textContent = "∞";
    pfEl.className = "value up";
    $("kpiPfMeta").textContent = "no losing trades";
  } else {
    pfEl.textContent = pf.toFixed(2);
    pfEl.className = `value ${pf >= 1 ? "up" : "down"}`;
    $("kpiPfMeta").textContent = "gross wins / gross losses";
  }

  const payoffEl = $("kpiPayoff");
  if (summary.avgWin == null && summary.avgLoss == null) {
    payoffEl.textContent = "—";
    payoffEl.className = "value";
    $("kpiPayoffMeta").textContent = "mean winner vs mean loser";
  } else {
    const w = summary.avgWin == null ? "—" : fmtPnl(summary.avgWin);
    const l = summary.avgLoss == null ? "—" : `-${Number(summary.avgLoss).toFixed(4)}`;
    payoffEl.textContent = `${w} / ${l}`;
    payoffEl.className = "value";
    $("kpiPayoffMeta").textContent = summary.payoff == null
      ? "mean winner vs mean loser"
      : `payoff ${summary.payoff.toFixed(2)}× · avg fill ${summary.avgFill == null ? "—" : summary.avgFill.toFixed(3)}`;
  }

  const best = summary.bestDay;
  const worst = summary.worstDay;
  const bestEl = $("kpiBestDay");
  if (!best) {
    bestEl.textContent = "—";
    bestEl.className = "value";
    $("kpiBestDayMeta").textContent = "local calendar day";
  } else {
    bestEl.textContent = fmtPnl(best.pnl);
    bestEl.className = `value ${best.pnl >= 0 ? "up" : "down"}`;
    $("kpiBestDayMeta").textContent = worst && worst.day !== best.day
      ? `worst ${fmtPnl(worst.pnl)} · ${worst.day.slice(5)}`
      : best.day;
  }

  $("kpiStreak").textContent = `${summary.maxWinStreak || 0}W / ${summary.maxLossStreak || 0}L`;
  const upN = summary.upTrades || 0;
  const downN = summary.downTrades || 0;
  const upR = summary.upWinRate == null ? "—" : `${(summary.upWinRate * 100).toFixed(0)}%`;
  const downR = summary.downWinRate == null ? "—" : `${(summary.downWinRate * 100).toFixed(0)}%`;
  $("kpiStreakMeta").textContent = `UP ${upN} (${upR}) · DOWN ${downN} (${downR})`;
}

/* ------------------------------------------------------------------ *
 * Equity chart with hover (ported 1:1 from the old backtest.js)
 * ------------------------------------------------------------------ */

let equityState = null;

function hideEquityHover() {
  const hair = $("equityHair");
  const dot = $("equityDot");
  const tip = $("equityTip");
  if (hair) hair.hidden = true;
  if (dot) dot.hidden = true;
  if (tip) tip.hidden = true;
}

function drawEquity(points) {
  const svg = $("equityChart");
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  hideEquityHover();
  const ns = "http://www.w3.org/2000/svg";
  const w = 800;
  const h = 220;
  const pad = 16;
  if (!points.length) {
    equityState = null;
    const text = document.createElementNS(ns, "text");
    text.setAttribute("x", "20");
    text.setAttribute("y", "28");
    text.setAttribute("fill", "#8ea0b8");
    text.setAttribute("font-size", "12");
    text.textContent = "No filled trades with these settings.";
    svg.appendChild(text);
    return;
  }
  const ys = points.map((p) => Number(p.equity));
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(0, ...ys);
  const spanY = maxY - minY || 1;
  const xAt = (i) => pad + (i / Math.max(points.length - 1, 1)) * (w - pad * 2);
  const yAt = (v) => h - pad - ((v - minY) / spanY) * (h - pad * 2);
  const zero = document.createElementNS(ns, "line");
  zero.setAttribute("x1", String(pad));
  zero.setAttribute("x2", String(w - pad));
  zero.setAttribute("y1", String(yAt(0)));
  zero.setAttribute("y2", String(yAt(0)));
  zero.setAttribute("stroke", "#243044");
  svg.appendChild(zero);
  const d = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(Number(p.equity)).toFixed(1)}`)
    .join(" ");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#5b8cff");
  path.setAttribute("stroke-width", "2");
  svg.appendChild(path);
  equityState = { points, w, h, pad, minY, spanY };
}

function onEquityMove(event) {
  if (!equityState || !equityState.points.length) return;
  const svg = $("equityChart");
  const hair = $("equityHair");
  const dot = $("equityDot");
  const tip = $("equityTip");
  const rect = svg.getBoundingClientRect();
  const { points, w, h, pad, minY, spanY } = equityState;
  const n = points.length;
  const vx = ((event.clientX - rect.left) / rect.width) * w;
  const inner = w - pad * 2 || 1;
  const frac = Math.max(0, Math.min(1, (vx - pad) / inner));
  const i = Math.round(frac * Math.max(n - 1, 0));
  const point = points[i];
  if (!point) return;
  const xAt = (idx) => pad + (idx / Math.max(n - 1, 1)) * inner;
  const yAt = (v) => h - pad - ((v - minY) / spanY) * (h - pad * 2);
  const equity = Number(point.equity);
  const leftPct = (xAt(i) / w) * 100;
  const topPct = (yAt(equity) / h) * 100;
  hair.hidden = false;
  hair.style.left = `${leftPct}%`;
  dot.hidden = false;
  dot.style.left = `${leftPct}%`;
  dot.style.top = `${topPct}%`;
  const cls = equity >= 0 ? "up" : "down";
  tip.hidden = false;
  tip.innerHTML = `<div class="tip-pnl ${cls}">${fmtPnl(equity)}</div><div class="tip-time">${fmtTime(point.t)}</div>`;
  const box = $("equityBox").getBoundingClientRect();
  const tipW = tip.offsetWidth;
  const tipH = tip.offsetHeight;
  let tipLeft = (leftPct / 100) * box.width - tipW / 2;
  tipLeft = Math.max(8, Math.min(box.width - tipW - 8, tipLeft));
  let tipTop = (topPct / 100) * box.height - tipH - 12;
  if (tipTop < 8) tipTop = (topPct / 100) * box.height + 12;
  tip.style.left = `${tipLeft}px`;
  tip.style.top = `${tipTop}px`;
}

/* ------------------------------------------------------------------ *
 * Trades table: search, side/result filters, sortable columns, pagination
 * ------------------------------------------------------------------ */

const tradesState = {
  all: [],
  sortKey: "t",
  sortDir: -1,
  search: "",
  side: "any",
  result: "any",
  page: 0,
  pageSize: 50,
};

function cmp(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

function filteredSortedTrades() {
  let rows = tradesState.all;
  if (tradesState.search) {
    const q = tradesState.search.toLowerCase();
    rows = rows.filter((r) => r.slug.toLowerCase().includes(q));
  }
  if (tradesState.side !== "any") rows = rows.filter((r) => r.side === tradesState.side);
  if (tradesState.result !== "any") rows = rows.filter((r) => r.status === tradesState.result);
  const { sortKey, sortDir } = tradesState;
  return rows.slice().sort((a, b) => sortDir * cmp(a[sortKey], b[sortKey]));
}

function renderTrades(trades) {
  tradesState.all = trades;
  tradesState.page = 0;
  renderTradesTable();
}

function renderTradesTable() {
  const filtered = filteredSortedTrades();
  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / tradesState.pageSize));
  tradesState.page = Math.min(Math.max(0, tradesState.page), totalPages - 1);
  const start = tradesState.page * tradesState.pageSize;
  const pageRows = filtered.slice(start, start + tradesState.pageSize);

  $("tradesMeta").textContent = tradesState.all.length
    ? `${total} of ${tradesState.all.length} trades match filters`
    : "";

  const body = $("tradesBody");
  body.replaceChildren();
  if (!pageRows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="11" class="muted">No trades match these filters.</td>`;
    body.appendChild(tr);
  } else {
    const frag = document.createDocumentFragment();
    for (const row of pageRows) {
      const tr = document.createElement("tr");
      tr.className = row.status === "win" ? "row-win" : "row-loss";
      const pnlCls = row.pnl >= 0 ? "up" : "down";
      tr.innerHTML = `
        <td>
          <a class="lab-window-link" href="/replay?window_id=${row.id}" title="Replay this window">${fmtWindowTime(row.start)}</a>
          <a class="lab-ext-link" href="${polymarketUrl(row.slug)}" target="_blank" rel="noopener" title="${row.slug}">↗</a>
        </td>
        <td><span class="result-pill ${row.status}">${row.side.toUpperCase()} ${row.status === "win" ? "won" : "lost"}</span></td>
        <td class="num">${row.fill.toFixed(3)}</td>
        <td class="num">${row.shares.toFixed(2)}</td>
        <td class="num">${row.fee.toFixed(4)}</td>
        <td class="num ${pnlCls}">${fmtPnl(row.pnl)}</td>
        <td class="num">${fmtElapsed(row.elapsed)}</td>
        <td class="num">${row.upMid == null ? "—" : row.upMid.toFixed(3)}</td>
        <td class="num">${row.btcMinusPtb == null ? "—" : row.btcMinusPtb.toFixed(2)}</td>
        <td class="num">${row.twapMinusPtb == null ? "—" : row.twapMinusPtb.toFixed(2)}</td>
        <td class="num">${row.volume == null ? "—" : row.volume.toFixed(2)}</td>
      `;
      frag.appendChild(tr);
    }
    body.appendChild(frag);
  }

  $("tradesPageLabel").textContent = total ? `${start + 1}–${Math.min(start + tradesState.pageSize, total)} of ${total}` : "0 of 0";
  $("tradesPrevBtn").disabled = tradesState.page <= 0;
  $("tradesNextBtn").disabled = start + tradesState.pageSize >= total;

  document.querySelectorAll("#tradesTable thead th[data-sort]").forEach((th) => {
    th.classList.toggle("sort-asc", th.dataset.sort === tradesState.sortKey && tradesState.sortDir === 1);
    th.classList.toggle("sort-desc", th.dataset.sort === tradesState.sortKey && tradesState.sortDir === -1);
  });
}

function wireTradesToolbar() {
  $("tradeSearch").addEventListener("input", () => {
    tradesState.search = $("tradeSearch").value.trim();
    tradesState.page = 0;
    renderTradesTable();
  });
  $("tradeSideFilter").addEventListener("change", () => {
    tradesState.side = $("tradeSideFilter").value;
    tradesState.page = 0;
    renderTradesTable();
  });
  $("tradeResultFilter").addEventListener("change", () => {
    tradesState.result = $("tradeResultFilter").value;
    tradesState.page = 0;
    renderTradesTable();
  });
  $("tradesTable").querySelector("thead").addEventListener("click", (event) => {
    const th = event.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    if (tradesState.sortKey === key) tradesState.sortDir *= -1;
    else {
      tradesState.sortKey = key;
      tradesState.sortDir = 1;
    }
    renderTradesTable();
  });
  $("tradesPrevBtn").addEventListener("click", () => {
    tradesState.page = Math.max(0, tradesState.page - 1);
    renderTradesTable();
  });
  $("tradesNextBtn").addEventListener("click", () => {
    tradesState.page += 1;
    renderTradesTable();
  });
}

/* ------------------------------------------------------------------ *
 * Sweep chart: PnL + win rate across one variable's whole range
 * ------------------------------------------------------------------ */

const SWEEP_RANGES = {
  oddsLo: { min: 0.01, max: 0.99, step: 0.02, requires: "useOdds" },
  oddsHi: { min: 0.01, max: 0.99, step: 0.02, requires: "useOdds" },
  elapsedFromMin: { min: 0, max: 5, step: 0.1, requires: "useLastMinutes" },
  elapsedToMin: { min: 0, max: 5, step: 0.1, requires: "useLastMinutes" },
  minDistance: { min: 0, max: 200, step: 3, requires: "useSpot" },
  maxDistance: { min: 0, max: 200, step: 3, requires: "useSpot" },
  minVolume: { min: 0, max: 500, step: 10, requires: "useVolume" },
  minVenues: { min: 1, max: 4, step: 1, requires: "useVenues" },
};

let sweepTimer = null;
function scheduleSweep() {
  if (sweepTimer) clearTimeout(sweepTimer);
  sweepTimer = setTimeout(runSweep, 150);
}

function runSweep() {
  if (!state.windows.length) return;
  const key = $("sweepVar").value;
  const params = getParams();
  const range = { ...SWEEP_RANGES[key] };
  if (key === "oddsLo") range.max = params.oddsHi;
  if (key === "oddsHi") range.min = params.oddsLo;
  if (key === "elapsedFromMin") range.max = params.elapsedToMin;
  if (key === "elapsedToMin") range.min = params.elapsedFromMin;
  if (key === "minDistance") range.max = params.maxDistance;
  if (key === "maxDistance") range.min = params.minDistance;
  if (range.max < range.min) return;
  const values = [];
  for (let v = range.min; v <= range.max + 1e-9; v += range.step) {
    values.push(Math.round(v * 1000) / 1000);
  }
  // Sweeping a variable implies trying it, even if its filter is currently off.
  params[range.requires] = true;
  const points = LabEngine.sweep(state.windows, params, key, values);
  drawSweep(points, params[key]);
}

function drawSweep(points, currentValue) {
  const svg = $("sweepChart");
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!points.length) return;
  const ns = "http://www.w3.org/2000/svg";
  const w = 800;
  const h = 220;
  const pad = 20;

  const pnls = points.map((p) => p.netPnl);
  const minPnl = Math.min(0, ...pnls);
  const maxPnl = Math.max(0, ...pnls);
  const spanPnl = maxPnl - minPnl || 1;
  const xAt = (i) => pad + (i / Math.max(points.length - 1, 1)) * (w - pad * 2);
  const yPnl = (v) => h - pad - ((v - minPnl) / spanPnl) * (h - pad * 2);
  const yWin = (v) => h - pad - (v == null ? 0 : v) * (h - pad * 2);

  const zero = document.createElementNS(ns, "line");
  zero.setAttribute("x1", String(pad));
  zero.setAttribute("x2", String(w - pad));
  zero.setAttribute("y1", String(yPnl(0)));
  zero.setAttribute("y2", String(yPnl(0)));
  zero.setAttribute("stroke", "#243044");
  svg.appendChild(zero);

  const pnlPath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yPnl(p.netPnl).toFixed(1)}`).join(" ");
  const pnlEl = document.createElementNS(ns, "path");
  pnlEl.setAttribute("d", pnlPath);
  pnlEl.setAttribute("fill", "none");
  pnlEl.setAttribute("stroke", "#5b8cff");
  pnlEl.setAttribute("stroke-width", "2");
  svg.appendChild(pnlEl);

  const winPath = points
    .filter((p) => p.winRate != null)
    .map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(points.indexOf(p)).toFixed(1)} ${yWin(p.winRate).toFixed(1)}`)
    .join(" ");
  if (winPath) {
    const winEl = document.createElementNS(ns, "path");
    winEl.setAttribute("d", winPath);
    winEl.setAttribute("fill", "none");
    winEl.setAttribute("stroke", "#e0b84e");
    winEl.setAttribute("stroke-width", "1.5");
    winEl.setAttribute("stroke-dasharray", "4 3");
    svg.appendChild(winEl);
  }

  let bestIdx = 0;
  for (let i = 1; i < points.length; i++) {
    if (points[i].netPnl > points[bestIdx].netPnl) bestIdx = i;
  }
  const best = points[bestIdx];
  const bestDot = document.createElementNS(ns, "circle");
  bestDot.setAttribute("cx", xAt(bestIdx).toFixed(1));
  bestDot.setAttribute("cy", yPnl(best.netPnl).toFixed(1));
  bestDot.setAttribute("r", "5");
  bestDot.setAttribute("fill", "#3dd68c");
  bestDot.setAttribute("stroke", "#0b0f14");
  svg.appendChild(bestDot);

  let curIdx = 0;
  let curDist = Infinity;
  for (let i = 0; i < points.length; i++) {
    const dist = Math.abs(points[i].value - currentValue);
    if (dist < curDist) {
      curDist = dist;
      curIdx = i;
    }
  }
  const curDot = document.createElementNS(ns, "circle");
  curDot.setAttribute("cx", xAt(curIdx).toFixed(1));
  curDot.setAttribute("cy", yPnl(points[curIdx].netPnl).toFixed(1));
  curDot.setAttribute("r", "4");
  curDot.setAttribute("fill", "#8ea0b8");
  curDot.setAttribute("stroke", "#0b0f14");
  svg.appendChild(curDot);

  svg.dataset.points = JSON.stringify(points.map((p) => p.value));
  svg._labPoints = points;
  svg._labPad = pad;
  svg._labW = w;
}

function onSweepClick(event) {
  const svg = $("sweepChart");
  const points = svg._labPoints;
  if (!points || !points.length) return;
  const rect = svg.getBoundingClientRect();
  const pad = svg._labPad;
  const w = svg._labW;
  const vx = ((event.clientX - rect.left) / rect.width) * w;
  const inner = w - pad * 2 || 1;
  const frac = Math.max(0, Math.min(1, (vx - pad) / inner));
  const i = Math.round(frac * Math.max(points.length - 1, 0));
  const point = points[i];
  if (!point) return;
  const key = $("sweepVar").value;
  const params = {};
  params[key] = point.value;
  params[SWEEP_RANGES[key].requires] = true;
  setParams(params);
  scheduleRecompute();
}

/* ------------------------------------------------------------------ *
 * Optimizer worker: coarse grid search over enabled filters
 * ------------------------------------------------------------------ */

function initWorker() {
  state.worker = new Worker("/static/lab_worker.js?v=5");
  state.worker.onmessage = (event) => {
    const msg = event.data || {};
    if (msg.type === "loaded") {
      state.workerReady = true;
    } else if (msg.type === "progress") {
      const pct = msg.total ? Math.round((msg.done / msg.total) * 100) : 0;
      $("searchProgressFill").style.width = `${pct}%`;
      $("searchStatus").textContent = `${msg.done} / ${msg.total} combinations`;
    } else if (msg.type === "result") {
      renderBestResults(msg.results, msg.scanned, msg.total);
      $("findBestBtn").disabled = false;
      $("searchProgress").hidden = true;
    }
  };
}

const SEARCH_DIMS = {
  elapsedFromMin: { min: 0, max: 4.5, step: 0.5, requires: "useLastMinutes" },
  elapsedToMin: { min: 0.5, max: 5, step: 0.5, requires: "useLastMinutes" },
  oddsLo: { min: 0.05, max: 0.85, step: 0.1, requires: "useOdds" },
  oddsHi: { min: 0.15, max: 0.95, step: 0.1, requires: "useOdds" },
  minDistance: { min: 0, max: 80, step: 10, requires: "useSpot" },
  maxDistance: { min: 10, max: 200, step: 20, requires: "useSpot" },
  minVolume: { min: 0, max: 300, step: 50, requires: "useVolume" },
  minVenues: { min: 1, max: 4, step: 1, requires: "useVenues" },
};

function findBestSettings() {
  if (!state.windows.length || !state.workerReady) return;
  const params = getParams();
  const dims = {};
  for (const [key, dim] of Object.entries(SEARCH_DIMS)) {
    if (params[dim.requires]) {
      dims[key] = { min: dim.min, max: dim.max, step: dim.step };
    }
  }
  if (!Object.keys(dims).length) {
    $("searchStatus").textContent = "Enable at least one numeric filter first.";
    return;
  }
  $("findBestBtn").disabled = true;
  $("searchProgress").hidden = false;
  $("searchProgressFill").style.width = "0%";
  $("searchStatus").textContent = "Searching…";
  const minTrades = Math.max(15, Math.round(state.windows.length * 0.03));
  state.worker.postMessage({ type: "search", baseParams: params, dims, minTrades });
}

function fmtMinClock(min) {
  const total = Math.max(0, Math.round(Number(min) * 60));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function describeParams(params) {
  const parts = [];
  if (params.useLastMinutes) {
    parts.push(`elapsed ${fmtMinClock(params.elapsedFromMin)}–${fmtMinClock(params.elapsedToMin)}`);
  }
  if (params.useOdds) parts.push(`odds ${Number(params.oddsLo).toFixed(2)}–${Number(params.oddsHi).toFixed(2)}`);
  if (params.useSpot) {
    const hi = params.maxDistance == null ? "∞" : Number(params.maxDistance).toFixed(0);
    parts.push(`|Δ| $${Number(params.minDistance).toFixed(0)}–$${hi} (${btcSourceLabel(params.btcSource)})`);
  }
  if (params.useTwap) parts.push("TWAP agrees");
  if (params.useVolume) parts.push(`vol≥${params.minVolume.toFixed(0)}`);
  if (params.useVenues) parts.push(`venues≥${params.minVenues}`);
  return parts.join(" · ") || "no filters";
}

function renderBestResults(results, scanned, total) {
  $("searchStatus").textContent = `scanned ${scanned ?? total} combinations`;
  const body = $("bestBody");
  body.replaceChildren();
  if (!results || !results.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6" class="muted">No combination produced enough trades. Try loosening a filter.</td>`;
    body.appendChild(tr);
    return;
  }
  const frag = document.createDocumentFragment();
  results.forEach((r, i) => {
    const tr = document.createElement("tr");
    const cls = r.summary.netPnl >= 0 ? "up" : "down";
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td class="${cls}">${fmtPnl(r.summary.netPnl)}</td>
      <td>${r.summary.winRate == null ? "—" : (r.summary.winRate * 100).toFixed(1) + "%"}</td>
      <td>${r.summary.trades}</td>
      <td>${describeParams(r.params)}</td>
      <td><button type="button" class="lab-best-apply">Apply</button></td>
    `;
    tr.querySelector(".lab-best-apply").addEventListener("click", () => {
      setParams(r.params);
      scheduleRecompute();
    });
    frag.appendChild(tr);
  });
  body.appendChild(frag);
}

/* ------------------------------------------------------------------ *
 * Tape loading (NDJSON: scan progress, then the full tape)
 * ------------------------------------------------------------------ */

function setDataStatus(text) {
  $("dataStatus").textContent = text;
}

async function loadTape() {
  if (state.loading) return;
  state.loading = true;
  $("errorBox").hidden = true;
  $("rescanBtn").disabled = true;
  setDataStatus("Scanning windows…");
  $("scanProgress").hidden = false;
  $("scanProgressFill").style.width = "0%";
  const rangeDays = $("rangeSel").value;
  const slug = $("slugFilter").value.trim();
  const url = new URL("/api/lab/tape", window.location.origin);
  url.searchParams.set("range_days", rangeDays);
  if (slug) url.searchParams.set("slug", slug);

  try {
    const res = await fetch(url);
    if (!res.ok || !res.body) {
      throw new Error(res.statusText || "Failed to load tape");
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let sawData = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "error") throw new Error(ev.detail || "Tape scan failed");
        if (ev.type === "scan_start") {
          setDataStatus(ev.missing ? `Sampling ${ev.missing} new windows (${ev.cached} cached)…` : `${ev.cached} windows cached.`);
        }
        if (ev.type === "scan_progress") {
          const pct = ev.total ? Math.round((ev.done / ev.total) * 100) : 0;
          $("scanProgressFill").style.width = `${pct}%`;
          setDataStatus(`Sampling windows… ${ev.done} / ${ev.total}`);
        }
        if (ev.type === "scan_done") {
          $("scanProgress").hidden = true;
        }
        if (ev.type === "data") {
          sawData = true;
          state.windows = ev.windows || [];
          state.skipCounts = ev.skip_counts || {};
          const skipTxt = Object.entries(state.skipCounts)
            .map(([k, v]) => `${v} ${k}`)
            .join(", ");
          setDataStatus(`${state.windows.length} windows ready${skipTxt ? " · skipped: " + skipTxt : ""}.`);
          if (state.worker) {
            state.worker.postMessage({ type: "load", windows: state.windows });
          }
          recompute();
          runSweep();
        }
      }
    }
    if (!sawData) throw new Error("Tape stream ended before finishing");
  } catch (err) {
    $("errorBox").hidden = false;
    $("errorBox").textContent = err.message || String(err);
    setDataStatus("Failed to load.");
  } finally {
    state.loading = false;
    $("rescanBtn").disabled = false;
    $("scanProgress").hidden = true;
  }
}

/* ------------------------------------------------------------------ */

function init() {
  wireFilters();
  wireTradesToolbar();
  initWorker();
  $("rangeSel").addEventListener("change", loadTape);
  $("rescanBtn").addEventListener("click", loadTape);
  $("slugFilter").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadTape();
  });
  $("sweepVar").addEventListener("change", runSweep);
  $("sweepChart").addEventListener("click", onSweepClick);
  $("equityBox").addEventListener("mousemove", onEquityMove);
  $("equityBox").addEventListener("mouseleave", hideEquityHover);
  $("findBestBtn").addEventListener("click", findBestSettings);
  loadTape();
}

init();
