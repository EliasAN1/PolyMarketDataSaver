import { formatDayLabel } from "./parse.js";

export const DEFAULT_FILTERS = {
  side: "all",
  inverted: "all",
  outcome: "all",
  secMin: 0,
  secMax: 300,
  onlyResolved: false,
  hideFiltered: false,
  dayKey: null,
};

let selectedDayKey = null;

export function selectedDay() {
  return selectedDayKey;
}

/** Click the same day again to clear. */
export function toggleSelectedDay(dayKey) {
  selectedDayKey = selectedDayKey === dayKey ? null : dayKey || null;
  return selectedDayKey;
}

export function clearSelectedDay() {
  selectedDayKey = null;
}

export function passesFilters(t, f) {
  if (f.dayKey && t.dayKey !== f.dayKey) return false;
  if (f.onlyResolved && !t.resolved) return false;

  if (f.side !== "all" && (t.side ?? "") !== f.side) return false;

  if (f.inverted !== "all") {
    if (f.inverted === "inverted" && !t.inverted) return false;
    if (f.inverted === "original" && (t.inverted || t.flipped)) return false;
    if (f.inverted === "flipped" && !t.flipped) return false;
  }

  if (f.outcome !== "all") {
    if (!t.resolved) {
      if (f.outcome !== "open") return false;
    } else {
      const label = t.won === true ? "won" : t.won === false ? "lost" : "open";
      if (label !== f.outcome) return false;
    }
  }

  if (t.secLeft != null && (t.secLeft < f.secMin || t.secLeft > f.secMax)) return false;

  return true;
}

export function readFilters(root = document) {
  const seg = (name) => {
    const active = root.querySelector(`.seg-btn[data-filter="${name}"].is-active`);
    return active?.dataset.value ?? "all";
  };
  const num = (id, fb) => {
    const v = parseFloat(root.getElementById(id)?.value);
    return Number.isFinite(v) ? v : fb;
  };
  return {
    side: seg("side"),
    inverted: seg("inverted"),
    outcome: seg("outcome"),
    secMin: num("filter-sec-min", DEFAULT_FILTERS.secMin),
    secMax: num("filter-sec-max", DEFAULT_FILTERS.secMax),
    onlyResolved: !!root.getElementById("filter-only-resolved")?.checked,
    hideFiltered: !!root.getElementById("filter-hide-filtered")?.checked,
    dayKey: selectedDayKey,
  };
}

function setSegValue(root, name, value) {
  for (const btn of root.querySelectorAll(`.seg-btn[data-filter="${name}"]`)) {
    btn.classList.toggle("is-active", btn.dataset.value === value);
  }
}

export function resetFilterInputs(root = document) {
  clearSelectedDay();
  setSegValue(root, "side", DEFAULT_FILTERS.side);
  setSegValue(root, "inverted", DEFAULT_FILTERS.inverted);
  setSegValue(root, "outcome", DEFAULT_FILTERS.outcome);
  root.getElementById("filter-sec-min").value = String(DEFAULT_FILTERS.secMin);
  root.getElementById("filter-sec-max").value = String(DEFAULT_FILTERS.secMax);
  root.getElementById("filter-only-resolved").checked = DEFAULT_FILTERS.onlyResolved;
  root.getElementById("filter-hide-filtered").checked = DEFAULT_FILTERS.hideFiltered;
}

/** Wire pill toggles; call once at startup. */
export function bindSegFilters(root, onChange) {
  for (const btn of root.querySelectorAll(".seg-btn[data-filter]")) {
    btn.addEventListener("click", () => {
      const name = btn.dataset.filter;
      for (const sib of root.querySelectorAll(`.seg-btn[data-filter="${name}"]`)) {
        sib.classList.remove("is-active");
      }
      btn.classList.add("is-active");
      onChange();
    });
  }
}

export function activeFilterLabels(f) {
  const chips = [];
  if (f.dayKey) chips.push(formatDayLabel(f.dayKey));
  if (f.side !== "all") chips.push(`Side: ${f.side.toUpperCase()}`);
  if (f.inverted !== "all") chips.push(`Strategy: ${f.inverted}`);
  if (f.outcome !== "all") chips.push(`Outcome: ${f.outcome}`);
  if (f.onlyResolved) chips.push("Resolved only");
  if (f.secMin > 0) chips.push(`Sec ≥ ${f.secMin}`);
  if (f.secMax < 300) chips.push(`Sec ≤ ${f.secMax}`);
  if (f.hideFiltered) chips.push("Hiding filtered");
  return chips;
}

export function filtersActive(f) {
  return activeFilterLabels(f).length > 0;
}
