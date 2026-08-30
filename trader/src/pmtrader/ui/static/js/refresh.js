const TRADES_INTERVAL_MS = 4_000;

let timer = null;
let refreshFn = null;

export function startAutoRefresh(fn) {
  stopAutoRefresh();
  refreshFn = fn;
  timer = setInterval(() => {
    if (document.hidden) return;
    refreshFn?.({ silent: true });
  }, TRADES_INTERVAL_MS);

  document.addEventListener("visibilitychange", onVisibility);
}

export function stopAutoRefresh() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  document.removeEventListener("visibilitychange", onVisibility);
  refreshFn = null;
}

function onVisibility() {
  if (!document.hidden) {
    refreshFn?.({ silent: true });
  }
}
