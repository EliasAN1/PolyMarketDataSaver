import { parseJsonl } from "./parse.js";

export async function listLogSources() {
  try {
    const res = await fetch("/api/logs", { cache: "no-store" });
    if (!res.ok) return { files: [] };
    return await res.json();
  } catch {
    return { files: [] };
  }
}

export async function loadFromServer(source) {
  const extra = source && source !== "live" ? `&source=${encodeURIComponent(source)}` : "";
  const res = await fetch(`/api/logs/trades.jsonl?_=${Date.now()}${extra}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const text = await res.text();
  return parseJsonl(text);
}

export async function loadBalanceFromServer() {
  try {
    const res = await fetch("/api/logs/balance.json");
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}
