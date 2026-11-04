const TABS = ["live", "stats", "days", "trades", "wallet", "settings"];
const STORAGE_KEY = "pmtrader.ui.tab";

let active = "live";
let onChange = null;

export function currentTab() {
  return active;
}

export function initTabs(opts = {}) {
  onChange = opts.onChange ?? null;
  const initial = readInitial();
  selectTab(initial, { persist: true, silent: true });

  document.querySelectorAll(".tab-btn[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => selectTab(btn.dataset.tab));
  });

  window.addEventListener("hashchange", () => {
    const fromHash = normalize(location.hash.slice(1));
    if (fromHash && fromHash !== active) selectTab(fromHash, { persist: true, silent: false });
  });

  return active;
}

export function selectTab(id, { persist = true, silent = false } = {}) {
  const tab = normalize(id) || "live";
  const changed = active !== tab;
  active = tab;

  for (const pane of document.querySelectorAll(".tab-pane[data-tab]")) {
    const on = pane.dataset.tab === tab;
    pane.hidden = !on;
    pane.classList.toggle("is-active", on);
  }

  for (const btn of document.querySelectorAll(".tab-btn[data-tab]")) {
    const on = btn.dataset.tab === tab;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  }

  if (persist) {
    try {
      localStorage.setItem(STORAGE_KEY, tab);
    } catch {
      /* ignore */
    }
    const hashBody = location.hash.slice(1);
    const keepSub = hashBody === tab || hashBody.startsWith(`${tab}/`);
    if (!keepSub) {
      history.replaceState(null, "", `#${tab}`);
    }
  }

  if (changed) window.scrollTo({ top: 0, behavior: "instant" });
  if (!silent) onChange?.(tab);
}

function normalize(id) {
  const raw = String(id || "").toLowerCase().trim();
  const base = raw.split("/")[0];
  return TABS.includes(base) ? base : null;
}

function readInitial() {
  const fromHash = normalize(location.hash.slice(1));
  if (fromHash) return fromHash;
  try {
    const stored = normalize(localStorage.getItem(STORAGE_KEY));
    if (stored) return stored;
  } catch {
    /* ignore */
  }
  return "stats";
}
