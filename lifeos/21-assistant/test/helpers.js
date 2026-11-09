// Fake KV, queue and network. Nothing here reaches a real service.
export function fakeKV() {
  const data = new Map();
  return {
    data,
    async get(k, type) { const v = data.get(k); return v === undefined ? null : type === "json" ? JSON.parse(v) : v; },
    async put(k, v) { data.set(k, v); },
    async delete(k) { data.delete(k); },
  };
}

export function fakeEnv(extra = {}) {
  const queued = [];
  return {
    KV: fakeKV(), MESSAGES: { queued, send: async (m) => queued.push(m) },
    TELEGRAM_TOKEN: "TEST", OWNER_CHAT_ID: "42", WEBHOOK_SECRET: "s3cret", MODEL: "claude-opus-5-5",
    MONTHLY_LIMIT_USD: "10", ANTHROPIC_API_KEY: "test", ...extra,
  };
}

// routes: [[substring, (url, init) => jsonOrResponse]]. Records Telegram messages in `sent`.
export function fakeNet(routes = []) {
  const sent = [];
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input);
    calls.push(url);
    if (url.includes("/sendMessage")) { sent.push(JSON.parse(init.body)); return Response.json({ result: {} }); }
    if (url.includes("/sendChatAction")) return Response.json({ result: true });
    for (const [part, fn] of routes) {
      if (url.includes(part)) {
        const out = await fn(url, init);
        return out instanceof Response ? out : Response.json(out);
      }
    }
    throw new Error(`unexpected request: ${url}`);
  };
  return { sent, calls, restore: () => { globalThis.fetch = original; } };
}

// A stand-in for the Anthropic client: returns the scripted responses and records each request.
export function fakeClaude(responses) {
  const requests = [];
  return {
    requests,
    beta: { messages: { create: async (params) => {
      requests.push(JSON.parse(JSON.stringify(params)));
      return responses.shift();
    } } },
  };
}

export const reply = (content, stop_reason = "end_turn", usage = { input_tokens: 1000, output_tokens: 100 }) => ({
  model: "claude-opus-5-5", content, stop_reason, usage,
});
export const TEXT = (text, extra = {}) => ({ type: "text", text, ...extra });
