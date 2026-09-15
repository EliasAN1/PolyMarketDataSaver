const TABS = ["live", "stats", "days", "trades", "wallet"];
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
    const nextHash = `#${tab}`;
    if (location.hash !== nextHash) {
      history.replaceState(null, "", nextHash);
    }
  }

  if (!silent) onChange?.(tab);
}

function normalize(id) {
  const raw = String(id || "").toLowerCase().trim();
  return TABS.includes(raw) ? raw : null;
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
  return "live";
}
