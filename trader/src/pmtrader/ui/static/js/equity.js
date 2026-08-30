import { fmtUsd } from "./stats.js";
import { fmtTs } from "./format.js";
import { tradePnl } from "./parse.js";
import {
  windowState,
  saveWindowState,
  collectPeriods,
  resolveWindowBounds,
  filterTradesInWindow,
  buildXAxisLabels,
  anchorsForMode,
  anchorLabel,
  shiftAnchor,
  ensureAnchor,
} from "./equity-window.js";

const RANGE_KEY = "pm-centionaire.analyzer.equity.ranges";
const W = 920;
const H_MAIN = 248;
const H_DD = 54;
const GAP = 10;
const PADL = 64;
const PADR = 18;
const PADT = 16;
const PADB = 36;
const H = PADT + H_MAIN + GAP + H_DD + PADB;
const plotW = W - PADL - PADR;
const plotH = H_MAIN;
const ddTop = PADT + H_MAIN + GAP;
const ddH = H_DD;
const TIP_W = 188;
const TIP_H = 58;

let compareRangeA = null;
let compareRangeB = null;
let dragState = null;
let dragWired = false;
let chartData = null;
let dragUpdate = null;

loadCompareRanges();

function loadCompareRanges() {
  try {
    const raw = localStorage.getItem(RANGE_KEY);
    if (!raw) return;
    const o = JSON.parse(raw);
    compareRangeA = o.a ?? null;
    compareRangeB = o.b ?? null;
  } catch {
    compareRangeA = null;
    compareRangeB = null;
  }
}

function saveCompareRanges() {
  localStorage.setItem(
    RANGE_KEY,
    JSON.stringify({ a: compareRangeA, b: compareRangeB }),
  );
}

function niceStep(raw) {
  if (raw <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const n = raw / pow;
  const nice = n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10;
  return nice * pow;
}

function buildTradePoints(resolved) {
  const ordered = [...resolved].sort((a, b) => (a.entryTs ?? 0) - (b.entryTs ?? 0));
  let cum = 0;
  let peak = 0;
  const pts = [];

  for (const t of ordered) {
    const delta = tradePnl(t) ?? 0;
    cum += delta;
    peak = Math.max(peak, cum);
    pts.push({
      ts: t.entryTs ?? 0,
      y: cum,
      delta,
      won: !!t.won,
      side: t.side,
      peak,
      dd: cum - peak,
    });
  }

  return pts;
}

function longestLossStreak(ordered) {
  let cur = 0;
  let best = 0;
  let start = null;
  let bestStart = null;
  let bestEnd = null;

  for (const t of ordered) {
    if (!t.won) {
      if (cur === 0) start = t.entryTs ?? 0;
      cur++;
      if (cur > best) {
        best = cur;
        bestStart = start;
        bestEnd = t.entryTs ?? 0;
      }
    } else {
      cur = 0;
    }
  }

  return { count: best, start: bestStart, end: bestEnd };
}

function rangeStats(resolved, startTs, endTs) {
  let net = 0;
  let n = 0;
  let wins = 0;
  for (const t of resolved) {
    const ts = t.entryTs ?? 0;
    if (ts < startTs || ts > endTs) continue;
    net += tradePnl(t) ?? 0;
    n++;
    if (t.won) wins++;
  }
  return { net, n, wins };
}

function compareBand(range, color, tag, xOf, xMin, xMax) {
  if (!range) return "";
  let s = Math.min(range.start, range.end);
  let en = Math.max(range.start, range.end);
  s = Math.max(s, xMin);
  en = Math.min(en, xMax);
  if (en <= s) return "";
  const x1 = xOf(s);
  const x2 = xOf(en);
  const w = Math.max(x2 - x1, 2);
  return (
    `<rect x="${x1.toFixed(1)}" y="${PADT}" width="${w.toFixed(1)}" height="${plotH}" fill="${color}" fill-opacity="0.14"/>` +
    `<line x1="${x1.toFixed(1)}" y1="${PADT}" x2="${x1.toFixed(1)}" y2="${(PADT + plotH).toFixed(1)}" stroke="${color}" stroke-width="1" stroke-dasharray="3 3" stroke-opacity="0.75"/>` +
    `<line x1="${x2.toFixed(1)}" y1="${PADT}" x2="${x2.toFixed(1)}" y2="${(PADT + plotH).toFixed(1)}" stroke="${color}" stroke-width="1" stroke-dasharray="3 3" stroke-opacity="0.75"/>` +
    `<text x="${(x1 + 4).toFixed(1)}" y="${(PADT + 12).toFixed(1)}" fill="${color}" font-size="10" font-weight="700">${tag}</text>`
  );
}

function stepPath(pts, xOf, yOf, y0) {
  if (!pts.length) return "";
  let d = `M${xOf(pts[0].ts).toFixed(1)} ${yOf(y0).toFixed(1)}`;
  let prevY = y0;
  for (const p of pts) {
    const x = xOf(p.ts).toFixed(1);
    d += ` L${x} ${yOf(prevY).toFixed(1)} L${x} ${yOf(p.y).toFixed(1)}`;
    prevY = p.y;
  }
  return d;
}

function buildEquitySvg(resolved, bounds) {
  const filtered = filterTradesInWindow(resolved, bounds);
  const ordered = [...filtered].sort((a, b) => (a.entryTs ?? 0) - (b.entryTs ?? 0));
  const pts = buildTradePoints(filtered);
  if (!pts.length) return { empty: true, bounds, filtered };

  const ys = pts.map((p) => p.y);
  const dds = pts.map((p) => p.dd);
  let yMin = Math.min(0, ...ys);
  let yMax = Math.max(0, ...ys);
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  const yPad = (yMax - yMin) * 0.1;
  yMin -= yPad;
  yMax += yPad;

  const xMin = bounds.startTs;
  const xMax = bounds.endTs;
  const xSpan = Math.max(xMax - xMin, 3600);
  const xOf = (ts) => PADL + ((ts - xMin) / xSpan) * plotW;
  const yOf = (y) => PADT + (1 - (y - yMin) / (yMax - yMin)) * plotH;
  const xToTs = (x) => xMin + ((x - PADL) / plotW) * xSpan;

  const streak = longestLossStreak(ordered);
  const finalY = pts[pts.length - 1].y;
  const lineColor = finalY >= 0 ? "#34d399" : "#fb7185";

  let grid = "";
  const yStep = niceStep((yMax - yMin) / 4);
  for (let v = Math.ceil(yMin / yStep) * yStep; v <= yMax; v += yStep) {
    const y = yOf(v);
    grid += `<line x1="${PADL}" y1="${y.toFixed(1)}" x2="${W - PADR}" y2="${y.toFixed(1)}" stroke="#1c2b27"/>`;
    grid += `<text x="${PADL - 8}" y="${(y + 3).toFixed(1)}" text-anchor="end" fill="#5f7468" font-size="10">${fmtUsd(v)}</text>`;
  }

  const y0 = yOf(0);
  grid += `<line x1="${PADL}" y1="${y0.toFixed(1)}" x2="${W - PADR}" y2="${y0.toFixed(1)}" stroke="#3a504a" stroke-width="1.2"/>`;

  const xAxis = buildXAxisLabels(bounds.mode, xMin, xMax, xOf, H - 10, PADT);
  grid += xAxis.grid;

  let streakBand = "";
  if (streak.count > 0 && streak.start != null && streak.end != null) {
    const x1 = xOf(streak.start);
    const x2 = xOf(streak.end);
    const w = Math.max(x2 - x1, 3);
    streakBand =
      `<rect x="${x1.toFixed(1)}" y="${PADT}" width="${w.toFixed(1)}" height="${plotH}" fill="#fb7185" fill-opacity="0.1"/>` +
      `<line x1="${x1.toFixed(1)}" y1="${PADT}" x2="${x1.toFixed(1)}" y2="${(PADT + plotH).toFixed(1)}" stroke="#fb7185" stroke-width="1" stroke-dasharray="3 3" stroke-opacity="0.55"/>` +
      `<line x1="${x2.toFixed(1)}" y1="${PADT}" x2="${x2.toFixed(1)}" y2="${(PADT + plotH).toFixed(1)}" stroke="#fb7185" stroke-width="1" stroke-dasharray="3 3" stroke-opacity="0.55"/>` +
      `<text x="${(x1 + 4).toFixed(1)}" y="${(PADT + 12).toFixed(1)}" fill="#fb7185" font-size="10" font-weight="600">Loss streak · ${streak.count}L</text>`;
  }

  const d = stepPath(pts, xOf, yOf, 0);
  const fill =
    `${d} L${xOf(xMax).toFixed(1)} ${y0.toFixed(1)} L${xOf(xMin).toFixed(1)} ${y0.toFixed(1)} Z`;

  let extrema = "";
  let troughP = pts[0];
  let peakP = pts[0];
  for (const p of pts) {
    if (p.y < troughP.y) troughP = p;
    if (p.y > peakP.y) peakP = p;
  }
  if (troughP.y < -0.001) {
    extrema +=
      `<circle cx="${xOf(troughP.ts).toFixed(1)}" cy="${yOf(troughP.y).toFixed(1)}" r="3.5" fill="#fb7185"/>` +
      `<text x="${(xOf(troughP.ts) + 6).toFixed(1)}" y="${(yOf(troughP.y) + 4).toFixed(1)}" fill="#fb7185" font-size="10">Low ${fmtUsd(troughP.y)}</text>`;
  }
  if (peakP.y > 0.001 && peakP !== pts[pts.length - 1]) {
    extrema +=
      `<circle cx="${xOf(peakP.ts).toFixed(1)}" cy="${yOf(peakP.y).toFixed(1)}" r="3.5" fill="#34d399"/>` +
      `<text x="${(xOf(peakP.ts) + 6).toFixed(1)}" y="${(yOf(peakP.y) - 5).toFixed(1)}" fill="#34d399" font-size="10">High ${fmtUsd(peakP.y)}</text>`;
  }

  const lastTs = pts[pts.length - 1].ts;
  const endLabel =
    `<circle cx="${xOf(lastTs).toFixed(1)}" cy="${yOf(finalY).toFixed(1)}" r="4" fill="${lineColor}"/>` +
    `<text x="${(xOf(lastTs) - 6).toFixed(1)}" y="${(yOf(finalY) - 8).toFixed(1)}" text-anchor="end" fill="${lineColor}" font-size="11" font-weight="700">${fmtUsd(finalY)}</text>`;

  let tradeDots = "";
  for (const p of pts) {
    const c = p.won ? "#34d399" : "#fb7185";
    tradeDots += `<circle cx="${xOf(p.ts).toFixed(1)}" cy="${yOf(p.y).toFixed(1)}" r="2.2" fill="${c}" fill-opacity="0.85"/>`;
  }

  const xlab = xAxis.labels;
  const bands =
    compareBand(compareRangeA, "#60a5fa", "A", xOf, xMin, xMax) +
    compareBand(compareRangeB, "#f472b6", "B", xOf, xMin, xMax);

  const ddMin = Math.min(...dds, 0);
  const ddPad = Math.abs(ddMin) * 0.08;
  const ddFloor = ddMin - ddPad;
  const ddYOf = (v) => ddTop + (1 - (v - ddFloor) / (0 - ddFloor || 1)) * ddH;

  let ddPath = `M${xOf(xMin).toFixed(1)} ${ddYOf(0).toFixed(1)}`;
  for (const p of pts) {
    ddPath += ` L${xOf(p.ts).toFixed(1)} ${ddYOf(p.dd).toFixed(1)}`;
  }
  const ddFill =
    `${ddPath} L${xOf(xMax).toFixed(1)} ${ddYOf(0).toFixed(1)} L${xOf(xMin).toFixed(1)} ${ddYOf(0).toFixed(1)} Z`;

  const dragEls =
    `<rect id="ec-drag-band" x="0" y="${PADT}" width="0" height="${plotH}" fill="#2dd4bf" fill-opacity="0.12" visibility="hidden"/>` +
    `<text id="ec-drag-label" x="0" y="${(PADT + 12).toFixed(1)}" fill="#edf5f1" font-size="10" font-weight="600" visibility="hidden"></text>`;

  const hover =
    `<g id="ec-hover" visibility="hidden">` +
    `<line id="ec-cross-main" x1="0" y1="${PADT}" x2="0" y2="${(PADT + plotH).toFixed(1)}" stroke="#8aa396" stroke-width="1" stroke-dasharray="2 3"/>` +
    `<line id="ec-cross-dd" x1="0" y1="${ddTop}" x2="0" y2="${(ddTop + ddH).toFixed(1)}" stroke="#8aa396" stroke-width="1" stroke-dasharray="2 3"/>` +
    `<circle id="ec-dot" cx="0" cy="0" r="4" fill="#edf5f1" stroke="#060a09" stroke-width="1.5"/>` +
    `<g id="ec-tip">` +
    `<rect id="ec-tip-bg" x="0" y="0" width="${TIP_W}" height="${TIP_H}" rx="8" fill="#0c1210" stroke="#3a504a" stroke-width="1"/>` +
    `<text id="ec-tip-time" x="10" y="18" fill="#8aa396" font-size="11" font-weight="600"></text>` +
    `<text id="ec-tip-cum" x="10" y="34" fill="#edf5f1" font-size="11"></text>` +
    `<text id="ec-tip-trade" x="10" y="48" fill="#34d399" font-size="11"></text>` +
    `</g></g>`;

  const hit =
    `<rect id="ec-hit" x="${PADL}" y="${PADT}" width="${plotW}" height="${plotH + GAP + ddH}" fill="transparent" pointer-events="all"/>`;

  const svg =
    `<svg viewBox="0 0 ${W} ${H}" class="equity-chart" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Cumulative P and L">` +
    `<rect width="${W}" height="${H}" fill="#0a100e" rx="10"/>` +
    streakBand +
    bands +
    `<path d="${fill}" fill="${lineColor}" fill-opacity="0.1"/>` +
    grid +
    `<path d="${d}" fill="none" stroke="${lineColor}" stroke-width="2" stroke-linejoin="round"/>` +
    tradeDots +
    extrema +
    endLabel +
    `<text x="${PADL}" y="${(ddTop - 4).toFixed(1)}" fill="#5f7468" font-size="9" font-weight="600">DRAWDOWN</text>` +
    `<line x1="${PADL}" y1="${ddYOf(0).toFixed(1)}" x2="${W - PADR}" y2="${ddYOf(0).toFixed(1)}" stroke="#1c2b27"/>` +
    `<path d="${ddFill}" fill="#fb7185" fill-opacity="0.2"/>` +
    `<path d="${ddPath}" fill="none" stroke="#fb7185" stroke-width="1.4" stroke-linejoin="round"/>` +
    xlab +
    dragEls +
    hover +
    hit +
    `</svg>`;

  return {
    svg,
    pts,
    resolved: filtered,
    allResolved: resolved,
    bounds,
    xOf,
    yOf,
    xToTs,
    xMin,
    xMax,
    W,
    H,
    PADL,
    PADR,
    PADT,
    plotH,
    ddTop,
    ddH,
  };
}

function compareStatsHtml(resolved) {
  const parts = [];
  if (compareRangeA) {
    const s = Math.min(compareRangeA.start, compareRangeA.end);
    const e = Math.max(compareRangeA.start, compareRangeA.end);
    const st = rangeStats(resolved, s, e);
    parts.push(`<span class="cmp-a"><strong>A</strong> ${fmtUsd(st.net)} · ${st.n} trades · ${st.wins}W</span>`);
  }
  if (compareRangeB) {
    const s = Math.min(compareRangeB.start, compareRangeB.end);
    const e = Math.max(compareRangeB.start, compareRangeB.end);
    const st = rangeStats(resolved, s, e);
    parts.push(`<span class="cmp-b"><strong>B</strong> ${fmtUsd(st.net)} · ${st.n} trades · ${st.wins}W</span>`);
  }
  if (!parts.length) return "";
  return `<div class="equity-compare">${parts.join('<span class="cmp-sep">·</span>')}<button type="button" class="equity-clear-ranges" aria-label="Clear compare ranges">×</button></div>`;
}

function wireHover(container, data) {
  const svg = container.querySelector("svg.equity-chart");
  if (!svg) return;

  const hover = svg.querySelector("#ec-hover");
  const crossMain = svg.querySelector("#ec-cross-main");
  const crossDd = svg.querySelector("#ec-cross-dd");
  const dot = svg.querySelector("#ec-dot");
  const tipBg = svg.querySelector("#ec-tip-bg");
  const tipTime = svg.querySelector("#ec-tip-time");
  const tipCum = svg.querySelector("#ec-tip-cum");
  const tipTrade = svg.querySelector("#ec-tip-trade");
  const hit = svg.querySelector("#ec-hit");
  if (!hover || !crossMain || !dot || !tipBg || !hit) return;

  const { pts, xOf, yOf, ddTop, ddH, W, PADL, PADR, PADT, plotH } = data;

  const ddYOf = (v) => {
    const dds = pts.map((p) => p.dd);
    const ddMin = Math.min(...dds, 0);
    const ddFloor = ddMin - Math.abs(ddMin) * 0.08;
    return ddTop + (1 - (v - ddFloor) / (0 - ddFloor || 1)) * ddH;
  };

  function toSvg(clientX, clientY) {
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const p = svg.createSVGPoint();
    p.x = clientX;
    p.y = clientY;
    return p.matrixTransform(ctm.inverse());
  }

  function nearest(svgX) {
    let best = pts[0];
    let bestDx = Infinity;
    for (const p of pts) {
      const dx = Math.abs(xOf(p.ts) - svgX);
      if (dx < bestDx) {
        bestDx = dx;
        best = p;
      }
    }
    return best;
  }

  function show(clientX, clientY) {
    const sp = toSvg(clientX, clientY);
    if (!sp || sp.x < PADL || sp.x > W - PADR) {
      hover.setAttribute("visibility", "hidden");
      return;
    }

    const p = nearest(sp.x);
    const px = xOf(p.ts);
    const py = yOf(p.y);

    crossMain.setAttribute("x1", px.toFixed(1));
    crossMain.setAttribute("x2", px.toFixed(1));
    if (crossDd) {
      crossDd.setAttribute("x1", px.toFixed(1));
      crossDd.setAttribute("x2", px.toFixed(1));
    }
    dot.setAttribute("cx", px.toFixed(1));
    dot.setAttribute("cy", py.toFixed(1));

    tipTime.textContent = fmtTs(p.ts);
    tipCum.textContent = `Cumulative ${fmtUsd(p.y)}`;
    const tradeColor = p.delta >= 0 ? "#34d399" : "#fb7185";
    tipTrade.setAttribute("fill", tradeColor);
    const side = (p.side ?? "").toUpperCase();
    tipTrade.textContent = `Trade ${fmtUsd(p.delta)} · ${p.won ? "W" : "L"}${side ? ` · ${side}` : ""} · DD ${fmtUsd(p.dd)}`;

    let tx = px + 12;
    let ty = py - TIP_H - 10;
    if (tx + TIP_W > W - PADR) tx = px - TIP_W - 12;
    if (tx < PADL) tx = PADL;
    if (ty < PADT) ty = py + 12;
    if (ty + TIP_H > PADT + plotH) ty = PADT + plotH - TIP_H - 8;

    tipBg.setAttribute("x", tx.toFixed(1));
    tipBg.setAttribute("y", ty.toFixed(1));
    tipTime.setAttribute("x", (tx + 10).toFixed(1));
    tipTime.setAttribute("y", (ty + 18).toFixed(1));
    tipCum.setAttribute("x", (tx + 10).toFixed(1));
    tipCum.setAttribute("y", (ty + 34).toFixed(1));
    tipTrade.setAttribute("x", (tx + 10).toFixed(1));
    tipTrade.setAttribute("y", (ty + 48).toFixed(1));

    hover.setAttribute("visibility", "visible");
  }

  function hide() {
    hover.setAttribute("visibility", "hidden");
  }

  hit.addEventListener("mousemove", (e) => show(e.clientX, e.clientY));
  hit.addEventListener("mouseleave", hide);
  hit.addEventListener("touchmove", (e) => {
    if (e.touches[0]) show(e.touches[0].clientX, e.touches[0].clientY);
  }, { passive: true });
  hit.addEventListener("touchend", hide);
}

function svgClientToX(clientX) {
  const svg = document.querySelector("svg.equity-chart");
  if (!svg || !chartData) return null;
  const ctm = svg.getScreenCTM();
  if (!ctm) return null;
  const p = svg.createSVGPoint();
  p.x = clientX;
  p.y = 0;
  return p.matrixTransform(ctm.inverse()).x;
}

function dragOnDown(e) {
  if (!chartData || e.button === 2) return;
  const x = svgClientToX(e.clientX);
  if (x == null || x < chartData.PADL || x > chartData.W - chartData.PADR) return;
  dragState = {
    which: e.shiftKey ? "b" : "a",
    startTs: chartData.xToTs(x),
    currentTs: chartData.xToTs(x),
  };
  e.preventDefault?.();
}

function dragOnMove(e) {
  if (!dragState || !chartData) return;
  const svg = document.querySelector("svg.equity-chart");
  if (!svg) return;
  const band = svg.querySelector("#ec-drag-band");
  const label = svg.querySelector("#ec-drag-label");
  if (!band || !label) return;

  const x = svgClientToX(e.clientX);
  if (x == null) return;
  dragState.currentTs = chartData.xToTs(x);

  let s = dragState.startTs;
  let en = dragState.currentTs;
  if (s > en) [s, en] = [en, s];

  const { xOf, PADT, plotH, resolved } = chartData;
  const x1 = xOf(s);
  const x2 = xOf(en);
  band.setAttribute("x", x1.toFixed(1));
  band.setAttribute("width", Math.max(x2 - x1, 2).toFixed(1));
  band.setAttribute("visibility", "visible");

  const st = rangeStats(resolved, s, en);
  label.setAttribute("x", (x1 + 4).toFixed(1));
  label.setAttribute("y", (PADT + 12).toFixed(1));
  label.setAttribute("visibility", "visible");
  label.textContent = `${dragState.which.toUpperCase()} · ${fmtTs(s)} – ${fmtTs(en)} · ${fmtUsd(st.net)} · ${st.n} trades`;
}

function dragOnUp() {
  if (!dragState) return;
  let s = dragState.startTs;
  let en = dragState.currentTs;
  if (s > en) [s, en] = [en, s];
  if (Math.abs(en - s) > 30) {
    if (dragState.which === "a") compareRangeA = { start: s, end: en };
    else compareRangeB = { start: s, end: en };
    saveCompareRanges();
  }
  dragState = null;

  const svg = document.querySelector("svg.equity-chart");
  if (svg) {
    svg.querySelector("#ec-drag-band")?.setAttribute("visibility", "hidden");
    svg.querySelector("#ec-drag-label")?.setAttribute("visibility", "hidden");
  }
  dragUpdate?.();
}

function wireDrag(onUpdate) {
  dragUpdate = onUpdate;
  const svg = document.querySelector("svg.equity-chart");
  if (!svg) return;
  svg.addEventListener("mousedown", dragOnDown);
  svg.addEventListener("touchstart", (e) => {
    if (e.touches[0]) dragOnDown(e.touches[0]);
  }, { passive: false });

  if (dragWired) return;
  dragWired = true;
  document.addEventListener("mousemove", dragOnMove);
  document.addEventListener("mouseup", dragOnUp);
  document.addEventListener("touchmove", (e) => {
    if (e.touches[0]) dragOnMove(e.touches[0]);
  }, { passive: false });
  document.addEventListener("touchend", dragOnUp);
}

function windowControlsHtml(periods) {
  const modes = [
    { id: "all", label: "All" },
    { id: "day", label: "Day" },
    { id: "week", label: "Week" },
    { id: "month", label: "Month" },
  ];
  const modeBtns = modes
    .map(
      (m) =>
        `<button type="button" class="seg-btn${windowState.mode === m.id ? " is-active" : ""}" data-eq-mode="${m.id}">${m.label}</button>`,
    )
    .join("");

  const anchors = anchorsForMode(windowState.mode, periods);
  const showNav = windowState.mode !== "all" && anchors.length > 0;
  const options = anchors
    .map(
      (a) =>
        `<option value="${a}"${windowState.anchor === a ? " selected" : ""}>${anchorLabel(windowState.mode, a)}</option>`,
    )
    .join("");

  return (
    `<div class="equity-window">` +
    `<div class="seg-track equity-window-modes" role="group" aria-label="Chart time range">${modeBtns}</div>` +
    (showNav
      ? `<div class="equity-window-nav">` +
        `<button type="button" class="eq-nav-btn" data-eq-nav="-1" aria-label="Previous period">‹</button>` +
        `<select class="eq-anchor-select" aria-label="Period">${options}</select>` +
        `<button type="button" class="eq-nav-btn" data-eq-nav="1" aria-label="Next period">›</button>` +
        `</div>`
      : "") +
    `</div>`
  );
}

function windowSummary(filtered, bounds) {
  const net = filtered.reduce((s, t) => s + (tradePnl(t) ?? 0), 0);
  return `${bounds.label} · ${filtered.length} trade${filtered.length === 1 ? "" : "s"} · ${fmtUsd(net)}`;
}

function wireWindowControls(container, periods, onUpdate) {
  container.querySelectorAll("[data-eq-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      windowState.mode = btn.dataset.eqMode;
      ensureAnchor(windowState.mode, periods);
      saveWindowState();
      onUpdate?.();
    });
  });

  container.querySelector("[data-eq-nav='-1']")?.addEventListener("click", () => {
    windowState.anchor = shiftAnchor(windowState.mode, windowState.anchor, 1, periods);
    saveWindowState();
    onUpdate?.();
  });

  container.querySelector("[data-eq-nav='1']")?.addEventListener("click", () => {
    windowState.anchor = shiftAnchor(windowState.mode, windowState.anchor, -1, periods);
    saveWindowState();
    onUpdate?.();
  });

  container.querySelector(".eq-anchor-select")?.addEventListener("change", (e) => {
    windowState.anchor = e.target.value;
    saveWindowState();
    onUpdate?.();
  });
}

/** Mount interactive equity chart into container. Returns false if nothing to plot. */
export function mountEquityChart(container, resolved, onUpdate) {
  if (!container || !resolved.length) {
    if (container) container.innerHTML = "";
    chartData = null;
    return false;
  }

  const periods = collectPeriods(resolved);
  ensureAnchor(windowState.mode, periods);
  const bounds = resolveWindowBounds(resolved, periods);
  if (!bounds) {
    container.innerHTML = "";
    chartData = null;
    return false;
  }

  const built = buildEquitySvg(resolved, bounds);
  const filtered = built.empty ? [] : filterTradesInWindow(resolved, bounds);
  const summary = windowSummary(filtered, bounds);
  const controls = windowControlsHtml(periods);

  if (built.empty) {
    container.innerHTML =
      `<div class="equity-head">${controls}<p class="equity-summary">${bounds.label}</p><p class="chart-empty">No trades in this period.</p></div>`;
    wireWindowControls(container, periods, onUpdate);
    chartData = null;
    return true;
  }

  chartData = built;
  const stats = compareStatsHtml(resolved);
  container.innerHTML =
    `<div class="equity-head">${controls}<p class="equity-summary">${summary}</p><span class="equity-hint">Hover · drag A · shift+drag B</span></div>` +
    built.svg +
    stats;

  wireWindowControls(container, periods, onUpdate);
  wireHover(container, built);
  wireDrag(onUpdate);

  container.querySelector(".equity-clear-ranges")?.addEventListener("click", () => {
    compareRangeA = null;
    compareRangeB = null;
    saveCompareRanges();
    dragUpdate?.();
  });

  return true;
}

/** @deprecated use mountEquityChart */
export function buildEquityCurve(resolved) {
  const periods = collectPeriods(resolved);
  const bounds = resolveWindowBounds(resolved, periods);
  if (!bounds) return null;
  const built = buildEquitySvg(resolved, bounds);
  return built.empty ? null : built.svg;
}
