/* Web Worker: coarse grid search over enabled entry filters.
 * Keeps a copy of the tape in worker memory so "Find best settings" never
 * re-sends the (large) tape payload — only params are exchanged after the
 * initial load.
 */
importScripts("/static/lab_engine.js?v=5");

let windows = [];

function buildCandidates(dim) {
  const values = [];
  const steps = Math.max(1, Math.round((dim.max - dim.min) / dim.step));
  for (let i = 0; i <= steps; i++) {
    values.push(Math.round((dim.min + i * dim.step) * 1000) / 1000);
  }
  return values;
}

self.onmessage = (event) => {
  const msg = event.data || {};
  if (msg.type === "load") {
    windows = msg.windows || [];
    self.postMessage({ type: "loaded", count: windows.length });
  } else if (msg.type === "search") {
    runSearch(msg.baseParams, msg.dims || {}, msg.minTrades || 15);
  }
};

function runSearch(baseParams, dims, minTrades) {
  const keys = Object.keys(dims);
  const grids = keys.map((k) => buildCandidates(dims[k]));
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
      if (
        (params.oddsLo != null &&
          params.oddsHi != null &&
          Number(params.oddsLo) > Number(params.oddsHi)) ||
        (params.elapsedFromMin != null &&
          params.elapsedToMin != null &&
          Number(params.elapsedFromMin) > Number(params.elapsedToMin)) ||
        (params.minDistance != null &&
          params.maxDistance != null &&
          Number(params.minDistance) > Number(params.maxDistance))
      ) {
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
