const STORAGE_KEY = "pm-centionaire.analyzer.v2.sections";

const DEFAULT = {
  window: true,
  highlights: false,
  equity: false,
  daily: false,
  breakdown: false,
  filters: false,
  trades: false,
};

export function loadAccordionState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT };
    return { ...DEFAULT, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT };
  }
}

function saveAccordionState(state) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function setAccordionOpen(el, open, { save = true } = {}) {
  el.classList.toggle("is-open", open);
  const btn = el.querySelector(".accordion-trigger");
  btn?.setAttribute("aria-expanded", open ? "true" : "false");
  if (!save) return;
  const id = el.dataset.section;
  if (!id) return;
  const next = loadAccordionState();
  next[id] = open;
  saveAccordionState(next);
}

/** Call once on startup — restores saved open/closed and binds click handlers. */
export function initAccordions() {
  const state = loadAccordionState();

  for (const el of document.querySelectorAll(".accordion[data-section]")) {
    const id = el.dataset.section;
    if (!id) continue;

    el.classList.add("no-transition");
    setAccordionOpen(el, !!state[id], { save: false });

    el.querySelector(".accordion-trigger")?.addEventListener("click", () => {
      setAccordionOpen(el, !el.classList.contains("is-open"));
    });
  }

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      for (const el of document.querySelectorAll(".accordion.no-transition")) {
        el.classList.remove("no-transition");
      }
    });
  });
}

/** Re-apply saved state after render without persisting or animating from wrong state. */
export function syncAccordionState() {
  const state = loadAccordionState();
  for (const el of document.querySelectorAll(".accordion[data-section]")) {
    if (el.hidden) continue;
    const id = el.dataset.section;
    if (!id) continue;
    const want = !!state[id];
    const have = el.classList.contains("is-open");
    if (want === have) continue;
    setAccordionOpen(el, want, { save: false });
  }
}
