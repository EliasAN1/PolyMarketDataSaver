/**
 * Live Trading Radar — Real-time 5m window monitor, 4 core signal pillars,
 * progress timeline, and strategy criteria checklist.
 */

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
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

export function renderLive(data, root = document) {
  const radarSlug = root.getElementById("radar-slug");
  const radarTimeLeft = root.getElementById("radar-time-left");
  const radarTimeBadge = root.getElementById("radar-time-badge");
  const radarStateTag = root.getElementById("radar-state-tag");
  const navLiveChip = root.getElementById("nav-live-chip");
  const navLiveStatus = root.getElementById("nav-live-status");

  // If trader offline
  if (!data?.running) {
    if (radarSlug) radarSlug.textContent = "Offline";
    if (radarTimeLeft) radarTimeLeft.textContent = "—";
    if (radarStateTag) {
      radarStateTag.textContent = "OFFLINE";
      radarStateTag.className = "window-state-tag";
    }
    if (navLiveChip) {
      navLiveChip.className = "nav-chip live-state-chip is-offline";
    }
    if (navLiveStatus) navLiveStatus.textContent = "OFFLINE";
    return;
  }

  const secondsLeft = data.seconds_left ?? 0;
  const duration = 300; // standard 5m window duration
  const elapsed = Math.max(0, duration - secondsLeft);
  const side = (data.side || "").toLowerCase();
  const traded = Boolean(data.traded);
  const state = data.state || "standby";

  // 1. Slug & Header Status
  if (radarSlug) radarSlug.textContent = data.slug || "Awaiting Window…";
  
  if (radarTimeLeft) {
    const mins = Math.floor(secondsLeft / 60);
    const secs = secondsLeft % 60;
    radarTimeLeft.textContent = `${mins}:${String(secs).padStart(2, "0")}`;
  }

  if (radarTimeBadge) {
    radarTimeBadge.classList.toggle("is-urgent", secondsLeft <= 30 && secondsLeft > 0);
  }

  // State Tag & Nav Status
  let stateText = "STANDBY";
  let stateCls = "state-standby";
  let navStatusText = "LIVE";
  let navCls = "nav-chip live-state-chip";

  if (traded) {
    stateText = "ORDER SENT";
    stateCls = "state-sent";
    navStatusText = "SENT";
  } else if (state === "ready") {
    stateText = "TRIGGER READY";
    stateCls = "state-ready";
    navStatusText = "READY";
  } else if (state.startsWith("skip:")) {
    const reason = state.slice(5).toUpperCase().replaceAll("_", " ");
    stateText = `SKIP: ${reason}`;
    stateCls = "state-skip";
    navStatusText = "WAITING";
    navCls += " is-waiting";
  }

  if (radarStateTag) {
    radarStateTag.textContent = stateText;
    radarStateTag.className = `window-state-tag ${stateCls}`;
  }

  if (navLiveChip) {
    navLiveChip.className = navCls;
  }
  if (navLiveStatus) {
    navLiveStatus.textContent = navStatusText;
  }

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

  // 4. Pillar 2: BTC Spot vs PTB
  const btcDeltaEl = root.getElementById("pillar-btc-delta");
  const btcSpotEl = root.getElementById("pillar-btc-spot");
  const btcSideBadge = root.getElementById("btc-side-badge");
  const btcSub = root.getElementById("pillar-btc-sub");

  if (btcDeltaEl) {
    btcDeltaEl.textContent = fmtSignedUsd(data.btc_delta, 1);
    btcDeltaEl.className = `pillar-value ${data.btc_delta > 0 ? "up" : data.btc_delta < 0 ? "down" : ""}`;
  }
  if (btcSpotEl) {
    btcSpotEl.textContent = data.btc != null ? `BTC ${fmtUsd(data.btc, 2)}` : "BTC $—";
  }
  if (btcSideBadge) {
    if (side) {
      btcSideBadge.textContent = `SIDE ${side.toUpperCase()}`;
      btcSideBadge.style.color = side === "up" ? "var(--emerald-light)" : "var(--crimson-light)";
      btcSideBadge.style.borderColor = side === "up" ? "var(--emerald-border)" : "var(--crimson-border)";
    } else {
      btcSideBadge.textContent = "SIDE —";
      btcSideBadge.style.color = "";
      btcSideBadge.style.borderColor = "";
    }
  }
  if (btcSub && data.config) {
    const minD = data.config.min_btc_away;
    const maxD = data.config.max_btc_away;
    if (minD === 0 && maxD != null) {
      btcSub.textContent = `Target: ≤ $${maxD}`;
    } else if (minD > 0 && maxD != null) {
      btcSub.textContent = `Target: $${minD}–$${maxD}`;
    } else if (minD > 0 && maxD == null) {
      btcSub.textContent = `Target: ≥ $${minD}`;
    } else {
      btcSub.textContent = "Window distance";
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
    oddsRatioFill.style.width = `${upPct}%`;
  }

  if (oddsBandBadge && data.config) {
    const trigger = data.config.trigger_band ?? `${data.config.odds_min?.toFixed(2)}-${data.config.odds_max?.toFixed(2)}`;
    const fak = data.config.fak_limit ?? data.config.odds_max;
    oddsBandBadge.textContent = `TRIG ${trigger} · FAK ${Number(fak).toFixed(2)}`;
  }

  // 6. Pillar 4: TWAP & Venues
  const twapDeltaEl = root.getElementById("pillar-twap-delta");
  const twapAbsEl = root.getElementById("pillar-twap-abs");
  const venuesBadge = root.getElementById("venues-count-badge");

  if (twapDeltaEl) {
    twapDeltaEl.textContent = fmtSignedUsd(data.twap_delta, 1);
    twapDeltaEl.className = `pillar-value ${data.twap_delta > 0 ? "up" : data.twap_delta < 0 ? "down" : ""}`;
  }
  if (twapAbsEl) {
    twapAbsEl.textContent = data.twap != null ? `TWAP ${fmtUsd(data.twap, 2)}` : "TWAP $—";
  }
  if (venuesBadge) {
    const venuesCount = side === "up" ? data.venues_up : side === "down" ? data.venues_down : (data.venues_up || data.venues_down || 0);
    venuesBadge.textContent = `${venuesCount ?? 0} / 4 VENUES`;
  }

  // 7. Strategy Checklist Matrix
  const checksGrid = root.getElementById("radar-checks-grid");
  const summaryBadge = root.getElementById("checklist-summary-badge");
  const checks = data.checks || [];

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
