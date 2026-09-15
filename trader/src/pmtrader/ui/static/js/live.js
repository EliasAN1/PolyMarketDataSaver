/**
 * Live Trading Radar — Real-time 5m window monitor, price cards,
 * progress timeline, and strategy criteria checklist.
 */

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function iconSvg(inner) {
  return `<svg class="state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}

const ICONS = {
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  pause: '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
  power: '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/>',
  check: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
  send: '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  trend: '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
  percent: '<line x1="19" y1="5" x2="5" y2="19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
  layers: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  pulse: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
};

const VENUE_LABELS = {
  binance_spot: "Binance",
  coinbase_spot: "Coinbase",
  bybit_spot: "Bybit",
  median: "Median",
};

function venueLabel(source) {
  return VENUE_LABELS[source] || "Binance";
}

const SKIP_STATUS = {
  outside_when: { label: "When", icon: "clock", hint: "Outside selected hours / days / markets" },
  too_late: { label: "Late", icon: "clock", hint: "Too late to enter" },
  no_window: { label: "Idle", icon: "pause", hint: "No live window" },
  no_ptb: { label: "PTB", icon: "target", hint: "No price to beat" },
  no_btc: { label: "Binance", icon: "trend", hint: "No Binance feed" },
  btc_out: { label: "Range", icon: "trend", hint: "Binance distance out of range" },
  odds_out: { label: "Odds", icon: "percent", hint: "Odds outside trigger band" },
  no_ask: { label: "Book", icon: "layers", hint: "No ask on the book" },
  ask_above_cap: { label: "Cap", icon: "shield", hint: "Ask above FAK cap" },
  no_twap: { label: "TWAP", icon: "pulse", hint: "No TWAP yet" },
  twap_disagree: { label: "TWAP", icon: "pulse", hint: "TWAP disagrees with Binance" },
  venues: { label: "Venues", icon: "grid", hint: "Not enough venues" },
};

function setBadge(el, text, cls) {
  if (!el) return;
  el.textContent = text;
  el.className = `pillar-badge ${cls || ""}`.trim();
}

function orderNote(data) {
  const order = data?.order;
  if (!order) return "";
  const side = String(order.side || "").toUpperCase();
  const lim = order.limit != null && !Number.isNaN(Number(order.limit))
    ? ` @ ${Number(order.limit).toFixed(2)}`
    : "";
  if (order.ok) {
    return `${side} FAK sent${lim}`.trim();
  }
  const detail = order.error || order.error_short || "rejected";
  return `${side} FAK failed${lim} — ${detail}`.trim();
}

function setOrderNote(el, data, traded) {
  if (!el) return;
  const text = traded ? orderNote(data) : "";
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    el.className = "radar-order-note";
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.className = `radar-order-note ${data?.order?.ok ? "is-ok" : "is-fail"}`;
}

function setStateTag(el, { cls, label, icon, hint }) {
  if (!el) return;
  el.className = `window-state-tag ${cls || ""}`.trim();
  el.title = hint || label;
  el.innerHTML = `${iconSvg(ICONS[icon] || ICONS.pause)}<span>${esc(label)}</span>`;
}

function fmtUsd(val, digits = 2) {
  if (val == null || Number.isNaN(val)) return "—";
  return `$${Number(val).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function fmtSignedUsd(val, digits = 1) {
  if (val == null || Number.isNaN(val)) return "—";
  const sign = val >= 0 ? "+" : "−";
  const abs = Math.abs(val).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const arrow = val > 0 ? "▲" : val < 0 ? "▼" : "";
  return `${sign}$${abs} ${arrow}`.trim();
}

function fmtOdds(val) {
  if (val == null || Number.isNaN(val)) return "—";
  return Number(val).toFixed(2);
}

const CHECKLIST_KEY = "pmtrader-checks-collapsed";
let checklistWired = false;

function applyChecklistCollapsed(root, collapsed) {
  const box = root.getElementById("strategy-checklist");
  const toggle = root.getElementById("checklist-toggle");
  if (!box) return;
  box.classList.toggle("is-collapsed", collapsed);
  if (toggle) toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
}

function wireChecklistToggle(root) {
  if (checklistWired) return;
  const toggle = root.getElementById("checklist-toggle");
  const box = root.getElementById("strategy-checklist");
  if (!toggle || !box) return;
  checklistWired = true;
  let collapsed = false;
  try {
    collapsed = localStorage.getItem(CHECKLIST_KEY) === "1";
  } catch {
    collapsed = false;
  }
  applyChecklistCollapsed(root, collapsed);
  toggle.addEventListener("click", () => {
    const next = !box.classList.contains("is-collapsed");
    applyChecklistCollapsed(root, next);
    try {
      localStorage.setItem(CHECKLIST_KEY, next ? "1" : "0");
    } catch {
      /* ignore */
    }
  });
}

function paintBrandLive(root, { cls = "", title = "Live" } = {}) {
  const brand = root.getElementById("nav-live");
  if (!brand) return;
  brand.classList.remove("is-offline", "is-waiting", "is-failed");
  if (cls) brand.classList.add(cls);
  brand.title = title;
}

export function renderLive(data, root = document) {
  wireChecklistToggle(root);
  const radarTimeLeft = root.getElementById("radar-time-left");
  const radarTimeBadge = root.getElementById("radar-time-badge");
  const radarStateTag = root.getElementById("radar-state-tag");

  if (!data?.running) {
    if (radarTimeLeft) radarTimeLeft.textContent = "—";
    setStateTag(radarStateTag, { label: "Off", icon: "power", hint: "Trader offline" });
    setOrderNote(root.getElementById("radar-order-note"), null, false);
    paintBrandLive(root, { cls: "is-offline", title: "Offline" });
    return;
  }

  const secondsLeft = data.seconds_left ?? 0;
  const duration = 300; // standard 5m window duration
  const elapsed = Math.max(0, duration - secondsLeft);
  const side = (data.side || "").toLowerCase();
  const traded = Boolean(data.traded);
  const state = data.state || "standby";

  if (radarTimeLeft) {
    const mins = Math.floor(secondsLeft / 60);
    const secs = secondsLeft % 60;
    radarTimeLeft.textContent = `${mins}:${String(secs).padStart(2, "0")}`;
  }

  if (radarTimeBadge) {
    radarTimeBadge.classList.toggle("is-urgent", secondsLeft <= 30 && secondsLeft > 0);
  }

  let brandLive = { cls: "", title: "Live" };

  if (state === "rolling" || data.stale) {
    setStateTag(radarStateTag, {
      cls: "state-skip",
      label: "Rolling",
      icon: "clock",
      hint: "Window ended — waiting for the next 5m market",
    });
    brandLive = { cls: "is-waiting", title: "Rolling" };
  } else if (traded && (state === "failed" || data.order?.ok === false)) {
    const short = data.order?.error_short || "Failed";
    setStateTag(radarStateTag, {
      cls: "state-failed",
      label: short,
      icon: "alert",
      hint: orderNote(data) || "Order rejected",
    });
    brandLive = { cls: "is-failed", title: short };
  } else if (traded) {
    setStateTag(radarStateTag, {
      cls: "state-sent",
      label: "Sent",
      icon: "send",
      hint: orderNote(data) || "Order sent",
    });
    brandLive = { cls: "", title: "Sent" };
  } else if (state === "ready") {
    setStateTag(radarStateTag, { cls: "state-ready", label: "Ready", icon: "check", hint: "Trigger ready" });
    brandLive = { cls: "", title: "Ready" };
  } else if (state.startsWith("skip:")) {
    const reason = state.slice(5);
    const skip = { ...(SKIP_STATUS[reason] || {
      label: reason.replaceAll("_", " "),
      icon: "pause",
      hint: reason.replaceAll("_", " "),
    }) };
    if (reason === "no_ptb" && data.ptb_wait) {
      skip.hint = data.ptb_wait;
    }
    if (reason === "no_btc" || reason === "btc_out") {
      const v = venueLabel(data.config?.btc_source);
      skip.label = v;
      skip.hint = reason === "no_btc" ? `No ${v} feed` : `${v} distance out of range`;
    }
    setStateTag(radarStateTag, { cls: "state-skip", ...skip });
    brandLive = { cls: "is-waiting", title: skip.hint || "Waiting" };
  } else {
    setStateTag(radarStateTag, { label: "Idle", icon: "pause", hint: "Standby" });
    brandLive = { cls: "", title: "Live" };
  }

  setOrderNote(root.getElementById("radar-order-note"), data, traded);
  paintBrandLive(root, brandLive);

  // 2. Window Timeline Progress
  const pct = Math.min(100, Math.max(0, (elapsed / duration) * 100));
  const progressBar = root.getElementById("timeline-progress-bar");
  const cursor = root.getElementById("timeline-cursor");
  const watchZone = root.getElementById("timeline-watch-zone");

  if (progressBar) progressBar.style.width = `${pct}%`;
  if (cursor) cursor.style.left = `${pct}%`;

  if (watchZone && data.config) {
    const fromS = data.config.watch_from_s ?? 0;
    const toS = data.config.watch_to_s ?? duration;
    const zoneLeft = (fromS / duration) * 100;
    const zoneWidth = Math.max(0, ((toS - fromS) / duration) * 100);
    watchZone.style.left = `${zoneLeft}%`;
    watchZone.style.width = `${zoneWidth}%`;

    const midLabel = root.getElementById("timeline-mid-label");
    if (midLabel) {
      const fMin = Math.floor(fromS / 60);
      const fSec = String(fromS % 60).padStart(2, "0");
      const tMin = Math.floor(toS / 60);
      const tSec = String(toS % 60).padStart(2, "0");
      midLabel.textContent = `Watch: ${fMin}:${fSec}–${tMin}:${tSec}`;
    }
  }

  // 3. Pillar 1: Price To Beat
  const ptbEl = root.getElementById("pillar-ptb-val");
  if (ptbEl) ptbEl.textContent = fmtUsd(data.ptb, 2);

  // 4. Pillar 2: Binance (or selected venue) vs PTB
  const venue = venueLabel(data.config?.btc_source);
  const btcLabelEl = root.getElementById("pillar-btc-label");
  const btcDeltaEl = root.getElementById("pillar-btc-delta");
  const btcSpotEl = root.getElementById("pillar-btc-spot");
  const btcSideBadge = root.getElementById("btc-side-badge");
  const btcSub = root.getElementById("pillar-btc-sub");

  if (btcLabelEl) btcLabelEl.textContent = venue;
  if (btcSpotEl) {
    btcSpotEl.textContent = data.btc != null ? fmtUsd(data.btc, 2) : "—";
  }
  if (btcDeltaEl) {
    btcDeltaEl.textContent = data.btc_delta != null ? `${fmtSignedUsd(data.btc_delta, 1)} vs PTB` : "vs PTB";
    btcDeltaEl.className = `pillar-delta ${data.btc_delta > 0 ? "up" : data.btc_delta < 0 ? "down" : ""}`;
  }
  if (data.btc_delta > 0) {
    setBadge(btcSideBadge, "UP", "is-up");
  } else if (data.btc_delta < 0) {
    setBadge(btcSideBadge, "DOWN", "is-down");
  } else {
    setBadge(btcSideBadge, "—", "");
  }
  if (btcSub && data.config) {
    const minD = data.config.min_btc_away;
    const maxD = data.config.max_btc_away;
    if (minD === 0 && maxD != null) {
      btcSub.textContent = `≤ $${maxD} from PTB`;
    } else if (minD > 0 && maxD != null) {
      btcSub.textContent = `$${minD}–$${maxD} from PTB`;
    } else if (minD > 0 && maxD == null) {
      btcSub.textContent = `≥ $${minD} from PTB`;
    } else {
      btcSub.textContent = `${venue} minus PTB`;
    }
  }

  // 5. Pillar 3: Polymarket Odds
  const upAskEl = root.getElementById("odds-up-val");
  const downAskEl = root.getElementById("odds-down-val");
  const upMidEl = root.getElementById("odds-up-mid");
  const downMidEl = root.getElementById("odds-down-mid");
  const upBox = root.getElementById("odds-up-box");
  const downBox = root.getElementById("odds-down-box");
  const oddsRatioFill = root.getElementById("odds-ratio-fill");
  const oddsBandBadge = root.getElementById("odds-band-badge");

  if (upAskEl) upAskEl.textContent = fmtOdds(data.up_ask);
  if (downAskEl) downAskEl.textContent = fmtOdds(data.down_ask);
  if (upMidEl) upMidEl.textContent = `mid ${fmtOdds(data.up_mid)}`;
  if (downMidEl) downMidEl.textContent = `mid ${fmtOdds(data.down_mid)}`;

  if (upBox) upBox.classList.toggle("is-selected", side === "up");
  if (downBox) downBox.classList.toggle("is-selected", side === "down");

  if (oddsRatioFill && data.up_ask != null && data.down_ask != null) {
    const total = (data.up_ask || 0.5) + (data.down_ask || 0.5);
    const upPct = total > 0 ? (data.up_ask / total) * 100 : 50;
    oddsRatioFill.style.height = `${upPct}%`;
    oddsRatioFill.style.width = `${upPct}%`;
  }

  if (oddsBandBadge && data.config) {
    const trigger = data.config.trigger_band ?? `${data.config.odds_min?.toFixed(2)}-${data.config.odds_max?.toFixed(2)}`;
    const fak = data.config.fak_limit ?? data.config.odds_max;
    oddsBandBadge.textContent = `TRIG ${trigger} · FAK ${Number(fak).toFixed(2)}`;
  }

  // 6. Pillar 4: TWAP vs PTB
  const twapDeltaEl = root.getElementById("pillar-twap-delta");
  const twapAbsEl = root.getElementById("pillar-twap-abs");
  const twapAgreeBadge = root.getElementById("twap-agree-badge");
  const twapSub = root.getElementById("pillar-twap-sub");

  if (twapAbsEl) {
    twapAbsEl.textContent = data.twap != null ? fmtUsd(data.twap, 2) : "—";
  }
  if (twapDeltaEl) {
    twapDeltaEl.textContent = data.twap_delta != null ? `${fmtSignedUsd(data.twap_delta, 1)} vs PTB` : "vs PTB";
    twapDeltaEl.className = `pillar-delta ${data.twap_delta > 0 ? "up" : data.twap_delta < 0 ? "down" : ""}`;
  }
  if (twapSub) twapSub.textContent = `Must match ${venue} side`;
  if (data.twap_delta == null || data.btc_delta == null) {
    setBadge(twapAgreeBadge, "—", "");
  } else if (
    (data.btc_delta > 0 && data.twap_delta > 0) ||
    (data.btc_delta < 0 && data.twap_delta < 0)
  ) {
    setBadge(twapAgreeBadge, "AGREES", "is-ok");
  } else {
    setBadge(twapAgreeBadge, "DISAGREES", "is-no");
  }

  // 7. Strategy Checklist Matrix
  const checksGrid = root.getElementById("radar-checks-grid");
  const summaryBadge = root.getElementById("checklist-summary-badge");
  const checks = (data.checks || []).filter((c) => c.enabled || c.id !== "venues");

  let passingCount = 0;
  let enabledCount = 0;

  const checksHtml = checks.map((c) => {
    if (c.enabled) enabledCount++;
    if (c.enabled && c.ok) passingCount++;

    const itemCls = !c.enabled ? "is-off" : c.ok ? "is-ok" : "is-blocked";
    const statusGlyph = !c.enabled
      ? `<span class="check-glyph off">—</span>`
      : c.ok
      ? `<span class="check-glyph ok">✓</span>`
      : `<span class="check-glyph fail">✕</span>`;

    const title = c.name || c.label || "Check";
    const target = c.target ? `<span class="check-target">${esc(c.target)}</span>` : "";
    const displayVal = !c.enabled ? "OFF" : c.value;

    return `
      <div class="check-card ${itemCls}">
        <div class="check-card-left">
          ${statusGlyph}
          <div class="check-info">
            <span class="check-title">${esc(title)}</span>
            ${target}
          </div>
        </div>
        <span class="check-val-badge">${esc(displayVal)}</span>
      </div>
    `;
  }).join("");

  if (checksGrid) checksGrid.innerHTML = checksHtml;
  if (summaryBadge) {
    const disabledCount = checks.length - enabledCount;
    const disabledStr = disabledCount > 0 ? ` · ${disabledCount} off` : "";
    summaryBadge.textContent = `${passingCount} / ${enabledCount} Passing${disabledStr}`;
    summaryBadge.style.color = passingCount === enabledCount && enabledCount > 0 ? "var(--emerald-light)" : "var(--amber)";
  }
}

export function startLivePoll(root = document, intervalMs = 1000) {
  async function tick() {
    try {
      const res = await fetch("/api/live", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderLive(data, root);
    } catch {
      renderLive({ running: false }, root);
    }
  }

  tick();
  setInterval(() => {
    if (!document.hidden) tick();
  }, intervalMs);
}
