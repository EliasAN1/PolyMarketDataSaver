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
  const OVERLAY_MAP = {
    binance_futures: "fut",
    coinbase_spot: "coinbase",
    bybit_spot: "bybit",
  };

  const els = {
    slug: document.getElementById("slug"),
    subtitle: document.getElementById("subtitle"),
    countdown: document.getElementById("countdown"),
    countdownFill: document.getElementById("countdownFill"),
    windowRange: document.getElementById("windowRange"),
    livePill: document.getElementById("livePill"),
    followLive: document.getElementById("followLive"),
    windowSelect: document.getElementById("windowSelect"),
    replayLink: document.getElementById("replayLink"),
    errorBox: document.getElementById("errorBox"),
    ptbValue: document.getElementById("ptbValue"),
    ptbMeta: document.getElementById("ptbMeta"),
    upValue: document.getElementById("upValue"),
    upMeta: document.getElementById("upMeta"),
    deltaValue: document.getElementById("deltaValue"),
    deltaMeta: document.getElementById("deltaMeta"),
    sideValue: document.getElementById("sideValue"),
    sideMeta: document.getElementById("sideMeta"),
    volBn: document.getElementById("volBn"),
    volCb: document.getElementById("volCb"),
    feedMode: document.getElementById("feedMode"),
    oddsRate: document.getElementById("oddsRate"),
    priceRate: document.getElementById("priceRate"),
    tickAge: document.getElementById("tickAge"),
    eventsBody: document.getElementById("eventsBody"),
    chart: document.getElementById("chart"),
  };

  let followLive = true;
  let selectedWindowId = null;
  let socket = null;
  let reconnectTimer = null;
  let chart = null;
  let series = {};
  let ptbLine = null;
  let lastTimes = {};
  let currentWindow = null;
  let latestHero = null;
  let pendingSnapshot = null;
  let pendingAppends = emptyAppends();
  let rafId = 0;
  let lastEvents = [];

  function emptyAppends() {
    return { odds: [], twap: [], volume: [], prices: {} };
  }

  function fmtNum(value, digits) {
    if (value == null || value === "") return "—";
    const n = Number(value);
    if (Number.isNaN(n)) return String(value);
    return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  }

  function fmtSigned(value, digits) {
    if (value == null || Number.isNaN(value)) return "—";
    const sign = value > 0 ? "+" : "";
    return sign + value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function fmtTime(ms) {
    if (!ms) return "—";
    return new Date(Number(ms)).toLocaleTimeString();
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function toTime(ms) {
    const t = Math.floor(Number(ms) / 1000);
    return Number.isFinite(t) && t > 0 ? t : null;
  }

  function uniqueLine(rows, valueKey) {
    const byTime = new Map();
    for (const row of rows || []) {
      const time = toTime(row.recv_ts_ms);
      const raw = row[valueKey] ?? row.price ?? row.value ?? row.up_mid;
      const value = Number(raw);
      if (time == null || !Number.isFinite(value)) continue;
      byTime.set(time, { time, value });
    }
    return [...byTime.values()];
  }

  function setReplayLink(windowInfo) {
    if (!els.replayLink) return;
    const id = windowInfo?.id;
    els.replayLink.href = id ? `/replay?window_id=${id}` : "/replay";
  }

  function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
  }

  function setClass(el, name, on) {
    el.classList.toggle(name, on);
  }

  function addSeries(type, options, pane) {
    if (chart.addSeries && type) {
      return chart.addSeries(type, options, pane);
    }
    const fallback = {
      Line: "addLineSeries",
      Histogram: "addHistogramSeries",
    }[options._kind || "Line"];
    const created = chart[fallback](options);
    if (pane && created.moveToPane) created.moveToPane(pane);
    return created;
  }

  function ensureChart() {
    if (chart) return;
    if (!LC) {
      showError("Chart library failed to load.");
      return;
    }
    const dashed = LC.LineStyle ? LC.LineStyle.Dashed : 2;
    const dotted = LC.LineStyle ? LC.LineStyle.Dotted : 1;
    chart = LC.createChart(els.chart, {
      width: els.chart.clientWidth || 800,
      height: els.chart.clientHeight || 520,
      layout: {
        background: { type: LC.ColorType ? LC.ColorType.Solid : "solid", color: COLORS.bg },
        textColor: COLORS.text,
        fontFamily: "Inter, Segoe UI, system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      crosshair: { mode: LC.CrosshairMode ? LC.CrosshairMode.Normal : 1 },
      rightPriceScale: { borderColor: COLORS.grid },
      timeScale: {
        borderColor: COLORS.grid,
        timeVisible: true,
        secondsVisible: true,
        rightOffset: 6,
      },
    });

    const Line = LC.LineSeries;
    const Hist = LC.HistogramSeries;
    series.spot = addSeries(Line, {
      color: COLORS.spot,
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    }, 0);
    series.fut = addSeries(Line, { color: COLORS.fut, lineWidth: 1, visible: false, lastValueVisible: false }, 0);
    series.coinbase = addSeries(Line, { color: COLORS.coinbase, lineWidth: 1, visible: false, lastValueVisible: false }, 0);
    series.bybit = addSeries(Line, { color: COLORS.bybit, lineWidth: 1, visible: false, lastValueVisible: false }, 0);
    series.up = addSeries(Line, {
      color: COLORS.up,
      lineWidth: 2,
      lastValueVisible: true,
      priceFormat: { type: "price", precision: 3, minMove: 0.001 },
    }, 1);
    series.twap = addSeries(Line, { color: COLORS.twap, lineWidth: 2, lastValueVisible: true }, 2);
    series.volume = addSeries(Hist, { color: COLORS.vol, priceScaleId: "vol", lastValueVisible: false }, 2);
    if (series.up.moveToPane) series.up.moveToPane(1);
    if (series.twap.moveToPane) series.twap.moveToPane(2);
    if (series.volume.moveToPane) series.volume.moveToPane(2);

    series.up.createPriceLine({
      price: 0.5,
      color: COLORS.text,
      lineWidth: 1,
      lineStyle: dotted,
      axisLabelVisible: true,
      title: "0.5",
    });
    series.up.applyOptions({
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }),
    });
    if (series.volume.priceScale) {
      series.volume.priceScale().applyOptions({
        scaleMargins: { top: 0.72, bottom: 0 },
        visible: false,
      });
    }
    if (chart.panes) {
      const panes = chart.panes();
      if (panes[0]?.setStretchFactor) panes[0].setStretchFactor(2.4);
      if (panes[1]?.setStretchFactor) panes[1].setStretchFactor(1.4);
      if (panes[2]?.setStretchFactor) panes[2].setStretchFactor(1);
    }

    series._dashed = dashed;
    lastTimes = {};
    const resize = () => {
      if (!chart) return;
      chart.applyOptions({
        width: els.chart.clientWidth || 800,
        height: els.chart.clientHeight || 520,
      });
    };
    resize();
    new ResizeObserver(resize).observe(els.chart);
  }

  function resetLastTimes() {
    lastTimes = {};
  }

  function setPtb(price) {
    const n = Number(price);
    if (!Number.isFinite(n) || !series.spot) return;
    if (ptbLine) {
      ptbLine.applyOptions({ price: n });
      return;
    }
    ptbLine = series.spot.createPriceLine({
      price: n,
      color: COLORS.ptb,
      lineWidth: 1,
      lineStyle: series._dashed,
      axisLabelVisible: true,
      title: "PTB",
    });
  }

  function applySeriesData(key, s, rows, valueKey) {
    const data = uniqueLine(rows, valueKey);
    s.setData(data);
    if (data.length) lastTimes[key] = data[data.length - 1].time;
  }

  function updateSeries(key, s, ms, value) {
    const time = toTime(ms);
    const num = Number(value);
    if (!s || time == null || !Number.isFinite(num)) return;
    const last = lastTimes[key] || 0;
    if (time < last) return;
    lastTimes[key] = time;
    s.update({ time, value: num });
  }

  function setVisibleWindow(windowInfo) {
    if (!chart || !windowInfo?.window_start || !windowInfo?.window_end) return;
    const from = Number(windowInfo.window_start);
    const to = Number(windowInfo.window_end);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return;
    try {
      chart.timeScale().setVisibleRange({ from, to });
    } catch {
      chart.timeScale().fitContent();
    }
  }

  function applySnapshot(data) {
    ensureChart();
    resetLastTimes();
    currentWindow = data.window || null;
    setReplayLink(currentWindow);
    applySeriesData("spot", series.spot, data.chart_prices?.binance_spot, "price");
    applySeriesData("fut", series.fut, data.chart_prices?.binance_futures, "price");
    applySeriesData("coinbase", series.coinbase, data.chart_prices?.coinbase_spot, "price");
    applySeriesData("bybit", series.bybit, data.chart_prices?.bybit_spot, "price");
    applySeriesData("up", series.up, data.chart_odds, "up_mid");
    applySeriesData("twap", series.twap, data.chart_twap, "value");
    applySeriesData("volume", series.volume, data.chart_volume, "value");
    setPtb(currentWindow?.price_to_beat);
    if (series.twap.setMarkers) {
      const start = Number(currentWindow?.window_start);
      if (Number.isFinite(start) && currentWindow?.price_to_beat_rtds) {
        series.twap.setMarkers([{
          time: start + 60,
          position: "aboveBar",
          color: COLORS.ptb,
          shape: "circle",
          text: "PTB",
        }]);
      } else {
        series.twap.setMarkers([]);
      }
    }
    setVisibleWindow(currentWindow);
    lastEvents = data.recent_odds || [];
    renderEvents();
    if (currentWindow?.id) selectedWindowId = currentWindow.id;
  }

  function applyAppends(appends) {
    if (!chart) ensureChart();
    for (const row of appends.odds || []) {
      updateSeries("up", series.up, row.recv_ts_ms, row.up_mid);
    }
    for (const row of appends.twap || []) {
      updateSeries("twap", series.twap, row.recv_ts_ms, row.value);
    }
    for (const row of appends.volume || []) {
      updateSeries("volume", series.volume, row.recv_ts_ms, row.value);
    }
    const prices = appends.prices || {};
    for (const row of prices.binance_spot || []) {
      updateSeries("spot", series.spot, row.recv_ts_ms, row.price);
    }
    for (const row of prices.binance_futures || []) {
      updateSeries("fut", series.fut, row.recv_ts_ms, row.price);
    }
    for (const row of prices.coinbase_spot || []) {
      updateSeries("coinbase", series.coinbase, row.recv_ts_ms, row.price);
    }
    for (const row of prices.bybit_spot || []) {
      updateSeries("bybit", series.bybit, row.recv_ts_ms, row.price);
    }
  }

  function mergeAppends(delta) {
    pendingAppends.odds.push(...(delta.append_odds || []));
    pendingAppends.twap.push(...(delta.append_twap || []));
    pendingAppends.volume.push(...(delta.append_volume || []));
    const prices = delta.append_prices || {};
    for (const [source, rows] of Object.entries(prices)) {
      if (!pendingAppends.prices[source]) pendingAppends.prices[source] = [];
      pendingAppends.prices[source].push(...rows);
    }
    if (delta.append_events) lastEvents = delta.append_events;
  }

  function queueMessage(data) {
    latestHero = data;
    if (data.window) {
      currentWindow = data.window;
      setReplayLink(currentWindow);
    }
    if (data.type === "snapshot") {
      pendingSnapshot = data;
      pendingAppends = emptyAppends();
    } else {
      mergeAppends(data);
    }
    if (!rafId) rafId = requestAnimationFrame(flush);
  }

  function flush() {
    rafId = 0;
    if (pendingSnapshot) {
      applySnapshot(pendingSnapshot);
      pendingSnapshot = null;
    }
    if (
      pendingAppends.odds.length ||
      pendingAppends.twap.length ||
      pendingAppends.volume.length ||
      Object.keys(pendingAppends.prices).length
    ) {
      applyAppends(pendingAppends);
      pendingAppends = emptyAppends();
    }
    if (latestHero) renderHero(latestHero);
  }

  function ptbOf(windowInfo) {
    if (!windowInfo) return null;
    const n = Number(windowInfo.price_to_beat ?? windowInfo.price_to_beat_gamma ?? windowInfo.price_to_beat_rtds);
    return Number.isFinite(n) ? n : null;
  }

  function renderHero(data) {
    const windowInfo = data.window || currentWindow;
    const odds = data.latest_odds || {};
    const prices = data.latest_prices || {};
    const vol = data.latest_volume || {};
    const ingest = data.ingest || {};
    const ptb = ptbOf(windowInfo);
    const spot = Number(prices.binance_spot?.price);
    const up = odds.up_mid == null ? null : Number(odds.up_mid);
    const delta = Number.isFinite(spot) && ptb != null ? spot - ptb : null;

    setText(els.slug, windowInfo?.slug || "BTC 5m");
    if (followLive) {
      setText(els.subtitle, windowInfo ? "Will BTC finish this 5m above the price to beat?" : "Waiting for collector");
    } else {
      setText(els.subtitle, "Viewing a past 5-minute window");
    }
    setText(els.ptbValue, fmtNum(ptb, 2));
    if (windowInfo?.price_to_beat_gamma) setText(els.ptbMeta, "Gamma official");
    else if (windowInfo?.price_to_beat_source === "binance_open") {
      setText(els.ptbMeta, "Binance 1m open (joined late)");
    } else if (windowInfo?.price_to_beat_source === "previous_final") {
      setText(els.ptbMeta, "Previous window Chainlink close");
    } else if (windowInfo?.price_to_beat_rtds) {
      setText(els.ptbMeta, "60s TWAP at window open");
    } else setText(els.ptbMeta, "waiting…");

    if (up == null || Number.isNaN(up)) {
      setText(els.upValue, "—");
      setText(els.upMeta, "waiting for book");
      els.upValue.className = "value";
    } else {
      setText(els.upValue, fmtNum(up, 3));
      setText(els.upMeta, `bid ${fmtNum(odds.up_bid, 3)} / ask ${fmtNum(odds.up_ask, 3)}`);
      els.upValue.className = "value " + (up >= 0.5 ? "up" : "down");
    }

    if (delta == null) {
      setText(els.deltaValue, "—");
      els.deltaValue.className = "value";
    } else {
      setText(els.deltaValue, fmtSigned(delta, 2));
      els.deltaValue.className = "value " + (delta >= 0 ? "up" : "down");
    }
    setText(els.deltaMeta, "Binance spot minus PTB");

    if (delta == null) {
      setText(els.sideValue, "—");
      els.sideValue.className = "value";
      setText(els.sideMeta, "spot vs price to beat");
    } else {
      const side = delta >= 0 ? "UP" : "DOWN";
      setText(els.sideValue, side);
      els.sideValue.className = "value " + (delta >= 0 ? "up" : "down");
      setText(els.sideMeta, delta >= 0 ? "spot is above PTB" : "spot is below PTB");
    }

    setText(els.volBn, vol.binance_spot ? `${fmtNum(vol.binance_spot.base_volume, 3)} BTC` : "—");
    setText(els.volCb, vol.coinbase_spot ? `${fmtNum(vol.coinbase_spot.base_volume, 3)} BTC` : "—");
    setText(els.oddsRate, `${ingest.odds_per_sec ?? 0}/s`);
    setText(els.priceRate, `${ingest.prices_per_sec ?? 0}/s`);

    for (const source of ["binance_spot", "binance_futures", "coinbase_spot", "bybit_spot"]) {
      const row = document.querySelector(`tr[data-source="${source}"]`);
      if (!row) continue;
      const tick = prices[source];
      const px = tick ? Number(tick.price) : NaN;
      row.querySelector(".px").textContent = Number.isFinite(px) ? fmtNum(px, 2) : "—";
      if (Number.isFinite(px) && ptb != null) {
        const vs = px - ptb;
        const cell = row.querySelector(".vs");
        cell.textContent = fmtSigned(vs, 2);
        cell.style.color = vs >= 0 ? COLORS.up : COLORS.down;
      } else {
        const cell = row.querySelector(".vs");
        cell.textContent = "—";
        cell.style.color = "";
      }
    }

    if (data.type === "snapshot" || data.append_events) renderEvents();
    if (ptb != null) setPtb(ptb);
  }

  function renderEvents() {
    els.eventsBody.innerHTML = (lastEvents || []).slice(0, 12).map((row) => `
      <tr>
        <td>${fmtTime(row.recv_ts_ms)}</td>
        <td>${row.event_type || "—"}</td>
        <td>${fmtNum(row.up_mid, 3)}</td>
      </tr>
    `).join("");
  }

  function tickClock() {
    const windowInfo = currentWindow;
    if (!windowInfo?.window_end || !windowInfo?.window_start) {
      setText(els.countdown, "—:——");
      els.countdownFill.style.width = "0%";
      return;
    }
    const now = Date.now() / 1000;
    const start = Number(windowInfo.window_start);
    const end = Number(windowInfo.window_end);
    const left = Math.max(0, Math.ceil(end - now));
    const elapsed = Math.min(300, Math.max(0, now - start));
    setText(els.countdown, `${Math.floor(left / 60)}:${pad(left % 60)}`);
    els.countdownFill.style.width = `${(elapsed / 300) * 100}%`;
    const startMs = start * 1000;
    const endMs = end * 1000;
    setText(
      els.windowRange,
      `${new Date(startMs).toLocaleTimeString()} – ${new Date(endMs).toLocaleTimeString()}`
    );

    const latestTs = Math.max(
      Number(latestHero?.latest_odds?.recv_ts_ms || 0),
      Number(latestHero?.latest_prices?.binance_spot?.recv_ts_ms || 0),
      0
    );
    setText(els.tickAge, latestTs ? `${Math.max(0, Date.now() - latestTs)} ms` : "—");
  }

  function setFeed(text, mode) {
    setText(els.feedMode, text);
    els.livePill.textContent = mode === "live" ? "LIVE" : mode === "past" ? "PAST" : "OFFLINE";
    els.livePill.className = "pill" + (mode === "live" ? " live" : mode === "past" ? " past" : "");
  }

  async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    return res.json();
  }

  async function loadWindows() {
    const data = await fetchJson("/api/windows");
    const current = els.windowSelect.value;
    els.windowSelect.innerHTML = (data.windows || []).map((w) => {
      const ptb = w.price_to_beat || w.price_to_beat_rtds || "";
      return `<option value="${w.id}">${w.slug}${ptb ? " · " + Number(ptb).toFixed(0) : ""}</option>`;
    }).join("");
    if (followLive && data.windows?.length) {
      selectedWindowId = data.windows[0].id;
      els.windowSelect.value = String(selectedWindowId);
    } else if (current && [...els.windowSelect.options].some((o) => o.value === current)) {
      els.windowSelect.value = current;
    }
  }

  async function loadHistory(windowId) {
    setFeed("history", "past");
    const [status, seriesData] = await Promise.all([
      fetchJson(`/api/status?window_id=${windowId}`),
      fetchJson(`/api/series?window_id=${windowId}&points=800`),
    ]);
    const snapshot = {
      type: "snapshot",
      connected: true,
      window: status.window,
      latest_odds: status.latest_odds,
      latest_prices: status.latest_prices,
      latest_volume: status.latest_volume,
      ingest: { odds_per_sec: 0, prices_per_sec: 0, prices_by_source: {} },
      chart_odds: seriesData.odds || [],
      chart_prices: seriesData.prices || {},
      chart_twap: seriesData.twap || [],
      chart_volume: seriesData.volume || [],
      recent_odds: [],
    };
    queueMessage(snapshot);
    showError("");
  }

  function connectLive() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws/live`);
    socket.onopen = () => setFeed("websocket", "live");
    socket.onmessage = (event) => {
      if (!followLive) return;
      const data = JSON.parse(event.data);
      if (data.connected === false) {
        setFeed("collector offline", "offline");
        showError("Collector is not connected. Run python -m pmdsaver.");
      } else {
        setFeed("websocket", "live");
        showError("");
      }
      queueMessage(data);
    };
    socket.onclose = () => {
      if (!followLive) return;
      setFeed("reconnecting", "offline");
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connectLive, 1000);
    };
    socket.onerror = () => socket.close();
  }

  document.querySelectorAll(".overlay").forEach((input) => {
    input.addEventListener("change", () => {
      const key = OVERLAY_MAP[input.dataset.source];
      const s = series[key];
      if (s) s.applyOptions({ visible: input.checked });
    });
  });

  els.windowSelect.addEventListener("change", async (event) => {
    selectedWindowId = Number(event.target.value);
    followLive = false;
    els.followLive.checked = false;
    if (socket) socket.close();
    await loadHistory(selectedWindowId);
  });

  els.followLive.addEventListener("change", (event) => {
    followLive = event.target.checked;
    if (followLive) {
      connectLive();
    } else if (selectedWindowId) {
      loadHistory(selectedWindowId);
    }
  });

  ensureChart();
  setInterval(tickClock, 250);
  loadWindows().catch(() => {});
  connectLive();
  setInterval(() => {
    if (followLive) loadWindows().catch(() => {});
  }, 15000);
})();
