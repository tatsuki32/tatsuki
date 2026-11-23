import test from "node:test";
import assert from "node:assert/strict";
import { handle } from "../src/insights.js";
import { seal } from "../src/accounts.js";

const env = { TOKEN_KEY: btoa("0".repeat(32)) };
env.DB = { prepare: () => ({ bind: (id) => ({ first: async () => (id === "instagram:111" ? { token: await seal(env, "SECRET-TOKEN") } : null) }) }) };
const ask = async (what, q, answer, headers = {}, status = 200) => {
  const seen = [];
  globalThis.fetch = async (u, o) => { seen.push({ u: new URL(u), o }); const a = typeof answer === "function" ? answer(new URL(u)) : answer; return new Response(JSON.stringify(a), { status, headers }); };
  const r = await handle(env, "111", what, new URLSearchParams(q));
  assert.ok(!JSON.stringify(r).includes("SECRET-TOKEN"));
  for (const s of seen) assert.ok(!s.u.href.includes("SECRET-TOKEN") && (s.o.method || "GET") === "GET");
  return { r, seen };
};

test("comments and replies come back without their text", async () => {
  const { r } = await ask("comments", { media: "5" }, { data: [{ id: "1", username: "a", text: "PRIVATE", timestamp: "2026-11-01T00:00:00+0000", replies: { data: [{ id: "2", from: { username: "b" }, text: "PRIVATE", timestamp: "2026-11-02T00:00:00+0000" }] } }], paging: { next: "x", cursors: { after: "ZZ" } } });
  assert.deepEqual(r.items.map((x) => x.u), ["a", "b"]);
  assert.equal(r.after, "ZZ");
  assert.ok(!JSON.stringify(r).includes("PRIVATE"));
});
test("messages: only theirs, only who and when, and quiet chats are not asked for", async () => {
  const { r, seen } = await ask("dms", { since: "1790000000000" }, (u) => {
    if (u.pathname.endsWith("/me/accounts")) return { data: [{ id: "9", access_token: "P", instagram_business_account: { id: "111" } }] };
    if (u.pathname.endsWith("/conversations")) return { data: [{ id: "new", updated_time: "2026-11-20T00:00:00+0000" }, { id: "old", updated_time: "2026-01-01T00:00:00+0000" }] };
    if (/limit\(20\)/.test(u.searchParams.get("fields"))) return { error: { message: "Please reduce the amount of data you're asking for", code: 1 } };
    return { participants: { data: [{ id: "111", username: "me" }, { id: "7", username: "a" }] }, messages: { data: [{ id: "m1", from: { id: "7" }, created_time: "2026-11-01T00:00:00+0000" }, { id: "m2", from: { id: "111" }, created_time: "2026-11-01T00:00:00+0000" }] } };
  });
  assert.deepEqual(r.items.map((c) => c.items.map((x) => [x.id, x.u])), [[["m1", "a"]], []]);
  for (const s of seen) assert.ok(!/message[,}]|text|attachments/.test(s.u.searchParams.get("fields")));
  assert.equal(r.meter.calls, 4);
});
test("the rate-limit numbers come through, also on failure", async () => {
  const h = { "x-app-usage": '{"call_count":12,"total_time":30,"total_cputime":1}', "x-business-use-case-usage": '{"1":[{"call_count":4,"estimated_time_to_regain_access":7}]}' };
  const { r } = await ask("profile", {}, { error: { message: "limit", code: 4 } }, h, 400);
  assert.deepEqual([r.ok, r.code, r.meter.app, r.meter.buc, r.meter.wait], [false, 4, 30, 4, 7]);
});
test("a personal account is not found rather than an error; a permission problem stays an error", async () => {
  assert.equal((await ask("discover", { u: "a" }, { error: { message: "x", code: 110, error_subcode: 2207013 } }, {}, 400)).r.found, false);
  const bad = (await ask("discover", { u: "a" }, { error: { message: "no permission", code: 10 } }, {}, 400)).r;
  assert.deepEqual([bad.ok, bad.code], [false, 10]);
});
test("a looked-up account", async () => {
  const { r, seen } = await ask("discover", { u: "a.b_c", media: "1" }, { business_discovery: { username: "a.b_c", name: "N", followers_count: 3, media: { data: [{ like_count: 2, timestamp: "2026-11-01T00:00:00+0000" }] } } });
  assert.deepEqual([r.found, r.followers, r.media[0].likes, r.media[0].comments], [true, 3, 2, null]);
  assert.match(seen[0].u.searchParams.get("fields"), /^business_discovery\.username\(a\.b_c\)/);
});
test("nothing odd reaches Meta", async () => {
  for (const [w, q] of [["discover", { u: "a){x}" }], ["discover", { u: "" }], ["comments", { media: "1/../2" }], ["comments", {}], ["audience", { by: "x" }], ["constructor", {}], ["media", { after: "a&b" }], ["dms", { since: "x" }]]) {
    const { r, seen } = await ask(w, q, {});
    assert.deepEqual([r.ok, seen.length], [false, 0], w);
  }
});
test("an account that is not connected", async () => {
  const r = await handle(env, "222", "profile", new URLSearchParams());
  assert.match(r.error, /not connected/);
});
