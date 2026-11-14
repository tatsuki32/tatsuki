// One-line summaries and the day's brief, in one Claude Haiku call per user per day.
// Only the feed's own title and description go in; the model is told not to add anything they don't say.
// Haiku 5.5 thinks by default (effort "medium"): effort "low" keeps the spend small, and a JSON schema
// (structured outputs) makes the answer always parse. The SDK sets the API version.
import Anthropic from "@anthropic-ai/sdk";
import { AI_MODEL } from "./plans.js";

export const MAX_ITEMS = 60;

const SYSTEM = `You write a short daily news digest in plain American English.
You get numbered articles: the site, the title and the feed's own short description.
Rules:
- Use only what the title and description say. Never add facts, numbers, names or causes they don't contain.
- "lines": one entry per article number. "line" is one sentence of at most 25 words saying what the article is about. If the description adds nothing to the title, use "".
- "brief": 3 to 5 short sentences on the day as a whole: the main threads across the articles. No intro, no sign-off, no hype.
- Translate titles and descriptions that are not in English.`;

export const SCHEMA = {
  type: "object",
  properties: {
    lines: {
      type: "array",
      items: { type: "object", properties: { n: { type: "integer" }, line: { type: "string" } }, required: ["n", "line"], additionalProperties: false },
    },
    brief: { type: "string" },
  },
  required: ["lines", "brief"],
  additionalProperties: false,
};

export function prompt(items) {
  return items
    .map((it, i) => `${i + 1}. [${it.site || ""}] ${it.title}${it.descr ? `\n   ${it.descr.slice(0, 400)}` : ""}`)
    .join("\n");
}

// the model's JSON → one line per article (in order) and the brief
export function readAnswer(json, n) {
  const v = typeof json === "string" ? JSON.parse(json) : json;
  const byN = new Map((v.lines || []).map((x) => [Number(x.n), x.line]));
  const lines = [];
  for (let i = 1; i <= n; i++) {
    const s = String(byN.get(i) ?? "").replace(/\s+/g, " ").trim();
    lines.push(s.length > 220 ? s.slice(0, 219) + "…" : s);
  }
  return { lines, brief: String(v.brief || "").trim().slice(0, 900) };
}

// → { lines: [string], brief, tin, tout }. client is for tests.
export async function summarize(env, items, client = null) {
  if (!client && !env.ANTHROPIC_KEY) throw new Error("no ANTHROPIC_KEY secret");
  const c = client || new Anthropic({ apiKey: env.ANTHROPIC_KEY, maxRetries: 2 });
  const list = items.slice(0, MAX_ITEMS);
  const r = await c.messages.create({
    model: AI_MODEL,
    max_tokens: 16000,
    system: SYSTEM,
    output_config: { effort: "low", format: { type: "json_schema", schema: SCHEMA } },
    messages: [{ role: "user", content: prompt(list) }],
  });
  const tin = (r.usage && r.usage.input_tokens) || 0, tout = (r.usage && r.usage.output_tokens) || 0;
  if (r.stop_reason !== "end_turn") {
    const e = new Error(`the summary stopped early (${r.stop_reason}${r.stop_details && r.stop_details.category ? `: ${r.stop_details.category}` : ""})`);
    Object.assign(e, { tin, tout });
    throw e;
  }
  const out = (r.content || []).filter((b) => b.type === "text").map((b) => b.text).join("");
  const { lines, brief } = readAnswer(out, list.length);
  return { lines, brief, tin, tout };
}
