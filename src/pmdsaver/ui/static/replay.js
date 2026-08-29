(() => {
  const LC = window.LightweightCharts;
  const COLORS = {
    spot: "#5b8cff",
    fut: "#9b7bff",
    coinbase: "#4cc9f0",
    bybit: "#e07a5f",
    up: "#3dd68c",
    down: "#ff6b7a",
    ptb: "#f5c451",
    twap: "#e0b84e",
    vol: "rgba(91, 140, 255, 0.55)",
    grid: "#243044",
    text: "#8ea0b8",
    bg: "#0b0f14",
  };

  const TABS = {
    odds: {
      columns: [
        ["elapsed", "Elapsed"],
        ["clock", "Time"],
        ["event_type", "Event"],
        ["up_bid", "UP bid"],
        ["up_ask", "UP ask"],
        ["up_mid", "UP mid"],
        ["down_bid", "DN bid"],
        ["down_ask", "DN ask"],
        ["down_mid", "DN mid"],
        ["last_trade_side", "Trade"],
        ["last_trade_price", "Px"],
      ],
    },
    binance_spot: { columns: [["elapsed", "Elapsed"], ["clock", "Time"], ["price", "Price"], ["size", "Size"]] },
    binance_futures: { columns: [["elapsed", "Elapsed"], ["clock", "Time"], ["price", "Price"], ["size", "Size"]] },
    coinbase_spot: { columns: [["elapsed", "Elapsed"], ["clock", "Time"], ["price", "Price"], ["size", "Size"]] },
    bybit_spot: { columns: [["elapsed", "Elapsed"], ["clock", "Time"], ["price", "Price"], ["size", "Size"]] },
    twap: { columns: [["elapsed", "Elapsed"], ["clock", "Time"], ["value", "TWAP"]] },
    volume: {
      columns: [
        ["elapsed", "Elapsed"],
        ["clock", "Time"],
        ["source", "Source"],
        ["base_volume", "Base vol"],
        ["quote_volume", "Quote vol"],
        ["is_closed", "Closed"],
      ],
    },
  };

  const $ = (id) => document.getElementById(id);

  const state = {
    windows: [],
    windowId: null,
    data: null,
    tab: "odds",
    search: "",
    sortKey: "recv_ts_ms",
    sortDir: 1,
    page: 0,
    pageSize: 100,
    chart: null,
    series: {},
    ptbLine: null,
  };

  function showError(msg) {
    const box = $("errorBox");
    box.hidden = !msg;
    box.textContent = msg || "";
  }

  function fmtNum(value, digits) {
    if (value == null || value === "") return "—";
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  }

  function fmtClock(ms) {
    if (ms == null) return "—";
    const d = new Date(Number(ms));
    if (!Number.isFinite(d.getTime())) return "—";
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function fmtElapsed(ms, start) {
    if (ms == null || start == null) return "—";
    const s = Math.max(0, (Number(ms) / 1000) - Number(start));
    if (!Number.isFinite(s)) return "—";
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${r.toFixed(2).padStart(5, "0")}`;
  }

  function fmtWhen(ts) {
    if (ts == null) return "—";
    const d = new Date(Number(ts) * 1000);
    if (!Number.isFinite(d.getTime())) return "—";
    return d.toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function polymarketUrl(slug) {
    return `https://polymarket.com/event/${encodeURIComponent(slug)}`;
  }

  function queryWindowId() {
    const raw = new URLSearchParams(location.search).get("window_id");
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  function toTime(ms) {
    const t = Math.floor(Number(ms) / 1000);
    return Number.isFinite(t) && t > 0 ? t : null;
  }

  function uniqueLine(rows, valueKey) {
    const byTime = new Map();
    for (const row of rows || []) {
      const time = toTime(row.recv_ts_ms);
      const value = Number(row[valueKey] ?? row.price ?? row.value);
      if (time == null || !Number.isFinite(value)) continue;
      byTime.set(time, { time, value });
    }
    return [...byTime.values()];
  }

  function addSeries(type, options, pane) {
    const { chart } = state;
    if (chart.addSeries && type) return chart.addSeries(type, options, pane);
    const fallback = { Line: "addLineSeries", Histogram: "addHistogramSeries" }[options._kind || "Line"];
    return chart[fallback](options);
  }

  function ensureChart() {
    if (state.chart) return;
    if (!LC) {
      showError("Chart library failed to load.");
      return;
    }
    const el = $("chart");
    const dashed = LC.LineStyle ? LC.LineStyle.Dashed : 2;
    state.chart = LC.createChart(el, {
      width: el.clientWidth || 800,
      height: el.clientHeight || 520,
      layout: {
        background: { type: LC.ColorType ? LC.ColorType.Solid : "solid", color: COLORS.bg },
        textColor: COLORS.text,
        fontFamily: "Inter, Segoe UI, system-ui, sans-serif",
      },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      crosshair: { mode: LC.CrosshairMode ? LC.CrosshairMode.Normal : 1 },
      rightPriceScale: { borderColor: COLORS.grid },
      timeScale: { borderColor: COLORS.grid, timeVisible: true, secondsVisible: true, rightOffset: 4 },
    });
    const Line = LC.LineSeries;
    const Hist = LC.HistogramSeries;
    const s = state.series;
    s.spot = addSeries(Line, { color: COLORS.spot, lineWidth: 2, priceFormat: { type: "price", precision: 2, minMove: 0.01 } }, 0);
    s.fut = addSeries(Line, { color: COLORS.fut, lineWidth: 1 }, 0);
    s.coinbase = addSeries(Line, { color: COLORS.coinbase, lineWidth: 1 }, 0);
    s.bybit = addSeries(Line, { color: COLORS.bybit, lineWidth: 1 }, 0);
    s.up = addSeries(Line, { color: COLORS.up, lineWidth: 2, priceFormat: { type: "price", precision: 3, minMove: 0.001 } }, 1);
    s.down = addSeries(Line, { color: COLORS.down, lineWidth: 1, priceFormat: { type: "price", precision: 3, minMove: 0.001 } }, 1);
    s.twap = addSeries(Line, { color: COLORS.twap, lineWidth: 2 }, 2);
    s.volume = addSeries(Hist, { color: COLORS.vol, priceScaleId: "vol", lastValueVisible: false }, 2);
    if (s.up.moveToPane) s.up.moveToPane(1);
    if (s.down.moveToPane) s.down.moveToPane(1);
    if (s.twap.moveToPane) s.twap.moveToPane(2);
    if (s.volume.moveToPane) s.volume.moveToPane(2);
    s.up.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }) });
    s.down.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }) });
    if (s.volume.priceScale) s.volume.priceScale().applyOptions({ scaleMargins: { top: 0.72, bottom: 0 }, visible: false });
    if (state.chart.panes) {
      const panes = state.chart.panes();
      if (panes[0]?.setStretchFactor) panes[0].setStretchFactor(2.4);
      if (panes[1]?.setStretchFactor) panes[1].setStretchFactor(1.4);
      if (panes[2]?.setStretchFactor) panes[2].setStretchFactor(1);
    }
    s._dashed = dashed;
    const resize = () => state.chart.applyOptions({ width: el.clientWidth || 800, height: el.clientHeight || 520 });
    resize();
    new ResizeObserver(resize).observe(el);
  }

  function setPtb(price) {
    const n = Number(price);
    if (!Number.isFinite(n) || !state.series.spot) return;
    if (state.ptbLine) {
      state.ptbLine.applyOptions({ price: n });
      return;
    }
    state.ptbLine = state.series.spot.createPriceLine({
      price: n,
      color: COLORS.ptb,
      lineWidth: 1,
      lineStyle: state.series._dashed,
      axisLabelVisible: true,
      title: "PTB",
    });
  }

  function renderChart(data) {
    ensureChart();
    const prices = data.prices || {};
    state.series.spot.setData(uniqueLine(prices.binance_spot, "price"));
    state.series.fut.setData(uniqueLine(prices.binance_futures, "price"));
    state.series.coinbase.setData(uniqueLine(prices.coinbase_spot, "price"));
    state.series.bybit.setData(uniqueLine(prices.bybit_spot, "price"));
    state.series.up.setData(uniqueLine(data.odds, "up_mid"));
    state.series.down.setData(uniqueLine(data.odds, "down_mid"));
    state.series.twap.setData(uniqueLine(data.twap, "value"));
    state.series.volume.setData(uniqueLine((data.volume || []).filter((r) => r.source === "binance_spot"), "base_volume"));
    setPtb(data.window?.price_to_beat);
    const from = Number(data.window?.window_start);
    const to = Number(data.window?.window_end);
    if (Number.isFinite(from) && Number.isFinite(to) && to > from) {
      try {
        state.chart.timeScale().setVisibleRange({ from, to });
      } catch {
        state.chart.timeScale().fitContent();
      }
    }
  }

  function sourceLabel(source) {
    if (source === "polymarket") return { text: "Polymarket verified", cls: "verified" };
    if (!source) return { text: "unverified", cls: "guessed" };
    return { text: `guessed from ${source}`, cls: "guessed" };
  }

  function renderHeader(data) {
    const w = data.window || {};
    const counts = data.counts || {};
    $("kpiWhen").textContent = fmtWhen(w.window_start);
    $("kpiWhenMeta").textContent = w.slug || "—";
    const outcome = (w.outcome || "").toLowerCase();
    const outEl = $("kpiOutcome");
    outEl.textContent = outcome ? outcome.toUpperCase() : "—";
    outEl.className = `value ${outcome === "up" ? "outcome-up" : outcome === "down" ? "outcome-down" : ""}`;
    const src = sourceLabel(w.outcome_source);
    $("kpiOutcomeMeta").innerHTML = `<span class="source-badge ${src.cls}">${src.text}</span>`;
    $("kpiPtb").textContent = fmtNum(w.price_to_beat, 2);
    $("kpiPtbMeta").textContent = w.price_to_beat_source || (w.price_to_beat_gamma ? "gamma" : "rtds") || "—";
    $("kpiFinal").textContent = fmtNum(w.final_price, 2);
    const priceCount = (counts.binance_spot || 0) + (counts.binance_futures || 0) + (counts.coinbase_spot || 0) + (counts.bybit_spot || 0);
    $("kpiTicks").textContent = `${counts.odds || 0}`;
    $("kpiTicksMeta").textContent = `${priceCount} prices · ${counts.twap || 0} TWAP · ${counts.volume || 0} vol`;
    const poly = $("polyLink");
    poly.href = polymarketUrl(w.slug);
    poly.hidden = !w.slug;
    document.querySelectorAll(".replay-tab").forEach((btn) => {
      const key = btn.dataset.tab;
      const n = key === "odds" || key === "twap" || key === "volume" ? (counts[key] || 0) : (counts[key] || 0);
      let label = btn.dataset.label;
      if (!label) {
        btn.dataset.label = btn.textContent;
        label = btn.textContent;
      }
      btn.innerHTML = `${label}<span class="count">${n}</span>`;
    });
  }

  function tabRows() {
    const data = state.data;
    if (!data) return [];
    const tab = state.tab;
    if (tab === "odds") return data.odds || [];
    if (tab === "twap") return data.twap || [];
    if (tab === "volume") return data.volume || [];
    return (data.prices && data.prices[tab]) || [];
  }

  function cmp(a, b) {
    if (a == null && b == null) return 0;
    if (a == null) return 1;
    if (b == null) return -1;
    if (typeof a === "number" && typeof b === "number") return a - b;
    const na = Number(a);
    const nb = Number(b);
    if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  }

  function filteredRows() {
    let rows = tabRows();
    if (state.search) {
      const q = state.search.toLowerCase();
      rows = rows.filter((row) => Object.values(row).some((v) => v != null && String(v).toLowerCase().includes(q)));
    }
    const key = state.sortKey === "elapsed" || state.sortKey === "clock" ? "recv_ts_ms" : state.sortKey;
    return rows.slice().sort((a, b) => state.sortDir * cmp(a[key], b[key]));
  }

  function cellValue(row, key) {
    const start = state.data?.window?.window_start;
    if (key === "elapsed") return fmtElapsed(row.recv_ts_ms, start);
    if (key === "clock") return fmtClock(row.recv_ts_ms);
    if (key === "is_closed") return row.is_closed ? "yes" : "no";
    const raw = row[key];
    if (raw == null || raw === "") return "—";
    if (["up_bid", "up_ask", "up_mid", "down_bid", "down_ask", "down_mid", "last_trade_price"].includes(key)) {
      return fmtNum(raw, 3);
    }
    if (["price", "value", "base_volume", "quote_volume", "size"].includes(key)) {
      return fmtNum(raw, 4);
    }
    return String(raw);
  }

  function renderTable() {
    const spec = TABS[state.tab];
    const filtered = filteredRows();
    const total = filtered.length;
    const totalPages = Math.max(1, Math.ceil(total / state.pageSize));
    state.page = Math.min(Math.max(0, state.page), totalPages - 1);
    const start = state.page * state.pageSize;
    const pageRows = filtered.slice(start, start + state.pageSize);

    const head = $("ticksHead");
    head.innerHTML = `<tr>${spec.columns.map(([key, label]) => `<th data-sort="${key}">${label}</th>`).join("")}</tr>`;
    const body = $("ticksBody");
    body.replaceChildren();
    if (!pageRows.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="${spec.columns.length}" class="muted">No ticks in this feed.</td>`;
      body.appendChild(tr);
    } else {
      const frag = document.createDocumentFragment();
      for (const row of pageRows) {
        const tr = document.createElement("tr");
        tr.innerHTML = spec.columns.map(([key]) => {
          const cls = ["up_mid", "down_mid", "price", "value", "base_volume"].includes(key) ? " class=\"num\"" : "";
          return `<td${cls}>${cellValue(row, key)}</td>`;
        }).join("");
        frag.appendChild(tr);
      }
      body.appendChild(frag);
    }

    $("tickMeta").textContent = `${total.toLocaleString()} rows`;
    $("ticksPageLabel").textContent = total ? `${start + 1}–${Math.min(start + state.pageSize, total)} of ${total}` : "0 of 0";
    $("ticksPrevBtn").disabled = state.page <= 0;
    $("ticksNextBtn").disabled = start + state.pageSize >= total;

    head.querySelectorAll("th[data-sort]").forEach((th) => {
      th.style.cursor = "pointer";
      const key = th.getAttribute("data-sort");
      th.textContent = TABS[state.tab].columns.find((c) => c[0] === key)?.[1] || key;
      if (key === state.sortKey) th.textContent += state.sortDir === 1 ? " ↑" : " ↓";
      th.onclick = () => {
        if (state.sortKey === key) state.sortDir *= -1;
        else {
          state.sortKey = key;
          state.sortDir = key === "recv_ts_ms" || key === "elapsed" || key === "clock" ? 1 : -1;
        }
        renderTable();
      };
    });
  }

  function setTab(tab) {
    state.tab = tab;
    state.page = 0;
    state.sortKey = "recv_ts_ms";
    state.sortDir = 1;
    document.querySelectorAll(".replay-tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tab));
    renderTable();
  }

  async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    return res.json();
  }

  function fillWindowSelect() {
    const sel = $("windowSelect");
    sel.innerHTML = state.windows.map((w) => {
      const when = fmtWhen(w.window_start);
      const side = w.outcome ? ` · ${String(w.outcome).toUpperCase()}` : "";
      return `<option value="${w.id}">${when}${side}</option>`;
    }).join("");
    if (state.windowId) sel.value = String(state.windowId);
  }

  function adjacentId(delta) {
    const ids = state.windows.map((w) => w.id);
    const idx = ids.indexOf(state.windowId);
    if (idx < 0) return null;
    return ids[idx + delta] || null;
  }

  async function loadWindow(windowId) {
    state.windowId = windowId;
    $("loadStatus").textContent = "Loading ticks…";
    showError("");
    const url = new URL(location.href);
    url.searchParams.set("window_id", String(windowId));
    history.replaceState(null, "", url);
    try {
      const data = await fetchJson(`/api/window/${windowId}/replay`);
      state.data = data;
      renderHeader(data);
      renderChart(data);
      state.page = 0;
      renderTable();
      $("loadStatus").textContent = data.window?.slug || "";
      fillWindowSelect();
      $("prevBtn").disabled = !adjacentId(1);
      $("nextBtn").disabled = !adjacentId(-1);
    } catch (err) {
      showError(err.message || String(err));
      $("loadStatus").textContent = "Failed";
    }
  }

  async function init() {
    document.querySelectorAll(".replay-tab").forEach((btn) => {
      btn.addEventListener("click", () => setTab(btn.dataset.tab));
    });
    $("tickSearch").addEventListener("input", (event) => {
      state.search = event.target.value.trim();
      state.page = 0;
      renderTable();
    });
    $("ticksPrevBtn").addEventListener("click", () => { state.page -= 1; renderTable(); });
    $("ticksNextBtn").addEventListener("click", () => { state.page += 1; renderTable(); });
    $("windowSelect").addEventListener("change", (event) => loadWindow(Number(event.target.value)));
    $("prevBtn").addEventListener("click", () => {
      const id = adjacentId(1);
      if (id) loadWindow(id);
    });
    $("nextBtn").addEventListener("click", () => {
      const id = adjacentId(-1);
      if (id) loadWindow(id);
    });

    try {
      const listing = await fetchJson("/api/windows?limit=400");
      state.windows = listing.windows || [];
      fillWindowSelect();
      const requested = queryWindowId() || (state.windows[0] && state.windows[0].id);
      if (requested) await loadWindow(requested);
      else $("loadStatus").textContent = "No windows in the database yet.";
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  init();
})();
