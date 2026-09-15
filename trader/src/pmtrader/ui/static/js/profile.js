const MAX_UINT = "115792089237316195423570";

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function fromAtomic(raw) {
  const n = num(raw);
  if (n == null) return null;
  return n >= 1000 ? n / 1e6 : n;
}

function usd(value, digits = 2) {
  const n = num(value);
  if (n == null) return "—";
  const sign = n < 0 ? "−" : "";
  return `${sign}$${Math.abs(n).toFixed(digits)}`;
}

function dec(value, digits = 2) {
  const n = num(value);
  if (n == null) return "—";
  return n.toFixed(digits);
}

function shortAddr(addr) {
  const a = String(addr || "");
  if (a.length < 12) return a || "—";
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

function when(value) {
  if (value == null || value === "") return "—";
  const n = num(value);
  if (n != null && n > 1e9) {
    const ms = n < 1e12 ? n * 1000 : n;
    return new Date(ms).toLocaleString();
  }
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

function tone(n) {
  if (n == null) return "";
  if (n > 0.005) return " up";
  if (n < -0.005) return " down";
  return "";
}

function listOf(value) {
  if (Array.isArray(value)) return value;
  if (value && Array.isArray(value.data)) return value.data;
  return [];
}

function allowanceLabel(map) {
  const entries = Object.entries(map || {});
  if (!entries.length) return "—";
  const unlimited = entries.every(([, v]) => String(v).startsWith(MAX_UINT));
  return unlimited ? `Unlimited · ${entries.length} contracts` : `${entries.length} set`;
}

function table(headers, rows) {
  if (!rows.length) return `<p class="profile-empty">None</p>`;
  const head = headers.map((h) => `<th>${esc(h)}</th>`).join("");
  const body = rows
    .map((cells) => `<tr>${cells.map((c) => `<td class="${c.cls || ""}">${c.html ?? esc(c.text ?? "—")}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="table-container"><table class="profile-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function cell(text, cls = "") {
  return { text, cls };
}

function renderProfile(data) {
  const clob = data.clob || {};
  const api = data.data || {};
  const bal = clob.balance_collateral || {};
  const cash = fromAtomic(bal.balance);
  const portfolio = Array.isArray(api.value) ? api.value[0] : api.value;
  const portVal = num(portfolio?.value ?? portfolio?.usd);
  const positions = listOf(api.positions);
  const active = positions.filter((p) => (num(p.curPrice) ?? 0) > 0.01);
  const leftover = positions.filter((p) => (num(p.curPrice) ?? 0) <= 0.01 && (num(p.size) ?? 0) > 0);
  const trades = listOf(api.trades);
  const activity = listOf(api.activity);
  const orders = listOf(clob.open_orders);
  const notes = listOf(clob.notifications);
  const closedOnly = clob.closed_only_mode?.closed_only === true;
  const errors = data.errors?.length
    ? `<p class="profile-error">${data.errors.map(esc).join(" · ")}</p>`
    : "";

  return `
    ${errors}
    <div class="profile-metrics-row">
      <div class="profile-metric-box">
        <span class="metric-label">pUSD Collateral</span>
        <strong class="metric-val mono">${esc(usd(cash, 2))}</strong>
      </div>
      <div class="profile-metric-box">
        <span class="metric-label">Portfolio Value</span>
        <strong class="metric-val mono">${esc(usd(portVal, 2))}</strong>
      </div>
      <div class="profile-metric-box">
        <span class="metric-label">Active Positions</span>
        <strong class="metric-val mono">${active.length}</strong>
      </div>
      <div class="profile-metric-box">
        <span class="metric-label">Settled / Leftover</span>
        <strong class="metric-val mono">${leftover.length}</strong>
      </div>
    </div>

    <section class="profile-card">
      <h3>Close Positions</h3>
      <p class="profile-hint">Redeems settled winners through the relayer and FAK-sells anything still open, including the current 5m window.</p>
      <div class="profile-close-row">
        <button type="button" class="profile-close-btn" data-close-scan>Scan queue</button>
        <button type="button" class="profile-close-btn" data-close-run>Redeem &amp; sell all</button>
      </div>
      <p class="profile-close-status" data-close-status></p>
    </section>

    <section class="profile-card">
      <h3>Account & Authorization</h3>
      <dl class="profile-kv">
        <dt>Funder</dt><dd title="${esc(data.wallet)}">${esc(shortAddr(data.wallet))}</dd>
        <dt>Signer</dt><dd title="${esc(data.signer)}">${esc(shortAddr(data.signer))}</dd>
        <dt>Type</dt><dd>${esc(data.signature_type === "3" || data.signature_type === 3 ? "Deposit wallet" : `Sig ${data.signature_type ?? "—"}`)}</dd>
        <dt>CLOB Mode</dt><dd>${data.dry_run ? "Dry Run / Simulation" : "Live CLOB Trading"}</dd>
        <dt>Allowance</dt><dd>${esc(allowanceLabel(bal.allowances))}</dd>
        <dt>Closed-only</dt><dd>${closedOnly ? "Yes" : "No"}</dd>
        <dt>CLOB Time</dt><dd>${esc(when(clob.server_time))}</dd>
      </dl>
    </section>

    <section class="profile-card">
      <h3>Open Orders (CLOB)</h3>
      ${table(
        ["Side", "Price", "Size", "Status", "Market"],
        orders.slice(0, 20).map((o) => [
          cell(o.side, (o.side || "").toLowerCase() === "buy" ? "up" : "down"),
          cell(dec(o.price, 2)),
          cell(dec(o.original_size ?? o.size, 2)),
          cell(o.status || "—"),
          cell(o.market || o.title || shortAddr(o.asset_id)),
        ]),
      )}
    </section>

    <section class="profile-card">
      <h3>Active Positions</h3>
      ${table(
        ["Market", "Side", "Shares", "Avg", "Now", "PnL"],
        active.slice(0, 30).map((p) => {
          const pnl = num(p.cashPnl);
          return [
            cell(p.title || p.slug || "—"),
            cell(p.outcome, (p.outcome || "").toLowerCase() === "up" ? "up" : "down"),
            cell(dec(p.size, 2)),
            cell(dec(p.avgPrice, 2)),
            cell(dec(p.curPrice, 2)),
            { text: usd(pnl), cls: tone(pnl) },
          ];
        }),
      )}
    </section>

    <section class="profile-card">
      <h3>Unredeemed Leftovers</h3>
      ${leftover.length ? `<p class="profile-hint">Settled tokens still on the wallet. Use Close Positions above, or redeem on Polymarket.</p>` : ""}
      ${table(
        ["Market", "Side", "Shares", "Cost"],
        leftover.slice(0, 20).map((p) => [
          cell(p.title || "—"),
          cell(p.outcome),
          cell(dec(p.size, 2)),
          cell(usd((num(p.avgPrice) ?? 0) * (num(p.size) ?? 0))),
        ]),
      )}
    </section>

    <section class="profile-card">
      <h3>Recent Fills</h3>
      ${table(
        ["Side", "Price", "Shares", "Market"],
        trades.slice(0, 25).map((t) => [
          cell(t.side, (t.side || "").toLowerCase() === "buy" ? "up" : "down"),
          cell(dec(t.price, 2)),
          cell(dec(t.size, 2)),
          cell(t.title || t.slug || "—"),
        ]),
      )}
    </section>

    <section class="profile-card">
      <h3>Wallet Activity</h3>
      ${table(
        ["Type", "Side", "Shares", "USDC", "Market"],
        activity.slice(0, 25).map((a) => [
          cell((a.type || "").replaceAll("_", " ")),
          cell(a.side || "—", (a.side || "").toLowerCase() === "buy" ? "up" : "down"),
          cell(dec(a.size, 2)),
          { text: usd(a.usdcSize), cls: tone(num(a.usdcSize)) },
          cell(a.title || "—"),
        ]),
      )}
    </section>

    <section class="profile-card">
      <h3>Notifications</h3>
      ${table(
        ["When", "Side", "Size", "Price", "Market"],
        notes.slice(0, 15).map((n) => {
          const p = n.payload || {};
          return [
            cell(when(n.timestamp)),
            cell(p.side || p.outcome || "—", (p.side || "").toLowerCase() === "buy" ? "up" : "down"),
            cell(dec(p.matched_size, 2)),
            cell(dec(p.price, 2)),
            cell(p.question || p.eventSlug || "—"),
          ];
        }),
      )}
    </section>
  `;
}

function closeSummary(data) {
  if (data.error) return data.error;
  const jobs = Array.isArray(data.jobs) ? data.jobs : [];
  const preview = jobs.slice(0, 12).map((j) => j.summary).filter(Boolean);
  const extra = jobs.length > 12 ? `\n… and ${jobs.length - 12} more` : "";
  const queued = data.scan_only || data.dry_run;
  const counts = queued
    ? `queue: ${data.redeemed ?? 0} redeem · ${data.sold ?? 0} sell · ${data.skipped ?? 0} skip`
    : `redeemed ${data.redeemed ?? 0} · sold ${data.sold ?? 0} · skipped ${data.skipped ?? 0} · failed ${data.failed ?? 0}`;
  const head = data.scan_only ? "Scan" : data.dry_run ? "Dry-run" : "Closed";
  return `${head}: ${jobs.length} jobs\n${counts}${preview.length ? `\n${preview.join("\n")}${extra}` : ""}`;
}

export function initProfile() {
  const refreshBtn = document.getElementById("profile-refresh");
  refreshBtn?.addEventListener("click", () => loadProfile({ force: true }));
}

export async function loadProfile({ force = false } = {}) {
  const body = document.getElementById("profile-body");
  const status = document.getElementById("profile-status");
  if (!body) return;
  if (body.dataset.loaded === "1" && !force) return;

  if (status) status.textContent = "Loading account details…";
  try {
    const res = await fetch("/api/profile", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    body.innerHTML = renderProfile(data);
    body.dataset.loaded = "1";
    bindClose(body);
    if (status) status.textContent = data.wallet ? `Connected: ${shortAddr(data.wallet)}` : "No wallet configured";
  } catch (err) {
    if (status) status.textContent = "Could not load profile.";
    body.innerHTML = `<p class="profile-error">${esc(err)}</p>`;
    body.dataset.loaded = "";
  }
}

function bindClose(body) {
  async function postClose({ scanOnly, confirm }) {
    const note = body.querySelector("[data-close-status]");
    const buttons = body.querySelectorAll("[data-close-scan], [data-close-run]");
    buttons.forEach((btn) => { btn.disabled = true; });
    if (note) note.textContent = scanOnly ? "Scanning wallet…" : "Closing positions…";
    try {
      const res = await fetch("/api/close-positions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scan_only: scanOnly, confirm, dry_run: false }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      if (note) note.textContent = closeSummary(data);
    } catch (err) {
      if (note) note.textContent = String(err.message || err);
    } finally {
      buttons.forEach((btn) => { btn.disabled = false; });
    }
  }

  body.querySelector("[data-close-scan]")?.addEventListener("click", () => {
    postClose({ scanOnly: true, confirm: false });
  });
  body.querySelector("[data-close-run]")?.addEventListener("click", () => {
    const ok = window.confirm(
      "Redeem settled positions and FAK-sell everything still open, including the current 5m window?"
    );
    if (!ok) return;
    postClose({ scanOnly: false, confirm: true });
  });
}
