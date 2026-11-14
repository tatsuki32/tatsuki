// One-line summaries and the day's brief, in one Claude Haiku call per user per day.
// Only the feed's own title and description go in; the model is told not to add anything they don't say.
import { AI_MODEL } from "./plans.js";

const API = "https://api.anthropic.com/v1/messages";
const VERSION = "2023-06-01";
export const MAX_ITEMS = 60;

const SYSTEM = `You write a short daily news digest in plain American English.
You get numbered articles: the site, the title and the feed's own short description.
Rules:
- Use only what the title and description say. Never add facts, numbers, names or causes they don't contain.
- "lines": for each number, one sentence of at most 25 words saying what the article is about. If the description adds nothing to the title, return "" for that number.
- "brief": 3 to 5 short sentences on the day as a whole: the main threads across the articles. No intro, no sign-off, no hype.
- Translate titles and descriptions that are not in English.
Answer with JSON only: {"lines": {"1": "...", "2": "..."}, "brief": "..."}`;

export function prompt(items) {
  return items
    .map((it, i) => `${i + 1}. [${it.site || ""}] ${it.title}${it.descr ? `\n   ${it.descr.slice(0, 400)}` : ""}`)
    .join("\n");
}

export function readAnswer(textOut, n) {
  const m = String(textOut || "").match(/\{[\s\S]*\}/);
  if (!m) throw new Error("the summary answer was not JSON");
  const v = JSON.parse(m[0]);
  const lines = [];
  for (let i = 1; i <= n; i++) {
    const s = String((v.lines || {})[i] ?? (v.lines || {})[String(i)] ?? "").replace(/\s+/g, " ").trim();
    lines.push(s.length > 220 ? s.slice(0, 219) + "…" : s);
  }
  const brief = String(v.brief || "").trim().slice(0, 900);
  return { lines, brief };
}

// → { lines: [string], brief, tin, tout }
export async function summarize(env, items, fetcher = fetch) {
  if (!env.ANTHROPIC_KEY) throw new Error("no ANTHROPIC_KEY secret");
  const list = items.slice(0, MAX_ITEMS);
  const r = await fetcher(API, {
    method: "POST",
    headers: { "x-api-key": env.ANTHROPIC_KEY, "anthropic-version": VERSION, "content-type": "application/json" },
    body: JSON.stringify({ model: AI_MODEL, max_tokens: 4000, system: SYSTEM, messages: [{ role: "user", content: prompt(list) }] }),
  });
  const v = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`Claude API ${r.status}: ${(v.error && v.error.message) || ""}`.slice(0, 200));
  const out = (v.content || []).filter((c) => c.type === "text").map((c) => c.text).join("");
  const { lines, brief } = readAnswer(out, list.length);
  return { lines, brief, tin: (v.usage && v.usage.input_tokens) || 0, tout: (v.usage && v.usage.output_tokens) || 0 };
}
