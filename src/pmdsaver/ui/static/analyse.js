function $(id) {
  return document.getElementById(id);
}

function num(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) ? value : fallback;
}

function fmtPct(value) {
  if (value == null) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function fmtNum(value, digits) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function svgEl(name, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) {
    el.setAttribute(key, String(value));
  }
  return el;
}

function clearSvg(svg) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
}

function drawBars(svgId, buckets, { expected } = {}) {
  const svg = $(svgId);
  clearSvg(svg);
  const w = 800;
  const h = 220;
  const pad = { l: 36, r: 16, t: 16, b: 36 };
  if (!buckets.length) {
    svg.appendChild(svgEl("text", { x: 20, y: 28, fill: "#8ea0b8", "font-size": 12 }));
    svg.lastChild.textContent = "No data.";
    return;
  }
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;
  const n = buckets.length;
  const gap = 8;
  const barW = Math.max(8, innerW / n - gap);
  svg.appendChild(svgEl("line", {
    x1: pad.l, x2: w - pad.r, y1: pad.t + innerH, y2: pad.t + innerH, stroke: "#243044",
  }));
  for (const frac of [0.25, 0.5, 0.75, 1]) {
    const y = pad.t + innerH - frac * innerH;
    svg.appendChild(svgEl("line", {
      x1: pad.l, x2: w - pad.r, y1: y, y2: y, stroke: "#1a2330",
    }));
  }
  buckets.forEach((bucket, i) => {
    const x = pad.l + i * (innerW / n) + gap / 2;
    const rate = bucket.up_rate;
    const barH = rate == null ? 0 : rate * innerH;
    const y = pad.t + innerH - barH;
    svg.appendChild(svgEl("rect", {
      x,
      y,
      width: barW,
      height: Math.max(0, barH),
      fill: rate == null ? "#243044" : "#5b8cff",
      opacity: rate == null ? 0.4 : 0.9,
    }));
    if (expected && expected[i] != null) {
      const ey = pad.t + innerH - expected[i] * innerH;
      svg.appendChild(svgEl("line", {
        x1: x, x2: x + barW, y1: ey, y2: ey, stroke: "#f5c451", "stroke-width": 2,
      }));
    }
    const label = svgEl("text", {
      x: x + barW / 2,
      y: h - 12,
      fill: "#8ea0b8",
      "font-size": 10,
      "text-anchor": "middle",
    });
    label.textContent = bucket.label;
    svg.appendChild(label);
    const nLabel = svgEl("text", {
      x: x + barW / 2,
      y: Math.max(pad.t + 12, y - 4),
      fill: "#8ea0b8",
      "font-size": 10,
      "text-anchor": "middle",
    });
    nLabel.textContent = `n=${bucket.n}`;
    svg.appendChild(nLabel);
  });
}

function drawScatter(svgId, points) {
  const svg = $(svgId);
  clearSvg(svg);
  const w = 800;
  const h = 280;
  const pad = { l: 44, r: 16, t: 16, b: 36 };
  if (!points.length) {
    svg.appendChild(svgEl("text", { x: 20, y: 28, fill: "#8ea0b8", "font-size": 12 }));
    svg.lastChild.textContent = "No points with both BTC−PTB and UP mid.";
    return;
  }
  const xs = points.map((p) => Number(p.x));
  const ys = points.map((p) => Number(p.y));
  const minX = Math.min(-1, ...xs);
  const maxX = Math.max(1, ...xs);
  const minY = 0;
  const maxY = 1;
  const spanX = maxX - minX || 1;
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;
  const xAt = (v) => pad.l + ((v - minX) / spanX) * innerW;
  const yAt = (v) => pad.t + innerH - ((v - minY) / (maxY - minY)) * innerH;
  svg.appendChild(svgEl("line", {
    x1: pad.l, x2: w - pad.r, y1: yAt(0.5), y2: yAt(0.5), stroke: "#243044",
  }));
  svg.appendChild(svgEl("line", {
    x1: xAt(0), x2: xAt(0), y1: pad.t, y2: pad.t + innerH, stroke: "#243044",
  }));
  for (const p of points) {
    svg.appendChild(svgEl("circle", {
      cx: xAt(Number(p.x)),
      cy: yAt(Number(p.y)),
      r: 3.2,
      fill: p.outcome === "up" ? "#3dd68c" : "#ff6b7a",
      opacity: 0.75,
    }));
  }
  const xLabel = svgEl("text", {
    x: w / 2, y: h - 8, fill: "#8ea0b8", "font-size": 11, "text-anchor": "middle",
  });
  xLabel.textContent = "BTC − PTB";
  svg.appendChild(xLabel);
  const yLabel = svgEl("text", {
    x: 12, y: 18, fill: "#8ea0b8", "font-size": 11,
  });
  yLabel.textContent = "UP mid";
  svg.appendChild(yLabel);
}

let tableRows = [];
let sortKey = "slug";
let sortDir = 1;

function cmp(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

function renderTable() {
  const body = $("rowsBody");
  body.replaceChildren();
  if (!tableRows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="11" class="muted">No complete windows at this sample time.</td>`;
    body.appendChild(tr);
    return;
  }
  const sorted = tableRows.slice().sort((a, b) => sortDir * cmp(a[sortKey], b[sortKey]));
  for (const row of sorted) {
    const tr = document.createElement("tr");
    const outcome = row.outcome || "—";
    const agree = row.odds_agree_spot == null ? "—" : row.odds_agree_spot ? "yes" : "no";
    tr.innerHTML = `
      <td><a class="lab-window-link" href="/replay?window_id=${row.window_id}" title="${row.slug}">${row.slug}</a></td>
      <td>${fmtNum(row.elapsed_s, 1)}</td>
      <td class="${outcome}">${outcome}</td>
      <td>${fmtNum(row.up_mid, 3)}</td>
      <td class="${(row.btc_minus_ptb || 0) >= 0 ? "up" : "down"}">${fmtNum(row.btc_minus_ptb, 2)}</td>
      <td>${fmtNum(row.twap_minus_ptb, 2)}</td>
      <td>${fmtNum(row.volume_base, 2)}</td>
      <td class="${row.spot_side || ""}">${row.spot_side || "—"}</td>
      <td>${agree}</td>
      <td>${row.venues_up ?? "—"}</td>
      <td>${row.venues_down ?? "—"}</td>
    `;
    body.appendChild(tr);
  }
  document.querySelectorAll("#anTable th[data-sort]").forEach((th) => {
    th.classList.toggle("sort-asc", th.dataset.sort === sortKey && sortDir === 1);
    th.classList.toggle("sort-desc", th.dataset.sort === sortKey && sortDir === -1);
  });
}

function render(data) {
  $("summary").hidden = false;
  const s = data.summary || {};
  $("kpiWindows").textContent = String(s.windows ?? 0);
  const skipped = s.skipped ? ` · ${s.skipped} skipped` : "";
  $("kpiWindowsMeta").textContent = `at ${data.at_s}s${skipped}`;
  $("kpiUpRate").textContent = fmtPct(s.up_rate);
  $("kpiUpMeta").textContent = `${s.up_count ?? 0} UP / ${s.down_count ?? 0} DOWN`;
  $("kpiDist").textContent = s.mean_abs_btc_ptb == null ? "—" : Number(s.mean_abs_btc_ptb).toFixed(2);
  $("kpiDistMeta").textContent = s.odds_spot_agree_rate == null
    ? "absolute distance"
    : `odds agree spot ${fmtPct(s.odds_spot_agree_rate)}`;
  const win = s.mean_up_mid_winners;
  const lose = s.mean_up_mid_losers;
  $("kpiMid").textContent = `${win == null ? "—" : Number(win).toFixed(2)} / ${lose == null ? "—" : Number(lose).toFixed(2)}`;
  const calExpected = (data.calibration || []).map((_, i) => 0.1 + i * 0.2);
  drawBars("calChart", data.calibration || [], { expected: calExpected });
  drawBars("distChart", data.buckets || []);
  drawScatter("scatterChart", data.scatter || []);
  tableRows = data.windows || [];
  renderTable();
}

async function load(event) {
  if (event) event.preventDefault();
  $("errorBox").hidden = true;
  $("runBtn").disabled = true;
  $("runBtn").textContent = "Sampling…";
  $("anStatus").textContent = "Reading stored feeds…";
  const params = new URLSearchParams({
    at_s: String(Math.floor(num("atS", 180))),
    workers: String(Math.max(0, Math.floor(num("workers", 0)))),
  });
  const slug = $("slug").value.trim();
  if (slug) params.set("slug", slug);
  try {
    const res = await fetch(`/api/analyse?${params.toString()}`);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      } catch {
        /* use statusText */
      }
      throw new Error(detail);
    }
    const data = await res.json();
    render(data);
    $("anStatus").textContent = `${data.summary?.windows ?? 0} windows`;
  } catch (err) {
    $("errorBox").hidden = false;
    $("errorBox").textContent = err.message || String(err);
    $("anStatus").textContent = "";
  } finally {
    $("runBtn").disabled = false;
    $("runBtn").textContent = "Load";
  }
}

$("anForm").addEventListener("submit", load);
$("anTable").querySelector("thead").addEventListener("click", (event) => {
  const th = event.target.closest("th[data-sort]");
  if (!th) return;
  const key = th.dataset.sort;
  if (sortKey === key) sortDir *= -1;
  else {
    sortKey = key;
    sortDir = 1;
  }
  renderTable();
});
load();
