import { initCompactUI, renderCompactOverview, esc } from "./compact-ui.js?v=26";
import { buildTrades, tradePnl, effectiveWon, isStatClosed, countsInStats } from "./parse.js?v=19";
import { computeSummary, fmtPct, greeting, formatRecordLine, fmtUsd, fmtCash } from "./stats.js?v=25";
import { loadFromServer, loadBalanceFromServer, listLogSources } from "./load.js?v=2";
import { renderTradesTable, bindTradesTableSort } from "./trades-table.js?v=23";
import { computeRecap, renderRecapHtml, startRecapCountdown } from "./recap.js?v=19";
import { startAutoRefresh } from "./refresh.js";
import { currentUsd, tweenUsd, flashDelta, pulseEl } from "./animate.js";
import { initProfile, loadProfile } from "./profile.js?v=23";
import { startLivePoll } from "./live.js?v=28";
import { mountEquityChart, initEquityExpand } from "./performance-chart.js?v=24";
import { initTabs } from "./tabs.js?v=24";
import { initSettings, loadSettings } from "./settings.js?v=2";
import { initCalendar, renderCalendar } from "./calendar.js?v=23";

const state = {
  records: [],
  archiveRecords: null,
  archives: [],
  logSource: "live",
  balance: null,
  loaded: false,
};
const archiveCache = new Map();

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

function isPreviewing() {
  return state.logSource && state.logSource !== "live";
}

function previewTrades() {
  if (isPreviewing() && state.archiveRecords) return buildTrades(state.archiveRecords);
  return trades();
}

function currentLogLabel() {
  const match = state.archives.find((file) => file.id === state.logSource);
  return match?.label || "Backup";
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

function paintDayPnl(s, { hero = true, nav = true } = {}) {
  const n = s?.todayNet ?? 0;
  const record =
    (s?.todayWins || 0) + (s?.todayLosses || 0) > 0
      ? `${s.todayWins}W · ${s.todayLosses}L`
      : "no fills";
  const targets = [];
  if (hero && els.heroDayPnlVal) targets.push(els.heroDayPnlVal);
  if (nav && els.navDayPnlVal) targets.push(els.navDayPnlVal);
  for (const el of targets) {
    el.textContent = fmtUsd(n);
    el.classList.toggle("up", n >= 0);
    el.classList.toggle("down", n < -0.001);
  }
  if (hero && els.heroDayPnl) els.heroDayPnl.title = `UTC day ${record}`;
  if (nav && els.navDayPnl) els.navDayPnl.title = `UTC day ${record}`;
}

function syncLogSelects() {
  for (const sel of document.querySelectorAll(".log-source-select")) {
    if (sel.value !== state.logSource) sel.value = state.logSource;
  }
}

function paintLogPreviewNotes() {
  const previewing = isPreviewing();
  document.body.classList.toggle("is-log-preview", previewing);
  const text = previewing ? `Previewing ${currentLogLabel()} — live trading is unchanged` : "";
  for (const note of document.querySelectorAll("[data-log-preview-note]")) {
    note.hidden = !previewing;
    if (previewing) note.textContent = text;
  }
}

async function initLogSources() {
  const data = await listLogSources();
  state.archives = Array.isArray(data.files) ? data.files : [];
  const backups = state.archives.filter((file) => !file.live);
  const labels = document.querySelectorAll(".log-source");
  const selects = document.querySelectorAll(".log-source-select");
  if (!backups.length) {
    labels.forEach((el) => { el.hidden = true; });
    return;
  }
  const options = state.archives
    .map((file) => `<option value="${esc(file.id)}">${esc(file.label)}</option>`)
    .join("");
  for (const sel of selects) {
    sel.innerHTML = options;
    sel.value = state.logSource;
  }
  labels.forEach((el) => { el.hidden = false; });
}

async function selectLogSource(id) {
  const next = id || "live";
  if (next === state.logSource) return;
  if (next !== "live" && !archiveCache.has(next)) {
    setLoading(true);
    try {
      archiveCache.set(next, await loadFromServer(next));
    } catch {
      syncLogSelects();
      setLoading(false);
      return;
    } finally {
      setLoading(false);
    }
  }
  state.logSource = next;
  state.archiveRecords = next === "live" ? null : archiveCache.get(next) || null;
  syncLogSelects();
  render({ animatePnl: false });
}

function paintOverviewStats(list, { animatePnl = false, previewing = false } = {}) {
  const s = computeSummary(list);
  if (!list.length) {
    els.hero?.classList.add("is-empty");
    els.hero?.classList.remove("is-profit", "is-loss");
    if (els.greeting) els.greeting.textContent = previewing ? currentLogLabel() : greeting();
    if (els.heroNet) {
      els.heroNet.textContent = "$0.00";
      els.heroNet.className = "hero-net mono";
    }
    if (els.heroSub) els.heroSub.textContent = "0W · 0L · 0 resolved";
    paintDayPnl({ todayNet: 0, todayWins: 0, todayLosses: 0 }, { hero: true, nav: false });
    if (els.heroSubNet) els.heroSubNet.textContent = previewing ? "Archived session" : "Session Profit";
    if (els.heroBadge) {
      els.heroBadge.textContent = "Standby";
      els.heroBadge.className = "hero-badge";
    }
    if (els.statWinRate) els.statWinRate.textContent = "—";
    if (els.statWinCount) els.statWinCount.textContent = "0 wins";
    if (els.statStreak) els.statStreak.textContent = "—";
    if (els.statStreakSub) els.statStreakSub.textContent = previewing ? "Archive" : "Session";
    displayed.net = null;
    paintEquity([]);
    renderCalendar([]);
    return;
  }

  els.hero?.classList.remove("is-empty");
  const positive = s.netPnl >= 0;
  els.hero?.classList.toggle("is-profit", positive);
  els.hero?.classList.toggle("is-loss", !positive && s.netPnl < -0.001);
  if (els.greeting) els.greeting.textContent = previewing ? currentLogLabel() : greeting();
  paintDayPnl(s, { hero: true, nav: false });

  const netDelta = applyUsd(els.heroNet, "net", s.netPnl, {
    animate: animatePnl,
    pulse: animatePnl,
    format: fmtUsd,
  });
  if (animatePnl) showHeroDelta(netDelta);

  if (els.heroSub) els.heroSub.textContent = formatRecordLine(s);
  if (els.heroSubNet) els.heroSubNet.textContent = previewing
    ? "Archived session"
    : positive ? "Session in profit" : "Session in drawdown";
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

  paintEquity(list);
  renderCalendar(list);
}

function paintLiveTrades(list, { animatePnl = false } = {}) {
  const liveSummary = computeSummary(list);
  paintDayPnl(liveSummary, { hero: false, nav: true });
  if (!list.length) {
    if (els.emptyState) els.emptyState.hidden = false;
    if (els.recapSection) els.recapSection.hidden = true;
    if (els.tradesSection) els.tradesSection.hidden = true;
    displayed.last5 = null;
    return;
  }
  if (els.emptyState) els.emptyState.hidden = true;
  const { fresh } = collectNewClosed(list);
  const recap = computeRecap(list);
  if (els.recapSection) els.recapSection.hidden = !recap.recent.length;
  if (recap.recent.length && els.recapInner) {
    els.recapInner.innerHTML = renderRecapHtml(recap);
  } else {
    displayed.last5 = null;
  }
  if (els.tradesSection) els.tradesSection.hidden = false;
  renderTradesTable(list, document);
  if (animatePnl && fresh.length) flashResolvedRows(document, fresh);
}

function render({ animatePnl = false } = {}) {
  const liveList = trades();
  const previewList = previewTrades();
  const previewing = isPreviewing();
  paintLogPreviewNotes();
  renderCompactOverview(previewList, liveList);

  let balanceVal = null;
  if (state.balance?.balance_pusd != null) {
    balanceVal = Number(state.balance.balance_pusd);
  } else {
    const lastBal = [...liveList].reverse().find((t) => t.balancePusd != null);
    if (lastBal?.balancePusd != null) balanceVal = lastBal.balancePusd;
  }

  if (balanceVal != null) {
    const formattedBal = fmtCash(balanceVal);
    if (els.navBalanceVal) els.navBalanceVal.textContent = formattedBal;
    if (els.statBalance) {
      applyUsd(els.statBalance, "balance", balanceVal, {
        animate: animatePnl && displayed.balance != null && !previewing,
        pulse: animatePnl && !previewing,
        tone: false,
        format: fmtCash,
      });
    }
  } else {
    if (els.navBalanceVal) els.navBalanceVal.textContent = "—";
    if (els.statBalance) els.statBalance.textContent = "—";
    displayed.balance = null;
  }

  paintOverviewStats(previewList, {
    animatePnl: animatePnl && !previewing,
    previewing,
  });
  paintLiveTrades(liveList, { animatePnl: animatePnl && !previewing });
}

async function refresh({ silent = false } = {}) {
  if (!silent) setLoading(true);
  try {
    state.records = await loadFromServer();
    document.getElementById("data-status").hidden = true;
    state.balance = await loadBalanceFromServer();
    const wasLoaded = state.loaded;
    state.loaded = true;
    render({ animatePnl: wasLoaded });
  } catch {
    const status = document.getElementById("data-status");
    status.hidden = false;
    status.textContent = state.loaded ? "Connection lost · showing your last loaded results" : "Unable to load results. Connect your VPN to reach the server.";
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
  const resolved = list.filter((t) => isStatClosed(t) && countsInStats(t));
  if (!resolved.length) {
    if (mount) mount.innerHTML = "";
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  mountEquityChart(mount, resolved, () => paintEquity(previewTrades()));
}

initCompactUI();
bindTradesTableSort(document, render);
initProfile();
initCalendar(document.getElementById("days-root"));
initSettings();
const startTab = initTabs({
  onChange(tab) {
    if (tab === "wallet") loadProfile();
    if (tab === "settings") loadSettings();
  },
});
if (startTab === "wallet") loadProfile();
if (startTab === "settings") loadSettings();
initEquityExpand();
document.addEventListener("change", (event) => {
  const sel = event.target.closest?.(".log-source-select");
  if (sel) selectLogSource(sel.value);
});
initLogSources();
refresh();
startAutoRefresh(refresh);
startRecapCountdown(document);
startLivePoll(document);
