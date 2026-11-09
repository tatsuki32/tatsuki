// What the bot keeps: the last location, a short conversation (gone after 30 minutes), and the month's usage.
import { hcmParts } from "./time.js";

const CONTEXT_TTL = 30 * 60;
const CONTEXT_TURNS = 6;

export const getLocation = (env) => env.KV.get("location", "json");
export const putLocation = (env, loc) => env.KV.put("location", JSON.stringify(loc));

export async function getHistory(env) {
  return (await env.KV.get("context", "json")) || [];
}

export async function addTurn(env, userText, assistantText) {
  const history = await getHistory(env);
  history.push({ role: "user", content: userText }, { role: "assistant", content: assistantText });
  await env.KV.put("context", JSON.stringify(history.slice(-CONTEXT_TURNS * 2)), { expirationTtl: CONTEXT_TTL });
}

export const forget = (env) => env.KV.delete("context");

const usageKey = (now) => `usage:${hcmParts(now).month}`;

export async function getUsage(env, now = new Date()) {
  return (await env.KV.get(usageKey(now), "json")) || { usd: 0, notified: false };
}

export async function putUsage(env, usage, now = new Date()) {
  await env.KV.put(usageKey(now), JSON.stringify(usage), { expirationTtl: 70 * 24 * 60 * 60 });
}

// Telegram sends an update again if it thinks delivery failed. True the first time an id is seen.
export async function firstTime(env, updateId) {
  const key = `update:${updateId}`;
  if (await env.KV.get(key)) return false;
  await env.KV.put(key, "1", { expirationTtl: 24 * 60 * 60 });
  return true;
}
