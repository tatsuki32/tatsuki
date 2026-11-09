import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import worker, { handleMessage } from "../src/index.js";
import { toolsFor, runTurn, sourcesOf } from "../src/assistant.js";
import { costOf } from "../src/usage.js";
import { rainLine, weatherText, skyWord } from "../src/weather.js";
import { decodeBase64Url, stripHtml, listEvents, readMail } from "../src/google.js";
import { splitText } from "../src/telegram.js";
import { hcmParts } from "../src/time.js";
import { fakeEnv, fakeNet, fakeClaude, reply, TEXT } from "./helpers.js";

const OWNER = { id: 42 };
const msg = (extra) => ({ chat: { id: 42 }, from: OWNER, ...extra });
const webhook = (body, secret = "s3cret") =>
  new Request("https://bot.example/", {
    method: "POST",
    headers: { "x-telegram-bot-api-secret-token": secret },
    body: JSON.stringify(body),
  });
const METEO = {
  daily: { weather_code: [3], temperature_2m_min: [24.4], temperature_2m_max: [33.2] },
  hourly: { time: ["2026-11-06T14:00", "2026-11-06T15:00"], precipitation_probability: [80, 20] },
};

let net;
beforeEach(() => { net = fakeNet([
  ["api.open-meteo.com", () => METEO],
  ["nominatim.openstreetmap.org", () => ({ address: { city: "ホーチミン市" } })],
]); });
afterEach(() => net.restore());

test("the webhook refuses a wrong secret", async () => {
  const env = fakeEnv();
  const res = await worker.fetch(webhook({ update_id: 1, message: msg({ text: "x" }) }, "wrong"), env);
  assert.equal(res.status, 403);
  assert.equal(env.MESSAGES.queued.length, 0);
});

test("a message from anyone else is dropped with no reply", async () => {
  const env = fakeEnv();
  const res = await worker.fetch(webhook({ update_id: 1, message: { chat: { id: 7 }, from: { id: 7 }, text: "x" } }), env);
  assert.equal(res.status, 200);
  assert.equal(env.MESSAGES.queued.length, 0);
  assert.equal(net.sent.length, 0);
});

test("a group with the owner in it is not the owner's chat", async () => {
  const env = fakeEnv();
  await worker.fetch(webhook({ update_id: 1, message: { chat: { id: -5 }, from: OWNER, text: "x" } }), env);
  assert.equal(env.MESSAGES.queued.length, 0);
});

test("the same update is queued only once", async () => {
  const env = fakeEnv();
  const body = { update_id: 9, message: msg({ text: "x" }) };
  await worker.fetch(webhook(body), env);
  await worker.fetch(webhook(body), env);
  assert.equal(env.MESSAGES.queued.length, 1);
});

test("an edited message is ignored, so live location does not move the place", async () => {
  const env = fakeEnv();
  await worker.fetch(webhook({ update_id: 2, edited_message: msg({ location: { latitude: 1, longitude: 2 } }) }), env);
  assert.equal(env.MESSAGES.queued.length, 0);
});

test("a location is stored rounded, with no history, and the weather comes back", async () => {
  const env = fakeEnv();
  await handleMessage(env, msg({ location: { latitude: 10.77689, longitude: 106.70081 } }));
  assert.deepEqual(JSON.parse(env.KV.data.get("location")), { lat: 10.78, lon: 106.7, place: "ホーチミン市" });
  assert.equal([...env.KV.data.keys()].length, 1);
  assert.match(net.sent[0].text, /ホーチミン市/);
  assert.match(net.sent[1].text, /24〜33℃ くもり/);
});

test("the morning message uses the last place and needs no Claude", async () => {
  const env = fakeEnv({ ANTHROPIC_API_KEY: undefined });
  env.KV.data.set("location", JSON.stringify({ lat: 1, lon: 2, place: "ダナン" }));
  env.KV.data.set(`usage:${hcmParts().month}`, JSON.stringify({ usd: 99, notified: true }));
  await worker.scheduled({}, env);
  assert.equal(net.sent.length, 1);
  assert.match(net.sent[0].text, /^ダナンの今日/);
  assert.ok(net.sent[0].text.split("\n").length <= 3);
});

test("with no location yet, the morning sends nothing", async () => {
  await worker.scheduled({}, fakeEnv());
  assert.equal(net.sent.length, 0);
});

test("a weather failure is one line, not silence", async () => {
  net.restore();
  net = fakeNet([["api.open-meteo.com", () => new Response("no", { status: 500 })]]);
  const env = fakeEnv();
  env.KV.data.set("location", JSON.stringify({ lat: 1, lon: 2, place: "ダナン" }));
  await worker.scheduled({}, env);
  assert.match(net.sent[0].text, /ダナンの天気を取得できませんでした/);
});

test("rain hours join into spans", () => {
  const times = [13, 14, 15, 16, 20].map((h) => `2026-11-06T${h}:00`);
  assert.equal(rainLine(times, [10, 60, 80, 10, 50]), "雨：14〜16時、20〜21時（最大80%）");
  assert.equal(rainLine(times, [0, 10, 20, 10, 0]), "雨の心配は少なめです（最大20%）");
  assert.equal(skyWord(999), "不明");
  assert.match(weatherText("x", METEO), /雨：14〜15時/);
});

test("over the limit Claude is not called, and the reply says the weather goes on", async () => {
  const env = fakeEnv();
  env.KV.data.set(`usage:${hcmParts().month}`, JSON.stringify({ usd: 10, notified: true }));
  const claude = fakeClaude([]);
  await handleMessage(env, msg({ text: "こんにちは" }), new Date(), claude);
  assert.equal(claude.requests.length, 0);
  assert.match(net.sent[0].text, /来月まで/);
});

test("reaching the limit tells once", async () => {
  const env = fakeEnv();
  env.KV.data.set(`usage:${hcmParts().month}`, JSON.stringify({ usd: 9.9999, notified: false }));
  const claude = fakeClaude([reply([TEXT("はい")])]);
  await handleMessage(env, msg({ text: "こんにちは" }), new Date(), claude);
  assert.equal(net.sent.filter((m) => /上限/.test(m.text)).length, 1);
  assert.equal(JSON.parse(env.KV.data.get(`usage:${hcmParts().month}`)).notified, true);
});

test("usage adds up tokens, cache and searches", () => {
  const usd = costOf("claude-opus-5-5", {
    input_tokens: 1e6, output_tokens: 1e6, cache_read_input_tokens: 1e6, cache_creation_input_tokens: 1e6,
    server_tool_use: { web_search_requests: 100 },
  });
  assert.ok(Math.abs(usd - (4 + 20 + 0.2 + 5 + 1)) < 1e-9);
});

test("a draft comes alone in its own message, and nothing is sent to anyone else", async () => {
  const env = fakeEnv();
  const claude = fakeClaude([
    reply([{ type: "tool_use", id: "t1", name: "deliver_draft", input: { draft: "Xin lỗi, tôi không thể.", meaning_ja: "すみません、できません" } }], "tool_use"),
    reply([TEXT("作りました。")]),
  ]);
  await handleMessage(env, msg({ text: "丁寧に断りたい" }), new Date(), claude);
  assert.deepEqual(net.sent.map((m) => m.text), ["作りました。", "意味：すみません、できません", "Xin lỗi, tôi không thể."]);
  assert.ok(net.sent.every((m) => m.chat_id === "42" && m.link_preview_options.is_disabled));
});

test("a photo goes to Claude but is not kept", async () => {
  net.restore();
  net = fakeNet([
    ["/getFile", () => ({ result: { file_path: "p.jpg" } })],
    ["/file/bot", () => new Response(new Uint8Array([1, 2, 3]))],
  ]);
  const env = fakeEnv();
  const claude = fakeClaude([reply([TEXT("フォー：米の麺")])]);
  await handleMessage(env, msg({ photo: [{ file_id: "small" }, { file_id: "big" }], caption: "訳して" }), new Date(), claude);
  const blocks = claude.requests[0].messages.at(-1).content;
  assert.equal(blocks.find((b) => b.type === "image").source.data, "AQID");
  assert.equal(net.sent[0].text, "フォー：米の麺");
  const stored = [...env.KV.data.values()].join("");
  assert.ok(!stored.includes("AQID"));
  assert.match(env.KV.data.get("context"), /\[写真\] 訳して/);
});

test("once mail is read, the web tool is gone", async () => {
  net.restore();
  net = fakeNet([
    ["oauth2.googleapis.com", () => ({ access_token: "a" })],
    ["/messages?", () => ({ messages: [] })],
  ]);
  const env = fakeEnv({ GOOGLE_REFRESH_TOKEN: "r", GOOGLE_CLIENT_ID: "c", GOOGLE_CLIENT_SECRET: "s" });
  const claude = fakeClaude([
    reply([{ type: "tool_use", id: "t1", name: "search_gmail", input: { query: "from:a" } }], "tool_use"),
    reply([TEXT("ありません")]),
  ]);
  await runTurn(env, [TEXT("メール")], [], new Date(), claude);
  const names = (r) => r.tools.map((t) => t.name);
  assert.ok(names(claude.requests[0]).includes("web_search"));
  assert.ok(!names(claude.requests[1]).includes("web_search"));
});

test("if the web came first, no tool can run after mail is read", () => {
  assert.equal(toolsFor({ privateSeen: true, webUsed: true }).tool_choice.type, "none");
  assert.equal(toolsFor({ privateSeen: false, webUsed: false }).tool_choice.disable_parallel_tool_use, true);
});

test("the assistant's tools can neither send nor change anything", () => {
  const names = toolsFor({ privateSeen: false, webUsed: false }).tools.map((t) => t.name);
  assert.deepEqual(names.sort(), ["deliver_draft", "list_calendar_events", "read_gmail", "search_gmail", "web_search"]);
});

test("a paused turn resumes without a new user message", async () => {
  const env = fakeEnv();
  const claude = fakeClaude([reply([{ type: "server_tool_use", id: "s", name: "web_search", input: {} }], "pause_turn"), reply([TEXT("結果")])]);
  const out = await runTurn(env, [TEXT("調べて")], [], new Date(), claude);
  assert.equal(out.text, "結果");
  assert.equal(claude.requests[1].messages.at(-1).role, "assistant");
});

test("a Google failure reaches Claude as a named error, not a crash", async () => {
  const env = fakeEnv(); // no Google settings
  const claude = fakeClaude([
    reply([{ type: "tool_use", id: "t1", name: "list_calendar_events", input: { start_date: "2026-11-09", end_date: "2026-11-15" } }], "tool_use"),
    reply([TEXT("つながっていません")]),
  ]);
  await runTurn(env, [TEXT("空きは？")], [], new Date(), claude);
  const result = claude.requests[1].messages.at(-1).content[0];
  assert.equal(result.is_error, true);
  assert.match(result.content, /つながっていません/);
});

test("a refusal gets one line and is not remembered", async () => {
  const env = fakeEnv();
  await handleMessage(env, msg({ text: "x" }), new Date(), fakeClaude([reply([], "refusal")]));
  assert.match(net.sent[0].text, /お答えできません/);
  assert.equal(env.KV.data.get("context"), undefined);
});

test("sources are listed once each", () => {
  const c = { url: "https://a.example", title: "A" };
  assert.deepEqual(sourcesOf([TEXT("x", { citations: [c, c] }), TEXT("y")]), ["A\nhttps://a.example"]);
});

test("forget clears the conversation", async () => {
  const env = fakeEnv();
  env.KV.data.set("context", "[]");
  await handleMessage(env, msg({ text: "忘れて" }));
  assert.equal(env.KV.data.get("context"), undefined);
});

test("calendar reads refuse a long range and skip cancelled events", async () => {
  const get = async (url) => url.includes("calendarList")
    ? { items: [{ id: "a@b", summary: "仕事" }, { id: "hidden", selected: false }] }
    : { items: [{ status: "cancelled" }, { summary: "会議", start: { dateTime: "2026-11-09T10:00:00+07:00" }, end: { dateTime: "2026-11-09T11:00:00+07:00" } }] };
  await assert.rejects(listEvents(get, { start_date: "2026-01-01", end_date: "2026-12-31" }));
  const { events } = await listEvents(get, { start_date: "2026-11-09", end_date: "2026-11-15" });
  assert.equal(events.length, 1);
  assert.equal(events[0].title, "会議");
  assert.equal(events[0].blocks_time, true);
});

test("a long mail says it was cut, and text is decoded", async () => {
  const data = btoa("日本語".replace(/./g, () => "a").repeat(4000)).replace(/\+/g, "-").replace(/\//g, "_");
  const get = async () => ({ payload: { headers: [{ name: "From", value: "x" }], parts: [{ mimeType: "text/plain", body: { data } }] } });
  const m = await readMail(get, { id: "abc" });
  assert.equal(m.cut_off, true);
  assert.equal(m.from, "x");
  await assert.rejects(readMail(get, { id: "../etc" }));
  assert.equal(decodeBase64Url(btoa("ok")), "ok");
  assert.equal(stripHtml("<style>x</style><p>a &amp; b</p>"), "a & b");
});

test("long text splits under the Telegram limit", () => {
  const parts = splitText("a".repeat(9000));
  assert.ok(parts.length === 3 && parts.every((p) => p.length <= 4000));
  assert.equal(parts.join(""), "a".repeat(9000));
});

test("the month follows Ho Chi Minh time", () => {
  assert.equal(hcmParts(new Date("2026-11-30T18:00:00Z")).month, "2026-12");
  assert.equal(hcmParts(new Date("2026-11-30T16:00:00Z")).month, "2026-11");
});
