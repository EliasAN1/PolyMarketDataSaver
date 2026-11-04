import { tradePnl, effectiveWon, isStatClosed, countsInStats } from './parse.js?v=19';
import { fmtUsd } from './stats.js?v=19';
import { fmtTs, fmtOdds, slugLabel, slugUrl, outcomeBadge } from './format.js?v=19';
import { selectTab } from './tabs.js?v=23';

export const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
let registry = new Map();
let tradeKeys = new WeakMap();
let sheetKind = null;
let checksObserver;

export function registerTrades(trades) {
  registry = new Map(trades.map((t, i) => [String(t.oid || `trade-${i}`), t]));
  tradeKeys = new WeakMap([...registry].map(([key, t]) => [t, key]));
}

function keyFor(t) {
  return tradeKeys.get(t) || '';
}

export function compactTradeHtml(t) {
  const pnl = tradePnl(t);
  const closed = isStatClosed(t);
  const badge = outcomeBadge(t);
  const result = t.cashedOut ? 'CASHED OUT' : badge.label;
  const side = String(t.side || '').toLowerCase();
  const label = slugLabel(t.slug).split(' · ')[0];
  const value = t.rejected ? 'Rejected' : closed && pnl != null ? fmtUsd(pnl) : t.unmatched ? 'No fill' : 'Open';
  const tone = closed && pnl != null ? pnl >= 0 ? 'up' : 'down' : '';
  return `<button type="button" class="compact-trade" data-trade-detail="${esc(keyFor(t))}">
    <span class="trade-direction ${side === 'down' ? 'down' : 'up'}" aria-label="${esc(side)}">${side === 'down' ? '↘' : '↗'}</span>
    <span class="trade-description"><strong>${esc(label)}</strong><small>${esc(fmtTs(t.entryTs))} · ${esc(fmtOdds(t.fillPrice))}</small></span>
    <span class="trade-result"><strong class="mono ${tone}">${esc(value)}</strong><small>${t.unmatched && closed ? 'NO FILL · ' : ''}${esc(result)}</small></span>
  </button>`;
}

export function renderCompactOverview(trades, liveTrades = trades) {
  registerTrades([...(liveTrades || []), ...(trades || [])]);
  const recent = [...trades].sort((a, b) => (b.entryTs || 0) - (a.entryTs || 0)).slice(0, 5);
  const real = recent.filter(t => isStatClosed(t) && countsInStats(t));
  const net = real.reduce((sum, t) => sum + (tradePnl(t) || 0), 0);
  const wins = real.filter(t => effectiveWon(t)).length;
  const host = document.getElementById('overview-recent');
  if (host) host.innerHTML = `<div class="compact-section-head"><div><h3>Recent results <strong class="recent-net mono ${net >= 0 ? 'up' : 'down'}">${fmtUsd(net)}</strong></h3><p>Last ${recent.length} orders · ${wins}W / ${real.length - wins}L</p></div><button class="text-action" type="button" data-go-tab="trades">All trades →</button></div>
    ${recent.length ? recent.slice(0, 3).map(compactTradeHtml).join('') : '<p class="compact-empty">Your trades will appear here after the first order.</p>'}`;
  const settled = [...liveTrades].filter(t => isStatClosed(t) && countsInStats(t)).sort((a,b) => (b.windowEnd || b.entryTs) - (a.windowEnd || a.entryTs))[0];
  const live = document.getElementById('live-recent');
  if (live) live.innerHTML = `<div class="compact-section-head"><h3>Last settled trade</h3><button class="text-action" type="button" data-go-tab="trades">All trades →</button></div>${settled ? compactTradeHtml(settled) : '<p class="compact-empty">No settled trades yet.</p>'}`;
}

function notifyNativeSheet(open) {
  try {
    window.Centionaire?.setSheetOpen?.(!!open);
  } catch {
    /* desktop browsers have no bridge */
  }
}

function showSheet(title, content, kind) {
  const dialog = document.getElementById('detail-sheet');
  const body = document.getElementById('sheet-content');
  const opening = !dialog.open;
  document.getElementById('sheet-title').textContent = title;
  body.innerHTML = content;
  sheetKind = kind;
  if (opening) {
    dialog.showModal();
    body.scrollTop = 0;
  }
  notifyNativeSheet(true);
}

function refreshOpenChecks() {
  const dialog = document.getElementById('detail-sheet');
  const body = document.getElementById('sheet-content');
  if (!dialog?.open || sheetKind !== 'checks' || !body) return;
  const html = checksHtml();
  if (body.innerHTML === html) return;
  const top = body.scrollTop;
  body.innerHTML = html;
  body.scrollTop = top;
}

function checksHtml() {
  const grid = document.getElementById('radar-checks-grid');
  return `<p class="sheet-subtitle">${esc(document.getElementById('checklist-summary-badge')?.textContent)}</p><div class="sheet-checks">${grid?.innerHTML || '<p>Waiting for live data.</p>'}</div>`;
}

export function initCompactUI() {
  const dialog = document.getElementById('detail-sheet');
  const close = () => dialog.close();
  dialog.querySelector('[data-sheet-close]').addEventListener('click', close);
  dialog.addEventListener('click', e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close(); } });
  dialog.addEventListener('close', () => { sheetKind = null; notifyNativeSheet(false); });
  dialog.addEventListener('cancel', () => notifyNativeSheet(false));
  notifyNativeSheet(false);
  document.addEventListener('ui:checks', () => showSheet('Entry checks', checksHtml(), 'checks'));
  checksObserver = new MutationObserver(() => refreshOpenChecks());
  checksObserver.observe(document.getElementById('strategy-checklist'), { childList: true, subtree: true });
  document.addEventListener('click', e => {
    const nav = e.target.closest('[data-go-tab]');
    if (nav) { selectTab(nav.dataset.goTab); window.scrollTo({ top: 0 }); }
    const button = e.target.closest('[data-trade-detail]');
    if (!button) return;
    const t = registry.get(button.dataset.tradeDetail);
    if (!t) return;
    const pnl = tradePnl(t);
    const details = [
      ['Market', slugLabel(t.slug)], ['Entered', fmtTs(t.entryTs)], ['Side', t.side || '—'],
      ['Outcome', t.cashedOut ? 'Cashed out' : outcomeBadge(t).label], ['Fill price', fmtOdds(t.fillPrice)],
      ['Shares', t.fillShares ?? '—'], ['Stake', t.stake == null ? '—' : `$${Number(t.stake).toFixed(2)}`],
      ['Fees recorded', t.feeUsd == null ? '—' : `$${Number(t.feeUsd).toFixed(4)}`], ['Order ID', t.oid || '—'],
    ];
    showSheet('Trade details', `<div class="sheet-pnl mono ${pnl != null && pnl < 0 ? 'down' : 'up'}">${pnl == null ? '—' : fmtUsd(pnl)}</div>
      <p class="sheet-subtitle">${t.unmatched ? 'Hypothetical result · this order did not fill' : 'Net P&L after fees'}</p>
      ${t.error ? `<p class="profile-error">${esc(t.error)}</p>` : ''}
      <dl class="detail-kv">${details.map(([k,v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
      <a class="sheet-link" href="${esc(slugUrl(t.slug))}" target="_blank" rel="noopener noreferrer">Open market ↗</a>`, 'trade');
  });
}
