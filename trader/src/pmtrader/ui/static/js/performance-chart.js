import { tradePnl, effectiveWon } from './parse.js?v=19';
import { fmtUsd } from './stats.js?v=19';
import { windowState, saveWindowState, collectPeriods, resolveWindowBounds, filterTradesInWindow, anchorsForMode, anchorLabel, ensureAnchor } from './equity-window.js';

const esc = v => String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const money = v => fmtUsd(Math.abs(v) < .005 ? 0 : v);
const stamp = ts => new Date(ts * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const axisMoney = v => `${v < 0 ? '−' : ''}$${Math.abs(v) >= 1000 ? `${(Math.abs(v) / 1000).toFixed(1)}k` : Math.abs(v).toFixed(0)}`;
const state = { metric: 'net', selected: null, compare: false, a: null, b: null, key: '', dataKey: '' };
let host, model, update, observer;

/** Each step is an actual closed fill; no interpolation or hypothetical orders. */
export function performancePoints(trades) {
  let net = 0, peak = 0, maxDip = 0, wins = 0, streak = 0, longestLoss = 0;
  const points = [...trades].sort((a,b) => (a.entryTs || 0) - (b.entryTs || 0)).map((trade, i) => {
    const delta = tradePnl(trade) ?? 0;
    net += delta;
    peak = Math.max(peak, net);
    const drawdown = net - peak;
    maxDip = Math.min(maxDip, drawdown);
    const won = effectiveWon(trade) === true;
    if (won) { wins++; streak = 0; } else { streak++; longestLoss = Math.max(longestLoss, streak); }
    return { id: String(trade.oid || i), ts: trade.entryTs || 0, net, drawdown, delta, won };
  });
  return { points, net, maxDip, wins, longestLoss };
}

function persistWindow() { try { saveWindowState(); } catch { /* Browsing with storage disabled still works. */ } }

export function mountEquityChart(container, resolved, onUpdate) {
  if (!container) return false;
  host = container;
  update = onUpdate;
  const periods = collectPeriods(resolved);
  if (!['all', 'day', 'week', 'month'].includes(windowState.mode)) windowState.mode = 'all';
  ensureAnchor(windowState.mode, periods);
  const bounds = resolveWindowBounds(resolved, periods);
  if (!bounds) return false;
  const key = `${bounds.mode}:${bounds.anchor || ''}`;
  const data = performancePoints(filterTradesInWindow(resolved, bounds));
  const dataKey = JSON.stringify(data.points.map(p => [p.id, p.ts, p.delta, p.won]));
  const windowChanged = key !== state.key;
  if (windowChanged) { state.selected = null; state.a = null; state.b = null; }
  model = { ...data, bounds, periods };
  if (state.selected != null && !data.points.some(p => p.id === state.selected)) state.selected = null;
  if (!windowChanged && state.dataKey === dataKey && host.querySelector('.pc-chart')) return true;
  state.key = key;
  state.dataKey = dataKey;
  render();
  if (!observer) {
    observer = new ResizeObserver(() => { if (host?.clientWidth) draw(); });
    observer.observe(container);
  }
  return !!data.points.length;
}

function render() {
  const anchors = anchorsForMode(windowState.mode, model.periods);
  const index = anchors.indexOf(windowState.anchor);
  const n = model.points.length;
  const split = Math.max(1, Math.floor(n / 2));
  state.a ||= [0, Math.max(0, split - 1)];
  state.b ||= [Math.min(split, n - 1), n - 1];
  for (const range of [state.a, state.b]) { range[0] = Math.max(0, Math.min(n - 1, range[0])); range[1] = Math.max(range[0], Math.min(n - 1, range[1])); }
  host.innerHTML = `
    <div class="pc-periods" role="group" aria-label="Chart time range">${[['day','Day'],['week','Week'],['month','Month'],['all','All time']].map(([id,label]) => `<button type="button" data-eq-mode="${id}" aria-pressed="${windowState.mode === id}">${label}</button>`).join('')}</div>
    ${anchors.length ? `<div class="pc-period-nav"><button type="button" data-period-step="1" aria-label="Previous period" ${index >= anchors.length - 1 ? 'disabled' : ''}>‹</button><select aria-label="Period">${anchors.map(a => `<option value="${esc(a)}" ${a === windowState.anchor ? 'selected' : ''}>${esc(anchorLabel(windowState.mode,a))}</option>`).join('')}</select><button type="button" data-period-step="-1" aria-label="Next period" ${index <= 0 ? 'disabled' : ''}>›</button></div>` : ''}
    <div class="pc-readout"><div><span class="pc-eyebrow" data-readout-label></span><strong data-readout-value></strong></div><div class="pc-point-note" data-point-note></div></div>
    <div class="pc-chart" ${n ? '' : 'hidden'}></div>
    ${n ? `<div class="pc-inspect"><label class="pc-scrubber-label"><span class="sr-chart">Inspect a trade</span><input type="range" data-scrubber aria-label="Inspect a trade" min="0" max="${n - 1}" step="1" value="${n - 1}"></label><button type="button" data-latest>Latest</button></div>
    <div class="pc-metrics"><span><b>${n}</b> trades</span><span><b>${Math.round(model.wins / n * 100)}%</b> won</span><span>Worst dip <b class="down">${money(model.maxDip)}</b></span></div>` : '<p class="pc-empty">No closed trades in this period.</p>'}
    <div class="pc-toolbar"><div role="group" aria-label="Chart metric"><button type="button" data-metric="net" aria-pressed="${state.metric === 'net'}">Profit</button><button type="button" data-metric="drawdown" aria-pressed="${state.metric === 'drawdown'}">Drawdown</button></div><button type="button" data-compare aria-expanded="${state.compare}" ${n < 2 ? 'disabled' : ''}>Compare ${state.compare ? '−' : '+'}</button></div>
    <div class="pc-comparison" ${state.compare && n > 1 ? '' : 'hidden'}>
      <p>Choose two trade ranges in this period.</p>
      ${['a','b'].map((id,i) => `<fieldset><legend>Range ${id.toUpperCase()}</legend><div class="pc-range-readout" data-range-result="${id}"></div>${['From','To'].map((label,j) => `<label>${label}<input type="range" data-range="${id}" data-end="${j}" aria-label="Range ${id.toUpperCase()} ${label.toLowerCase()} trade" min="0" max="${n - 1}" value="${state[id][j]}"></label>`).join('')}</fieldset>`).join('')}
      <p class="pc-compare-delta"></p>
      <p class="pc-comparison-foot">Net profit after fees · longest losing streak: ${model.longestLoss} trades</p>
    </div>
    <p class="pc-footnote">${state.metric === 'net' ? 'Net profit after fees · starts at $0 for each period' : 'Drawdown = dollars below the previous profit peak'}<br>Trade entry time · local time</p>`;
  wire();
  draw();
}

function selectedIndex() {
  const found = model.points.findIndex(p => p.id === state.selected);
  return found < 0 ? model.points.length - 1 : found;
}

function readout() {
  const i = selectedIndex(), p = model.points[i], inspection = state.selected != null && p;
  const value = p ? p[state.metric] : 0;
  host.querySelector('[data-readout-label]').textContent = state.metric === 'net' ? inspection ? 'Profit at this trade' : `${model.bounds.label} · net profit` : inspection ? 'Drawdown at this trade' : 'Current drawdown';
  const amount = host.querySelector('[data-readout-value]');
  amount.textContent = money(value);
  amount.className = value < -.005 ? 'down' : 'up';
  host.querySelector('[data-point-note]').innerHTML = inspection ? `<span>${esc(stamp(p.ts))}</span><strong class="${p.delta < 0 ? 'down' : 'up'}">${money(p.delta)} this trade</strong>` : '<span>Tap the line to inspect</span><span>or use the slider below</span>';
  const slider = host.querySelector('[data-scrubber]');
  if (slider && p) { slider.value = i; slider.setAttribute('aria-valuetext', `${stamp(p.ts)}, ${state.metric === 'net' ? 'net profit' : 'drawdown'} ${money(value)}, trade ${money(p.delta)}`); }
  const latest = host.querySelector('[data-latest]');
  if (latest) latest.disabled = !inspection;
}

function draw() {
  if (!host?.querySelector('.pc-readout') || !model) return;
  readout();
  const plot = host.querySelector('.pc-chart');
  if (!plot || !model.points.length || !host.clientWidth) return;
  const width = Math.max(220, host.clientWidth), height = document.getElementById('equity-card').classList.contains('is-expanded') ? 280 : 178;
  const left = 5, right = 53, top = 13, bottom = 25, pw = width - left - right, ph = height - top - bottom;
  const points = model.points;
  const values = points.map(p => p[state.metric]);
  let low = Math.min(0, ...values), high = Math.max(0, ...values);
  if (high - low < 1) { low -= .5; high += .5; }
  const pad = (high - low) * .13; low -= pad; high += pad;
  const first = model.bounds.startTs, last = Math.max(first + 1, model.bounds.endTs);
  const x = ts => left + (ts - first) / (last - first) * pw;
  const y = value => top + (high - value) / (high - low) * ph;
  const zero = y(0);
  let line = `M${left},${zero}`;
  for (const p of points) line += `H${x(p.ts)}V${y(p[state.metric])}`;
  const color = state.metric === 'drawdown' || model.net < 0 ? '#fb7185' : '#34d399';
  const min = Math.min(0,...values), max = Math.max(0,...values);
  // Always show the zero baseline; omit nearby labels instead of overlapping them.
  const levels = [0, max, min, (max + min) / 2];
  const unique = [];
  for (const v of levels) if (!unique.some(other => Math.abs(y(other) - y(v)) < 25)) unique.push(v);
  const grid = unique.map(v => `<line x1="${left}" x2="${left + pw}" y1="${y(v)}" y2="${y(v)}" stroke="${v === 0 ? '#506758' : '#25392d'}" stroke-dasharray="${v === 0 ? '4 4' : '2 5'}"/><text x="${width - 1}" y="${y(v) + 4}" text-anchor="end">${axisMoney(v)}</text>`).join('');
  const tickText = ts => new Date(ts * 1000).toLocaleString(undefined, last - first <= 86400 * 1.1 ? {hour:'2-digit',minute:'2-digit'} : {month:'short',day:'numeric'});
  const ticks = [first,(first+last)/2,last].map((ts,i) => `<text x="${x(ts)}" y="${height - 4}" text-anchor="${['start','middle','end'][i]}">${esc(tickText(ts))}</text>`).join('');
  const bands = state.compare ? ['a','b'].map((id,i) => { const range = state[id], a = points[range[0]], b = points[range[1]]; if (!a || !b) return ''; return `<rect x="${x(a.ts)}" y="${top}" width="${Math.max(2,x(b.ts)-x(a.ts))}" height="${ph}" fill="${i ? '#67b5ff' : '#fbbf24'}" opacity=".12"/>`; }).join('') : '';
  plot.innerHTML = `<svg class="equity-chart pc-svg" viewBox="0 0 ${width} ${height}" style="height:${height}px" role="img" aria-label="${state.metric === 'net' ? 'Cumulative net profit' : 'Drawdown from profit peak'} for ${esc(model.bounds.label)}"><defs><linearGradient id="pc-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="${color}" stop-opacity=".2"/><stop offset="1" stop-color="${color}" stop-opacity=".015"/></linearGradient></defs>${grid}${bands}<path d="${line}V${zero}H${left}Z" fill="url(#pc-fill)"/><path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/><g data-cursor></g>${ticks}</svg>`;
  model.geometry = { x, y, top, ph, width, first, last, left, pw, color };
  paintCursor();
  paintComparison();
}

function paintCursor() {
  const g = model.geometry, p = model.points[selectedIndex()];
  const cursor = host.querySelector('[data-cursor]');
  if (!cursor || !g || !p) return;
  cursor.innerHTML = `${state.selected != null ? `<line x1="${g.x(p.ts)}" x2="${g.x(p.ts)}" y1="${g.top}" y2="${g.top + g.ph}" stroke="#b0c6b7" stroke-dasharray="3 4"/>` : ''}<circle cx="${g.x(p.ts)}" cy="${g.y(p[state.metric])}" r="4" fill="${g.color}" stroke="#0d1612" stroke-width="2"/>`;
}

function paintComparison() {
  if (!state.compare || model.points.length < 2) return;
  const nets = [];
  for (const id of ['a','b']) {
    const [start,end] = state[id], points = model.points.slice(start,end+1);
    const net = points.reduce((s,p) => s + p.delta, 0);
    nets.push(net);
    host.querySelector(`[data-range-result="${id}"]`).innerHTML = `<strong class="${net < 0 ? 'down' : 'up'}">${money(net)}</strong><span>${points.length} ${points.length === 1 ? 'trade' : 'trades'} · ${points.filter(p => p.won).length} wins</span><small>Average ${money(net / points.length)} per trade</small><small>${esc(stamp(points[0].ts))} → ${esc(stamp(points.at(-1).ts))}</small>`;
    host.querySelectorAll(`[data-range="${id}"]`).forEach(slider => { slider.value = state[id][Number(slider.dataset.end)]; slider.setAttribute('aria-valuetext', stamp(model.points[Number(slider.value)].ts)); });
  }
  host.querySelector('.pc-compare-delta').textContent = `B minus A: ${money(nets[1] - nets[0])} · ranges can contain different numbers of trades`;
}

function inspect(index) {
  state.selected = model.points[index]?.id ?? null;
  readout();
  paintCursor();
}

function wire() {
  host.querySelectorAll('[data-eq-mode]').forEach(button => button.addEventListener('click', () => { windowState.mode = button.dataset.eqMode; windowState.anchor = null; persistWindow(); update?.(); }));
  host.querySelector('.pc-period-nav select')?.addEventListener('change', e => { windowState.anchor = e.target.value; persistWindow(); update?.(); });
  host.querySelectorAll('[data-period-step]').forEach(button => button.addEventListener('click', () => {
    const anchors = anchorsForMode(windowState.mode, model.periods), index = anchors.indexOf(windowState.anchor) + Number(button.dataset.periodStep);
    if (anchors[index]) { windowState.anchor = anchors[index]; persistWindow(); update?.(); }
  }));
  host.querySelectorAll('[data-metric]').forEach(button => button.addEventListener('click', () => { state.metric = button.dataset.metric; render(); }));
  host.querySelector('[data-latest]')?.addEventListener('click', () => { state.selected = null; readout(); paintCursor(); });
  host.querySelector('[data-scrubber]')?.addEventListener('input', e => inspect(Number(e.target.value)));
  host.querySelector('[data-compare]').addEventListener('click', () => { state.compare = !state.compare; render(); });
  host.querySelectorAll('[data-range]').forEach(slider => slider.addEventListener('input', () => {
    const id = slider.dataset.range, end = Number(slider.dataset.end);
    state[id][end] = Number(slider.value);
    if (state[id][0] > state[id][1]) state[id][1 - end] = state[id][end];
    draw();
  }));
  const plot = host.querySelector('.pc-chart');
  let dragging = false;
  function pick(e) {
    if (!model.geometry || !model.points.length) return;
    const rect = plot.getBoundingClientRect(), g = model.geometry;
    const ts = g.first + ((e.clientX - rect.left - g.left) / g.pw) * (g.last - g.first);
    let nearest = 0;
    for (let i = 1; i < model.points.length; i++) if (Math.abs(model.points[i].ts - ts) < Math.abs(model.points[nearest].ts - ts)) nearest = i;
    inspect(nearest);
  }
  plot?.addEventListener('pointerdown', e => { if (e.button !== 0) return; dragging = true; pick(e); });
  plot?.addEventListener('pointermove', e => { if (dragging || e.pointerType === 'mouse') pick(e); });
  for (const event of ['pointerup','pointercancel','pointerleave']) plot?.addEventListener(event, () => { dragging = false; });
}

export function initEquityExpand() {
  const card = document.getElementById('equity-card'), button = document.getElementById('equity-expand-btn');
  if (!card || !button) return;
  const placeholder = document.createComment('Chart position');
  const dialog = document.createElement('dialog');
  dialog.className = 'pc-dialog';
  dialog.setAttribute('aria-labelledby','performance-chart-title');
  document.body.append(dialog);
  function open() {
    if (dialog.open) return;
    card.before(placeholder);
    dialog.append(card);
    card.classList.add('is-expanded');
    button.textContent = 'Close'; button.setAttribute('aria-expanded','true');
    dialog.showModal();
    if (!location.hash.includes('/chart')) history.pushState(null,'','#stats/chart');
    draw();
  }
  dialog.addEventListener('close', () => {
    placeholder.replaceWith(card);
    card.classList.remove('is-expanded');
    button.textContent = 'Expand'; button.setAttribute('aria-expanded','false');
    if (location.hash.includes('/chart')) history.replaceState(null,'','#stats');
    button.focus({preventScroll:true});
    draw();
  });
  button.addEventListener('click', () => dialog.open ? dialog.close() : open());
  window.addEventListener('hashchange', () => { if (location.hash.includes('/chart')) open(); else if (dialog.open) dialog.close(); });
  if (location.hash.includes('/chart')) open();
}
