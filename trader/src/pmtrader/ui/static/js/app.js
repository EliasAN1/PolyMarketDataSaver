import { buildTrades, tradePnl, effectiveWon, isStatClosed } from "./parse.js?v=17";
import { computeSummary, fmtPct, greeting, formatRecordLine, fmtUsd, fmtCash } from "./stats.js?v=17";
import { loadFromServer, loadBalanceFromServer } from "./load.js";
import { renderTradesTable, bindTradesTableSort } from "./trades-table.js?v=17";
import { computeRecap, renderRecapHtml, startRecapCountdown } from "./recap.js?v=17";
import { startAutoRefresh } from "./refresh.js";
import { currentUsd, tweenUsd, flashDelta, pulseEl } from "./animate.js";
import { initProfile, loadProfile } from "./profile.js?v=17";
import { startLivePoll } from "./live.js?v=18";
import { mountEquityChart } from "./equity.js?v=17";
import { initTabs } from "./tabs.js?v=17";
import { initCalendar, renderCalendar } from "./calendar.js?v=17";

const state = {
  records: [],
  balance: null,
  loaded: false,
};

const els = {
  shell: document.getElementById("app-shell"),
  greeting: document.getElementById("hero-greeting"),
  heroNet: document.getElementById("hero-net"),
  heroDelta: document.getElementById("hero-delta"),
  heroSub: document.getElementById("hero-sub"),
  heroSubNet: document.getElementById("hero-sub-net"),
  heroDayPnl: document.getElementById("hero-day-pnl"),
  heroDayPnlVal: document.getElementById("hero-day-pnl-val"),
  heroBadge: document.getElementById("hero-badge"),
  statBalance: document.getElementById("stat-balance"),
  statWinRate: document.getElementById("stat-win-rate"),
  statWinCount: document.getElementById("stat-win-count"),
  statStreak: document.getElementById("stat-streak"),
  statStreakSub: document.getElementById("stat-streak-sub"),
  navBalanceVal: document.getElementById("nav-balance-val"),
  navDayPnl: document.getElementById("nav-day-pnl"),
  navDayPnlVal: document.getElementById("nav-day-pnl-val"),
  emptyState: document.getElementById("empty-state"),
  hero: document.getElementById("performance-section"),
  tradesSection: document.getElementById("trades-section"),
  recapSection: document.getElementById("recap-section"),
  recapInner: document.getElementById("recap-inner"),
};

function trades() {
  return buildTrades(state.records);
}

function setLoading(loading) {
  els.shell?.classList.toggle("is-loading", loading && !state.loaded);
}

const displayed = { net: null, balance: null, last5: null };
const knownResolved = new Set();
let heroFlashTimer = null;

function toneUsd(el, n) {
  el.classList.toggle("up", n >= 0);
  el.classList.toggle("down", n < -0.001);
}

function applyUsd(el, key, next, { animate = false, pulse = false, tone = true, format = fmtUsd } = {}) {
  const from = currentUsd(el) ?? displayed[key];
  displayed[key] = next;
  const doTween = animate && from != null && Math.abs(next - from) >= 0.005;
  tweenUsd(el, next, {
    from,
    animate: doTween,
    format,
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

function paintDayPnl(s) {
  const n = s?.todayNet ?? 0;
  const record =
    (s?.todayWins || 0) + (s?.todayLosses || 0) > 0
      ? `${s.todayWins}W · ${s.todayLosses}L`
      : "no fills";
  for (const el of [els.heroDayPnlVal, els.navDayPnlVal]) {
    if (!el) continue;
    el.textContent = fmtUsd(n);
    el.classList.toggle("up", n >= 0);
    el.classList.toggle("down", n < -0.001);
  }
  if (els.heroDayPnl) els.heroDayPnl.title = `UTC day ${record}`;
  if (els.navDayPnl) els.navDayPnl.title = `UTC day ${record}`;
}

function render({ animatePnl = false } = {}) {
  const list = trades();
  const s = computeSummary(list);

  // Update Collateral Balances (No + prefix for pure balance)
  let balanceVal = null;
  if (state.balance?.balance_pusd != null) {
    balanceVal = Number(state.balance.balance_pusd);
  } else {
    const lastBal = [...list].reverse().find((t) => t.balancePusd != null);
    if (lastBal?.balancePusd != null) balanceVal = lastBal.balancePusd;
  }

  if (balanceVal != null) {
    const formattedBal = fmtCash(balanceVal);
    if (els.navBalanceVal) els.navBalanceVal.textContent = formattedBal;
    if (els.statBalance) {
      applyUsd(els.statBalance, "balance", balanceVal, {
        animate: animatePnl && displayed.balance != null,
        pulse: animatePnl,
        tone: false,
        format: fmtCash,
      });
    }
  } else {
    if (els.navBalanceVal) els.navBalanceVal.textContent = "—";
    if (els.statBalance) els.statBalance.textContent = "—";
    displayed.balance = null;
  }

  // If no trades
  if (!list.length) {
    els.hero?.classList.add("is-empty");
    els.hero?.classList.remove("is-profit", "is-loss");
    if (els.emptyState) els.emptyState.hidden = false;
    if (els.recapSection) els.recapSection.hidden = true;
    if (els.tradesSection) els.tradesSection.hidden = true;
    if (els.heroNet) {
      els.heroNet.textContent = "$0.00";
      els.heroNet.className = "hero-net mono";
    }
    if (els.heroSub) els.heroSub.textContent = "0W · 0L · 0 resolved";
    paintDayPnl({ todayNet: 0, todayWins: 0, todayLosses: 0 });
    if (els.heroSubNet) els.heroSubNet.textContent = "Session Profit";
    if (els.heroBadge) {
      els.heroBadge.textContent = "Standby";
      els.heroBadge.className = "hero-badge";
    }
    if (els.statWinRate) els.statWinRate.textContent = "—";
    if (els.statWinCount) els.statWinCount.textContent = "0 wins";
    if (els.statStreak) els.statStreak.textContent = "—";
    if (els.statStreakSub) els.statStreakSub.textContent = "Session";
    displayed.net = displayed.last5 = null;
    paintEquity([]);
    renderCalendar([]);
    return;
  }

  if (els.emptyState) els.emptyState.hidden = true;

  const { fresh } = collectNewClosed(list);
  const shouldAnimate = animatePnl && displayed.net != null && fresh.length > 0;

  const positive = s.netPnl >= 0;
  els.hero?.classList.toggle("is-profit", positive);
  els.hero?.classList.toggle("is-loss", !positive && s.netPnl < -0.001);
  if (els.greeting) els.greeting.textContent = greeting();
  paintDayPnl(s);

  const netDelta = applyUsd(els.heroNet, "net", s.netPnl, {
    animate: shouldAnimate,
    pulse: shouldAnimate,
    format: fmtUsd,
  });
  if (shouldAnimate) showHeroDelta(netDelta);

  if (els.heroSub) els.heroSub.textContent = formatRecordLine(s);
  if (els.heroSubNet) els.heroSubNet.textContent = positive ? "Session in profit" : "Session in drawdown";
  if (els.heroBadge) {
    els.heroBadge.textContent = positive ? "In Profit" : s.netPnl < -0.001 ? "Drawdown" : "Breakeven";
    els.heroBadge.className = `hero-badge ${positive ? "up" : "down"}`;
  }

  if (els.statWinRate) els.statWinRate.textContent = fmtPct(s.winRate);
  if (els.statWinCount) els.statWinCount.textContent = `${s.wins}W / ${s.losses}L (${s.resolved} total)`;

  if (els.statStreak) {
    if (s.currentWinStreak > 0) {
      els.statStreak.textContent = `${s.currentWinStreak}W`;
      els.statStreak.className = "tile-val mono up";
    } else if (s.currentLossStreak > 0) {
      els.statStreak.textContent = `${s.currentLossStreak}L`;
      els.statStreak.className = "tile-val mono down";
    } else {
      els.statStreak.textContent = "—";
      els.statStreak.className = "tile-val mono";
    }
  }

  if (els.statStreakSub) {
    els.statStreakSub.textContent = `Max: ${s.maxWinStreak}W / ${s.maxLossStreak}L`;
  }

  // Recap Section
  const recap = computeRecap(list);
  if (els.recapSection) els.recapSection.hidden = !recap.recent.length;
  if (recap.recent.length && els.recapInner) {
    els.recapInner.innerHTML = renderRecapHtml(recap);
  } else {
    displayed.last5 = null;
  }

  // Trades Section
  if (els.tradesSection) els.tradesSection.hidden = false;
  renderTradesTable(list, document);
  if (shouldAnimate) flashResolvedRows(document, fresh);

  paintEquity(list);
  renderCalendar(list);
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
      if (els.emptyState) {
        els.emptyState.hidden = false;
        const p = els.emptyState.querySelector("p");
        if (p) p.textContent = "Could not load trades log from server.";
      }
    }
  } finally {
    setLoading(false);
  }
}

function paintEquity(list) {
  const mount = document.getElementById("equity-chart");
  const empty = document.getElementById("equity-empty");
  const resolved = list.filter((t) => isStatClosed(t));
  if (!resolved.length) {
    if (mount) mount.innerHTML = "";
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  mountEquityChart(mount, resolved, () => paintEquity(trades()));
}

bindTradesTableSort(document, render);
initProfile();
initCalendar(document.getElementById("days-root"));
const startTab = initTabs({
  onChange(tab) {
    if (tab === "wallet") loadProfile();
  },
});
if (startTab === "wallet") loadProfile();
refresh();
startAutoRefresh(refresh);
startRecapCountdown(document);
startLivePoll(document);
