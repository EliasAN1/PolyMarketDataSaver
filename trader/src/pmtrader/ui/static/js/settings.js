const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MARKETS = [
  { key: "tokyo_open", short: "Tokyo", local: "02:00–04:30" },
  { key: "london_open", short: "Lon open", local: "09:00–12:00" },
  { key: "wall_open", short: "NY open", local: "15:30–18:30" },
  { key: "asia", short: "Asia", local: "02:00–10:00" },
  { key: "london", short: "London", local: "09:00–18:30" },
  { key: "wall", short: "Wall St", local: "15:00–23:00" },
  { key: "overlap", short: "Overlap", local: "15:00–18:30" },
  { key: "off", short: "Off", local: "23:00–02:00" },
];

const draft = {
  hours: new Set(),
  weekdays: new Set(),
  sessions: new Set(),
  invert: new Set(),
};
let dirty = false;
let markets = MARKETS;

export function initSettings() {
  const form = document.getElementById("settings-form");
  if (!form) return;
  renderHours(document.getElementById("settings-hours"), "hour");
  renderDays();
  renderMarkets(markets);
  renderHours(document.getElementById("settings-invert"), "invert");
  form.addEventListener("click", onChip);
  form.addEventListener("input", (event) => {
    if (event.target.id === "settings-stake") dirty = true;
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveSettings();
  });
  loadSettings();
}

export async function loadSettings() {
  if (dirty) return;
  try {
    const res = await fetch("/api/live", { cache: "no-store" });
    if (!res.ok) return;
    applyPayload(await res.json());
  } catch {
    setStatus("Could not load settings.", "is-error");
  }
}

function applyPayload(data) {
  if (dirty) return;
  const cfg = data.config || data;
  const stake = document.getElementById("settings-stake");
  if (stake && cfg.stake_usd != null) stake.value = String(cfg.stake_usd);
  draft.hours = new Set((cfg.hours || []).map(Number));
  draft.weekdays = new Set(cfg.weekdays || []);
  draft.sessions = new Set(cfg.sessions || []);
  draft.invert = new Set((cfg.invert_hours || []).map(Number));
  const catalog = data.catalog || data.when?.catalog;
  if (Array.isArray(catalog) && catalog.length) {
    markets = catalog.map((row) => ({
      key: row.key,
      short: row.short || row.label || row.key,
      local: String(row.local || "").replace(" local", ""),
    }));
    renderMarkets(markets);
  }
  const cap = cfg.fak_limit;
  const help = document.getElementById("settings-invert-help");
  if (help && cap != null) {
    help.textContent = `UP buys DOWN, and DOWN buys UP. If the other ask is above ${Number(cap).toFixed(2)}, that window is skipped. Nothing selected means never invert.`;
  }
  syncPressed();
  warnIfInvertIsUntradable();
}

function renderHours(host, kind) {
  if (!host) return;
  const now = new Date().getHours();
  host.replaceChildren();
  for (let hour = 0; hour < 24; hour += 1) {
    const button = chip(kind, String(hour), String(hour).padStart(2, "0"));
    if (hour === now) button.classList.add("is-now");
    host.append(button);
  }
}

function renderDays() {
  const host = document.getElementById("settings-days");
  if (!host) return;
  host.replaceChildren(...DAYS.map((day) => chip("day", day, day)));
}

function renderMarkets(rows) {
  const host = document.getElementById("settings-markets");
  if (!host) return;
  host.replaceChildren(...rows.map((row) => chip("market", row.key, row.short, row.local)));
}

function chip(kind, value, label, detail) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "settings-chip";
  button.dataset.kind = kind;
  button.dataset.value = value;
  button.setAttribute("aria-pressed", "false");
  const name = document.createElement("strong");
  name.textContent = label;
  button.append(name);
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    button.append(small);
  }
  return button;
}

function onChip(event) {
  const button = event.target.closest?.(".settings-chip");
  if (!button) return;
  event.preventDefault();
  const value = button.dataset.value;
  const set = setFor(button.dataset.kind);
  if (!set) return;
  const key = button.dataset.kind === "day" || button.dataset.kind === "market" ? value : Number(value);
  if (set.has(key)) set.delete(key);
  else set.add(key);
  dirty = true;
  syncPressed();
  if (!warnIfInvertIsUntradable()) setStatus("");
}

function setFor(kind) {
  if (kind === "hour") return draft.hours;
  if (kind === "day") return draft.weekdays;
  if (kind === "market") return draft.sessions;
  if (kind === "invert") return draft.invert;
  return null;
}

function syncPressed() {
  for (const button of document.querySelectorAll("#settings-form .settings-chip")) {
    const key = button.dataset.kind === "day" || button.dataset.kind === "market"
      ? button.dataset.value
      : Number(button.dataset.value);
    const on = setFor(button.dataset.kind)?.has(key);
    const unused = button.dataset.kind === "invert" && on && draft.hours.size > 0 && !draft.hours.has(key);
    button.classList.toggle("is-on", Boolean(on));
    button.classList.toggle("is-unused", unused);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

function warnIfInvertIsUntradable() {
  if (draft.hours.size === 0) return false;
  const unused = [...draft.invert].filter((hour) => !draft.hours.has(hour)).sort((a, b) => a - b);
  if (!unused.length) return false;
  const labels = unused.map((hour) => String(hour).padStart(2, "0"));
  const list = labels.length === 1 ? labels[0] : `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}`;
  const verb = labels.length === 1 ? "is" : "are";
  setStatus(
    `${list} ${verb} set to buy against the signal, but the bot does not trade then. Add ${labels.length === 1 ? "that hour" : "those hours"} above, or turn the invert off.`,
    "is-error",
  );
  return true;
}

async function saveSettings() {
  const form = document.getElementById("settings-form");
  const stake = Number(document.getElementById("settings-stake")?.value);
  if (!Number.isFinite(stake) || stake <= 0) {
    setStatus("Enter a stake above 0.", "is-error");
    return;
  }
  if (warnIfInvertIsUntradable()) return;
  form?.classList.add("is-busy");
  setStatus("Saving…");
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        stake_usd: stake,
        hours: [...draft.hours].sort((a, b) => a - b),
        weekdays: DAYS.filter((day) => draft.weekdays.has(day)),
        sessions: markets.map((row) => row.key).filter((key) => draft.sessions.has(key)),
        invert_hours: [...draft.invert].sort((a, b) => a - b),
      }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(payload.error || "Could not save settings.", "is-error");
      return;
    }
    dirty = false;
    applyPayload(payload);
    setStatus("Saved. The running trader is using this now.", "is-ok");
  } catch {
    setStatus("Could not reach the trader.", "is-error");
  } finally {
    form?.classList.remove("is-busy");
  }
}

function setStatus(text, cls) {
  const status = document.getElementById("settings-status");
  if (!status) return;
  status.textContent = text;
  status.className = `settings-status${cls ? ` ${cls}` : ""}`;
}
