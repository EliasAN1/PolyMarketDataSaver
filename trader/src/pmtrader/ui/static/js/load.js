import { parseJsonl } from "./parse.js";

export async function loadFromServer() {
  const res = await fetch(`/api/logs/trades.jsonl?_=${Date.now()}`, { cache: "no-store" });
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
