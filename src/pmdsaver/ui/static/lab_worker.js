/* Web Worker for "Find best settings".
 *
 * One primary worker keeps a copy of the tape (so the tape payload is sent
 * once) and serves: "prepare" (reduce the tape for the fast search),
 * "evaluate" (full LabEngine.evaluate for given params) and "search" (the
 * original one-combo-at-a-time scan, used when the fast path does not cover
 * the enabled filters). Pool workers only receive the reduced tape and run
 * "fastSearch" over their share of the grid.
 */
importScripts("/static/lab_engine.js?v=8");

let windows = [];

self.onmessage = (event) => {
  const msg = event.data || {};
  if (msg.type === "load") {
    windows = msg.windows || [];
    self.postMessage({ type: "loaded", count: windows.length });
  } else if (msg.type === "prepare") {
    const plan = self.LabEngine.searchPlan(msg.baseParams, msg.dims || {});
    const prep = self.LabEngine.prepareSearch(windows, msg.baseParams, plan);
    self.postMessage({ type: "prepared", id: msg.id, prep }, self.LabEngine.prepTransferables(prep));
  } else if (msg.type === "evaluate") {
    const summaries = (msg.list || []).map((params) => self.LabEngine.evaluate(windows, params).summary);
    self.postMessage({ type: "evaluated", id: msg.id, summaries });
  } else if (msg.type === "fastSearch") {
    const plan = self.LabEngine.searchPlan(msg.baseParams, msg.dims || {});
    const { top, done } = self.LabEngine.fastSearchChunk(msg.prep, plan, msg.baseParams, {
      start: msg.start,
      step: msg.step,
      minTrades: msg.minTrades,
      limit: msg.limit,
      onProgress: (n) => self.postMessage({ type: "fastProgress", id: msg.id, done: n }),
    });
    self.postMessage({ type: "fastResult", id: msg.id, top, done });
  } else if (msg.type === "search") {
    runSearch(msg.baseParams, msg.dims || {}, msg.minTrades || 15);
  }
};

function runSearch(baseParams, dims, minTrades) {
  const keys = Object.keys(dims);
  const grids = keys.map((k) => self.LabEngine.buildCandidates(dims[k]));
  const total = grids.reduce((acc, g) => acc * g.length, 1) || 1;

  if (keys.length === 0) {
    const { summary } = self.LabEngine.evaluate(windows, baseParams);
    self.postMessage({
      type: "result",
      results: [{ params: baseParams, summary }],
      total: 1,
      scanned: 1,
    });
    return;
  }

  const indices = keys.map(() => 0);
  let results = [];
  let done = 0;

  function currentParams() {
    const params = Object.assign({}, baseParams);
    keys.forEach((k, i) => {
      params[k] = grids[i][indices[i]];
    });
    return params;
  }

  function advance() {
    for (let i = keys.length - 1; i >= 0; i--) {
      indices[i]++;
      if (indices[i] < grids[i].length) return true;
      indices[i] = 0;
    }
    return false;
  }

  const BATCH = 150;

  function step() {
    let processed = 0;
    while (processed < BATCH) {
      const params = currentParams();
      if (self.LabEngine.comboInvalid(params)) {
        done++;
        processed++;
        if (!advance()) {
          finish();
          return;
        }
        continue;
      }
      const { summary } = self.LabEngine.evaluate(windows, params);
      if (summary.trades >= minTrades) {
        results.push({ params, summary });
      }
      done++;
      processed++;
      if (!advance()) {
        finish();
        return;
      }
    }
    self.postMessage({ type: "progress", done, total });
    setTimeout(step, 0);
  }

  function finish() {
    results.sort((a, b) => b.summary.netPnl - a.summary.netPnl);
    self.postMessage({
      type: "result",
      results: results.slice(0, 10),
      total,
      scanned: done,
    });
  }

  self.postMessage({ type: "progress", done: 0, total });
  step();
}
