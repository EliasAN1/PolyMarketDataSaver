function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function toneClass(ok, enabled) {
  if (!enabled) return "off";
  return ok ? "up" : "down";
}

export function renderLive(data, root) {
  const inner = root.getElementById("window-inner");
  const status = root.getElementById("window-status");
  if (!inner) return;

  if (!data?.running) {
    inner.innerHTML = `<p class="profile-empty">Start the trader to see this window.</p>`;
    if (status) status.textContent = "offline";
    return;
  }

  const left = data.seconds_left != null ? `${data.seconds_left}s` : "—";
  const side = (data.side || "").toUpperCase();
  if (status) {
    status.textContent = data.traded ? "sent" : data.state || "—";
    status.className = `window-status ${data.traded ? "up" : data.state === "ready" ? "up" : "down"}`;
  }

  const checks = (data.checks || [])
    .map((c) => {
      const cls = toneClass(c.ok, c.enabled);
      const badge = !c.enabled ? "off" : c.ok ? "ok" : "no";
      return `<div class="live-check ${cls}${c.enabled ? "" : " is-off"}">
        <span class="live-dot">${badge}</span>
        <span class="live-label">${esc(c.label)}</span>
        <span class="live-value">${esc(c.value)}</span>
      </div>`;
    })
    .join("");

  inner.innerHTML = `
    <div class="live-head">
      <strong class="live-slug">${esc(data.slug || "—")}</strong>
      <span class="live-meta">${esc(left)} left${side ? ` · ${esc(side)}` : ""}</span>
    </div>
    <div class="live-checks">${checks}</div>
  `;
}

export function startLivePoll(root = document, intervalMs = 1000) {
  async function tick() {
    try {
      const res = await fetch("/api/live", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      renderLive(await res.json(), root);
    } catch {
      renderLive({ running: false }, root);
    }
  }
  tick();
  setInterval(() => {
    if (!document.hidden) tick();
  }, intervalMs);
}
