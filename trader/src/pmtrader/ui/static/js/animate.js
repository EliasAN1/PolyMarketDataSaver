import { fmtUsd } from "./stats.js";

const running = new WeakMap();

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function easeOutCubic(t) {
  return 1 - (1 - t) ** 3;
}

export function currentUsd(el) {
  return running.get(el)?.value ?? null;
}

export function cancelUsdTween(el) {
  const job = running.get(el);
  if (job?.raf) cancelAnimationFrame(job.raf);
  running.delete(el);
}

/**
 * Count an element's USD text from `from` to `to`.
 * Mid-flight calls retarget from the live interpolated value.
 */
export function tweenUsd(
  el,
  to,
  { from = null, animate = false, duration = 1050, onFrame, format = fmtUsd } = {},
) {
  if (!el) return;
  const startVal = currentUsd(el) ?? from;
  cancelUsdTween(el);

  const snap = () => {
    paintUsd(el, to, onFrame, format);
  };

  if (
    !animate ||
    startVal == null ||
    !Number.isFinite(startVal) ||
    !Number.isFinite(to) ||
    prefersReducedMotion() ||
    Math.abs(to - startVal) < 0.005
  ) {
    snap();
    return;
  }

  const started = performance.now();
  const job = { value: startVal, raf: 0 };
  running.set(el, job);

  const tick = (now) => {
    const t = Math.min(1, (now - started) / duration);
    const v = startVal + (to - startVal) * easeOutCubic(t);
    job.value = v;
    paintUsd(el, v, onFrame, format);
    if (t < 1) {
      job.raf = requestAnimationFrame(tick);
    } else {
      paintUsd(el, to, onFrame, format);
      running.delete(el);
    }
  };
  job.raf = requestAnimationFrame(tick);
}

function paintUsd(el, n, onFrame, format = fmtUsd) {
  el.textContent = format(n);
  onFrame?.(n);
}

export function fmtUsdDelta(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n >= 0 ? "+" : "−"}$${Math.abs(n).toFixed(2)}`;
}

/** Pop a signed delta next to the running total, then float it away. */
export function flashDelta(el, delta) {
  if (!el || !Number.isFinite(delta) || Math.abs(delta) < 0.005 || prefersReducedMotion()) {
    return;
  }
  el.hidden = false;
  el.textContent = fmtUsdDelta(delta);
  el.classList.remove("is-on", "up", "down");
  el.classList.add(delta >= 0 ? "up" : "down");
  void el.offsetWidth;
  el.classList.add("is-on");
}

export function pulseEl(el, className = "is-ticking") {
  if (!el || prefersReducedMotion()) return;
  el.classList.remove(className);
  void el.offsetWidth;
  el.classList.add(className);
}
