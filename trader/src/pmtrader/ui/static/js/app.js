import { buildTrades, tradePnl, effectiveWon, isStatClosed } from "./parse.js";
import { computeSummary, fmtPct, greeting, formatRecordLine } from "./stats.js";
import { loadFromServer, loadBalanceFromServer } from "./load.js";
import { renderTradesTable, bindTradesTableSort } from "./trades-table.js";
import { computeRecap, renderRecapHtml, startRecapCountdown } from "./recap.js";
import { startAutoRefresh } from "./refresh.js";
import { currentUsd, tweenUsd, flashDelta, pulseEl } from "./animate.js";
import { initProfile } from "./profile.js";
import { initAccordions } from "./accordion.js";
import { startLivePoll } from "./live.js";

const state = {
  records: [],
  balance: null,
  loaded: false,
};

const els = {
  shell: document.querySelector(".shell"),
  greeting: document.getElementById("hero-greeting"),
  heroNet: document.getElementById("hero-net"),
  heroDelta: document.getElementById("hero-delta"),
  heroSub: document.getElementById("hero-sub"),
  heroBadge: document.getElementById("hero-badge"),
  statBalance: document.getElementById("stat-balance"),
  statWinRate: document.getElementById("stat-win-rate"),
  refreshBtn: document.getElementById("refresh-btn"),
  emptyState: document.getElementById("empty-state"),
  hero: document.getElementById("hero"),
  tradesSection: document.getElementById("trades-section"),
  recapSection: document.getElementById("recap-section"),
  recapInner: document.getElementById("recap-inner"),
};

function trades() {
  return buildTrades(state.records);
}

function setLoading(loading) {
  els.refreshBtn?.toggleAttribute("disabled", loading);
  els.refreshBtn?.classList.toggle("is-loading", loading);
  els.shell?.classList.toggle("is-loading", loading && !state.loaded);
}

const displayed = { net: null, balance: null, last5: null };
const knownResolved = new Set();
let heroFlashTimer = null;

function toneUsd(el, n) {
  el.classList.toggle("up", n >= 0);
  el.classList.toggle("down", n < -0.001);
}

function applyUsd(el, key, next, { animate = false, pulse = false, tone = true } = {}) {
  const from = currentUsd(el) ?? displayed[key];
  displayed[key] = next;
  const doTween = animate && from != null && Math.abs(next - from) >= 0.005;
  tweenUsd(el, next, {
    from,
    animate: doTween,
    onFrame: tone ? (n) => toneUsd(el, n) : undefined,
  });
  if (doTween && pulse) pulseEl(el);
  return doTween ? next - from : 0;
}

function showHeroDelta(delta) {
  flashDelta(els.heroDelta, delta);
  els.hero?.classList.remove("is-pnl-up", "is-pnl-down");
  if (Math.abs(delta) < 0.005) return;
  els.hero?.classList.add(delta >= 0 ? "is-pnl-up" : "is-pnl-down");
  if (heroFlashTimer) clearTimeout(heroFlashTimer);
  heroFlashTimer = setTimeout(() => {
    els.hero?.classList.remove("is-pnl-up", "is-pnl-down");
    heroFlashTimer = null;
  }, 900);
}

function collectNewClosed(list) {
  const fresh = [];
  let delta = 0;
  for (const t of list) {
    if (!isStatClosed(t) || !t.oid) continue;
    if (knownResolved.has(t.oid)) continue;
    knownResolved.add(t.oid);
    fresh.push(t);
    delta += tradePnl(t) ?? 0;
  }
  return { fresh, delta };
}

function flashResolvedRows(root, fresh) {
  for (const t of fresh) {
    const tr = root.querySelector(`tr.trade-row[data-oid="${CSS.escape(t.oid)}"]`);
    if (!tr) continue;
    if (t.cashedOut || t.resolved) tr.classList.add("row-pnl-flash");
    if (effectiveWon(t) === false) tr.classList.add("row-pnl-loss");
  }
}

function render({ animatePnl = false } = {}) {
  const list = trades();
  const s = computeSummary(list);

  if (!list.length) {
    els.hero?.classList.add("is-empty");
    els.hero?.classList.remove("is-profit", "is-loss");
    els.emptyState.hidden = false;
    els.recapSection.hidden = true;
    els.tradesSection.hidden = true;
    els.heroNet.textContent = "—";
    els.heroSub.textContent = "";
    els.heroBadge.textContent = "";
    if (els.statBalance) els.statBalance.textContent = "—";
    if (els.statWinRate) els.statWinRate.textContent = "—";
    displayed.net = displayed.balance = displayed.last5 = null;
    return;
  }

  els.hero?.classList.remove("is-empty");
  els.emptyState.hidden = true;

  const { fresh } = collectNewClosed(list);
  const shouldAnimate = animatePnl && displayed.net != null && fresh.length > 0;

  const positive = s.netPnl >= 0;
  els.hero?.classList.toggle("is-profit", positive);
  els.hero?.classList.toggle("is-loss", !positive && s.netPnl < -0.001);
  els.greeting.textContent = greeting();
  const netDelta = applyUsd(els.heroNet, "net", s.netPnl, {
    animate: shouldAnimate,
    pulse: shouldAnimate,
  });
  if (shouldAnimate) showHeroDelta(netDelta);
  els.heroSub.textContent = formatRecordLine(s);
  els.heroBadge.textContent = positive ? "In profit" : s.netPnl < -0.001 ? "Drawdown" : "Breakeven";
  els.heroBadge.className = `hero-badge ${positive ? "up" : "down"}`;

  els.statWinRate.textContent = fmtPct(s.winRate);
  let balanceVal = null;
  if (state.balance?.balance_pusd != null) {
    balanceVal = Number(state.balance.balance_pusd);
  } else {
    const lastBal = [...list].reverse().find((t) => t.balancePusd != null);
    if (lastBal?.balancePusd != null) balanceVal = lastBal.balancePusd;
  }
  if (balanceVal != null) {
    applyUsd(els.statBalance, "balance", balanceVal, {
      animate: shouldAnimate,
      pulse: shouldAnimate,
      tone: false,
    });
  } else {
    els.statBalance.textContent = "—";
    displayed.balance = null;
  }

  const recap = computeRecap(list);
  els.recapSection.hidden = !recap.recent.length;
  if (recap.recent.length) {
    els.recapInner.innerHTML = renderRecapHtml(recap);
    const last5El = els.recapInner.querySelector(".recap-value");
    if (last5El) {
      applyUsd(last5El, "last5", recap.last5Net, { animate: shouldAnimate });
    } else {
      displayed.last5 = recap.last5Net;
    }
  } else {
    displayed.last5 = null;
  }

  els.tradesSection.hidden = false;
  renderTradesTable(list, document);
  if (shouldAnimate) flashResolvedRows(document, fresh);
}

async function refresh({ silent = false } = {}) {
  if (!silent) setLoading(true);
  try {
    state.records = await loadFromServer();
    state.balance = await loadBalanceFromServer();
    const wasLoaded = state.loaded;
    state.loaded = true;
    render({ animatePnl: wasLoaded });
  } catch {
    if (!state.records.length) {
      els.hero?.classList.add("is-empty");
      els.emptyState.hidden = false;
      els.emptyState.textContent = "Could not load trades.";
    }
  } finally {
    setLoading(false);
  }
}

els.refreshBtn?.addEventListener("click", () => refresh());
bindTradesTableSort(document, render);
initAccordions();
initProfile();
refresh();
startAutoRefresh(refresh);
startRecapCountdown(document);
startLivePoll(document);
