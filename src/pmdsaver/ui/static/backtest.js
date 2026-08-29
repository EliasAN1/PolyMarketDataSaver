function $(id) {
  return document.getElementById(id);
}

function num(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) ? value : fallback;
}

function toggleParams() {
  const name = $("strategy").value;
  const isCombo = name === "combo";
  document.querySelectorAll(".combo-only").forEach((el) => {
    el.hidden = !isCombo;
  });
  if (!isCombo) {
    $("useOdds").checked = true;
    $("useLastMinutes").checked = true;
    $("useSpot").checked = false;
    $("useTwap").checked = false;
    $("useVolume").checked = false;
    $("useVenues").checked = false;
  }
}

function fmtPnl(value) {
  if (value == null) return "—";
  const n = Number(value);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(4)}`;
}

function fmtTime(ts) {
  if (ts == null) return "—";
  const d = new Date(Number(ts) * 1000);
  if (!Number.isFinite(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

let equityState = null;

function hideEquityHover() {
  const hair = $("equityHair");
  const dot = $("equityDot");
  const tip = $("equityTip");
  if (hair) hair.hidden = true;
  if (dot) dot.hidden = true;
  if (tip) tip.hidden = true;
}

function drawEquity(points) {
  const svg = $("equityChart");
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  hideEquityHover();
  const ns = "http://www.w3.org/2000/svg";
  const w = 800;
  const h = 220;
  const pad = 16;
  if (!points.length) {
    equityState = null;
    const text = document.createElementNS(ns, "text");
    text.setAttribute("x", "20");
    text.setAttribute("y", "28");
    text.setAttribute("fill", "#8ea0b8");
    text.setAttribute("font-size", "12");
    text.textContent = "No filled trades.";
    svg.appendChild(text);
    return;
  }
  const xs = points.map((_, i) => i);
  const ys = points.map((p) => Number(p.equity));
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(0, ...ys);
  const spanY = maxY - minY || 1;
  const xAt = (i) => pad + (i / Math.max(xs.length - 1, 1)) * (w - pad * 2);
  const yAt = (v) => h - pad - ((v - minY) / spanY) * (h - pad * 2);
  const zero = document.createElementNS(ns, "line");
  zero.setAttribute("x1", String(pad));
  zero.setAttribute("x2", String(w - pad));
  zero.setAttribute("y1", String(yAt(0)));
  zero.setAttribute("y2", String(yAt(0)));
  zero.setAttribute("stroke", "#243044");
  svg.appendChild(zero);
  const d = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(Number(p.equity)).toFixed(1)}`)
    .join(" ");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#5b8cff");
  path.setAttribute("stroke-width", "2");
  svg.appendChild(path);
  equityState = { points, w, h, pad, minY, spanY };
}

function onEquityMove(event) {
  if (!equityState || !equityState.points.length) return;
  const svg = $("equityChart");
  const hair = $("equityHair");
  const dot = $("equityDot");
  const tip = $("equityTip");
  const rect = svg.getBoundingClientRect();
  const { points, w, h, pad, minY, spanY } = equityState;
  const n = points.length;
  const vx = ((event.clientX - rect.left) / rect.width) * w;
  const inner = (w - pad * 2) || 1;
  const frac = Math.max(0, Math.min(1, (vx - pad) / inner));
  const i = Math.round(frac * Math.max(n - 1, 0));
  const point = points[i];
  if (!point) return;
  const xAt = (idx) => pad + (idx / Math.max(n - 1, 1)) * inner;
  const yAt = (v) => h - pad - ((v - minY) / spanY) * (h - pad * 2);
  const equity = Number(point.equity);
  const leftPct = (xAt(i) / w) * 100;
  const topPct = (yAt(equity) / h) * 100;
  hair.hidden = false;
  hair.style.left = `${leftPct}%`;
  dot.hidden = false;
  dot.style.left = `${leftPct}%`;
  dot.style.top = `${topPct}%`;
  const cls = equity >= 0 ? "up" : "down";
  tip.hidden = false;
  tip.innerHTML = `<div class="tip-pnl ${cls}">${fmtPnl(equity)}</div><div class="tip-time">${fmtTime(point.t)}</div>`;
  const box = $("equityBox").getBoundingClientRect();
  const tipW = tip.offsetWidth;
  const tipH = tip.offsetHeight;
  let tipLeft = (leftPct / 100) * box.width - tipW / 2;
  tipLeft = Math.max(8, Math.min(box.width - tipW - 8, tipLeft));
  let tipTop = (topPct / 100) * box.height - tipH - 12;
  if (tipTop < 8) tipTop = (topPct / 100) * box.height + 12;
  tip.style.left = `${tipLeft}px`;
  tip.style.top = `${tipTop}px`;
}

function render(report) {
  $("summary").hidden = false;
  const pnlEl = $("netPnl");
  pnlEl.textContent = fmtPnl(report.net_pnl);
  pnlEl.className = `value ${report.net_pnl >= 0 ? "up" : "down"}`;
  $("pnlMeta").textContent = report.avg_pnl == null
    ? "no fills"
    : `avg ${fmtPnl(report.avg_pnl)} / trade · fees ${fmtPnl(report.fees_paid)} · stake $${Number(report.stake ?? 1).toFixed(2)}`;
  $("trades").textContent = String(report.trades ?? 0);
  $("tradeMeta").textContent = `${report.wins ?? 0} win / ${report.losses ?? 0} loss · ${report.no_trade ?? 0} no trade`;
  $("winRate").textContent = report.win_rate == null ? "—" : `${(report.win_rate * 100).toFixed(1)}%`;
  $("skipped").textContent = String(report.skipped ?? 0);
  const reasons = Object.entries(report.skip_counts || {})
    .map(([k, v]) => `${k} ${v}`)
    .join(" · ");
  $("skipMeta").textContent = reasons || "complete windows only";

  drawEquity(report.equity || []);

  const body = $("rowsBody");
  body.replaceChildren();
  const rows = report.rows || [];
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="13" class="muted">No closed windows in the database yet.</td>`;
    body.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    const side = row.side || "—";
    const fill = row.fill == null ? "—" : Number(row.fill).toFixed(3);
    const outcome = row.outcome || "—";
    const status = row.status === "skipped" ? `${row.status} (${row.skip_reason})` : row.status;
    tr.innerHTML = `
      <td>${row.slug}</td>
      <td class="${side}">${side}</td>
      <td>${fill}</td>
      <td>${row.shares == null ? "—" : Number(row.shares).toFixed(2)}</td>
      <td>${row.fee == null ? "—" : Number(row.fee).toFixed(4)}</td>
      <td class="${outcome}">${outcome}</td>
      <td class="${(row.pnl || 0) >= 0 ? "up" : "down"}">${fmtPnl(row.pnl)}</td>
      <td>${row.elapsed_s == null ? "—" : Number(row.elapsed_s).toFixed(1)}</td>
      <td>${row.up_mid == null ? "—" : Number(row.up_mid).toFixed(3)}</td>
      <td>${row.btc_minus_ptb == null ? "—" : Number(row.btc_minus_ptb).toFixed(2)}</td>
      <td>${row.twap_minus_ptb == null ? "—" : Number(row.twap_minus_ptb).toFixed(2)}</td>
      <td>${row.volume == null ? "—" : Number(row.volume).toFixed(2)}</td>
      <td class="muted">${status}</td>
    `;
    body.appendChild(tr);
  }
}

async function run(event) {
  event.preventDefault();
  $("errorBox").hidden = true;
  $("runBtn").disabled = true;
  $("runBtn").textContent = "0 backtested, … left";
  $("btStatus").textContent = "Starting…";
  const body = {
    strategy: $("strategy").value,
    fill: $("fill").value,
    stake: num("stake", 1),
    fee_rate: num("feeRate", 0.07),
    hit_odds: num("hitOdds", 0.25),
    last_minutes: num("lastMinutes", 3),
    use_last_minutes: $("useLastMinutes").checked,
    use_odds: $("useOdds").checked,
    use_spot: $("useSpot").checked,
    min_distance: num("minDistance", 10),
    use_twap: $("useTwap").checked,
    use_volume: $("useVolume").checked,
    min_volume: num("minVolume", 50),
    use_venues: $("useVenues").checked,
    min_venues: Math.max(1, Math.floor(num("minVenues", 2))),
    workers: Math.max(0, Math.floor(num("workers", 0))),
    slug: $("slug").value.trim() || null,
  };
  try {
    const res = await fetch("/api/backtest/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      } catch {
        /* use statusText */
      }
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let sawDone = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "error") {
          throw new Error(ev.detail || "Backtest failed");
        }
        if (ev.type === "start" || ev.type === "progress") {
          const doneN = ev.done ?? 0;
          const leftN = ev.left ?? 0;
          $("runBtn").textContent = `${doneN} backtested, ${leftN} left`;
          if (ev.slug) {
            const workers = ev.workers ? ` · ${ev.workers} workers` : "";
            $("btStatus").textContent = `${ev.slug} · ${ev.status || ""}${workers}`;
          } else {
            const workers = ev.workers ? ` · ${ev.workers} workers` : "";
            $("btStatus").textContent = `${ev.total ?? 0} windows${workers}`;
          }
        }
        if (ev.type === "done") {
          sawDone = true;
          render(ev.report);
          $("btStatus").textContent = `${ev.report.windows ?? 0} windows done`;
        }
      }
    }
    if (!sawDone) {
      throw new Error("Backtest stream ended before finishing");
    }
  } catch (err) {
    $("errorBox").hidden = false;
    $("errorBox").textContent = err.message || String(err);
    $("btStatus").textContent = "";
  } finally {
    $("runBtn").disabled = false;
    $("runBtn").textContent = "Run";
  }
}

$("strategy").addEventListener("change", toggleParams);
$("btForm").addEventListener("submit", run);
$("equityBox").addEventListener("mousemove", onEquityMove);
$("equityBox").addEventListener("mouseleave", hideEquityHover);
toggleParams();
