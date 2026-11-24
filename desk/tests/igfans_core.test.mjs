import test from "node:test";
import assert from "node:assert/strict";
import { deflateRawSync } from "node:zlib";
import * as C from "../igfans_web/core.js";

const T = 1700000000;
const item = (u, ts) => ({ title: "", media_list_data: [], string_list_data: [{ href: `https://www.instagram.com/${u}`, value: u, timestamp: ts }] });
const many = (n, from = 0) => Array.from({ length: n }, (_, i) => item(`user${from + i}`, T + from + i));
const zip = (files) => {
  const parts = [], dir = [];
  let at = 0;
  for (const [name, text, store] of files) {
    const nb = Buffer.from(name), raw = Buffer.from(text), data = store ? raw : deflateRawSync(raw), h = Buffer.alloc(30), c = Buffer.alloc(46);
    h.writeUInt32LE(0x04034b50, 0); h.writeUInt16LE(store ? 0 : 8, 8); h.writeUInt16LE(nb.length, 26);
    c.writeUInt32LE(0x02014b50, 0); c.writeUInt16LE(store ? 0 : 8, 10); c.writeUInt32LE(data.length, 20); c.writeUInt32LE(raw.length, 24); c.writeUInt16LE(nb.length, 28); c.writeUInt32LE(at, 42);
    parts.push(h, nb, data); dir.push(c, nb); at += 30 + nb.length + data.length;
  }
  const d = Buffer.concat(dir), e = Buffer.alloc(22);
  e.writeUInt32LE(0x06054b50, 0); e.writeUInt16LE(files.length, 10); e.writeUInt32LE(d.length, 12); e.writeUInt32LE(at, 16);
  return new Blob([...parts, d, e]);
};
const snap = (id, at, followers, following = {}) => ({ id, at, followers, following });

test("12,000 people in three files come out as 12,000 CSV rows", async () => {
  const z = zip([["connections/followers_and_following/followers_1.json", JSON.stringify(many(5000))], ["connections/followers_and_following/followers_2.json", JSON.stringify(many(5000, 5000))],
    ["connections/followers_and_following/followers_3.json", JSON.stringify(many(2000, 10000)), true],
    ["connections/followers_and_following/following.json", JSON.stringify({ relationships_following: [{ title: "user1", string_list_data: [{ href: "https://www.instagram.com/_u/user1", timestamp: T }] }] })],
    ["your_activity/messages/secret.json", "{}"]]);
  z.name = "instagram-my.page-2026-11-24-abc.zip";
  const r = await C.readDrop([z]);
  assert.equal(r.account, "my.page");
  assert.equal(Object.keys(r.followers).length, 12000);
  assert.deepEqual(Object.keys(r.following), ["user1"]);
  const t = C.table({ imports: [snap("a", 1, r.followers, r.following)], events: [], profiles: {}, now: Date.now() });
  const rows = C.pick(t.rows, "all", "newest", "");
  assert.equal(rows[0].u, "user11999");
  assert.equal(C.csv(rows).split("\r\n").length, 12001);
  assert.equal(C.pick(t.rows, "mutual", "name", "").length, 1);
  const t0 = performance.now();
  C.pick(t.rows, "all", "score", "user11");
  assert.ok(performance.now() - t0 < 200);
});

test("both shapes, names in title or href, odd rows", () => {
  const r = C.readFiles([{ name: "followers_1.json", text: JSON.stringify({ relationships_followers: [item("A.b_c", T), { string_list_data: [] }] }) },
    { name: "following.json", text: JSON.stringify({ relationships_following: [{ title: "", string_list_data: [{ href: "https://www.instagram.com/_u/zed", timestamp: 0 }] }] }) }]);
  assert.deepEqual(r.followers, { "a.b_c": T * 1000 });
  assert.deepEqual(r.following, { zed: 0 });
  assert.match(r.warnings.join(), /1 rows/);
});

test("wrong exports say what to do", () => {
  assert.throws(() => C.readFiles([{ name: "followers_1.html", text: "" }]), /choose JSON/);
  assert.throws(() => C.readFiles([{ name: "x.json", text: "{}" }]), /No followers file/);
  assert.throws(() => C.readFiles([{ name: "followers_1.json", text: "[]" }]), /empty/);
  assert.throws(() => C.readFiles([{ name: "followers_1.json", text: JSON.stringify(many(2)) }, { name: "followers_3.json", text: JSON.stringify(many(2, 9)) }]), /missing/);
  assert.throws(() => C.readFiles([{ name: "followers_1.json", text: "{oops" }]), /could not be read/);
});
test("a file that is not a ZIP", async () => { await assert.rejects(C.readZip(new Blob(["hello"])), /not a ZIP/); });

test("one export has one mark", () => {
  assert.equal(C.fingerprint({ a: 1, b: 2 }, {}), C.fingerprint({ b: 2, a: 1 }, {}));
  assert.notEqual(C.fingerprint({ a: 1 }, { b: 1 }), C.fingerprint({ a: 1, b: 1 }, {}));
});

test("only the person who left shows; a changed ID is not a loss", () => {
  const h = C.history([snap("1", 1, { a: 10, b: 20, c: 30 }), snap("2", 2, { a: 10, d: 30, e: 99 })])[0];
  assert.deepEqual([h.added, h.gone, h.renamed], [["e"], ["b"], [{ from: "c", to: "d" }]]);
  assert.deepEqual(C.history([snap("1", 1, { a: 0, b: 0 }), snap("2", 2, { c: 0, b: 0 })])[0].renamed, []);
});

test("a short date range is never read as people leaving", () => {
  const old = Object.fromEntries(Array.from({ length: 200 }, (_, i) => [`u${i}`, 1600000000000 + i * C.DAY]));
  const part = Object.fromEntries(Object.entries(old).slice(150));
  assert.match(C.looksPartial(part, old), /All time/);
  assert.equal(C.looksPartial(Object.fromEntries(Object.entries(old).slice(1)), old), "");
  assert.match(C.looksPartial(part, null, 200), /profile says 200/);
  assert.equal(C.history([snap("1", 1, old), { ...snap("2", 2, part), partial: "x" }]).length, 0);
  assert.equal(C.table({ imports: [snap("1", 1, old), { ...snap("2", 2, part), partial: "x" }], now: 3 }).latest.id, "1");
});

test("fans: weights, half-life, a year, and people who do not follow", () => {
  const now = 1e12, d = C.DAY;
  const ev = [{ u: "a", k: "comment", at: now }, { u: "A", k: "dm", at: now - 90 * d }, { u: "b", k: "tag", at: now - 366 * d }, { u: "c", k: "tag", at: now }, { u: "me", k: "comment", at: now }];
  const t = C.table({ imports: [snap("1", 1, { a: 5 })], events: ev, profiles: { a: { found: true, name: "An", followers: 9 } }, now, own: "me" });
  const f = C.pick(t.rows, "fans", "score", "");
  assert.deepEqual(f.map((r) => [r.u, r.score, r.follower]), [["a", 4, true], ["c", 4, false]]);
  assert.equal(f[0].name, "An");
  assert.equal(C.scores(ev, now, { comment: 10 }).get("a").score, 11);
});

test("a CSV cell never becomes a formula", () => {
  const t = C.table({ imports: [snap("1", 1, { a: 5 })], profiles: { a: { found: true, name: '=HYPERLINK("x")', bio: "+1 line" } }, now: 9 });
  const out = C.csv(t.rows);
  assert.ok(out.includes(`"'=HYPERLINK(""x"")"`) && out.includes(`"'+1 line"`));
});

test("an ID that is also a JavaScript word", () => {
  const h = C.history([snap("1", 1, { a: 10 }), snap("2", 2, { a: 10, constructor: 20 })])[0];
  assert.deepEqual(h.added, ["constructor"]);
  const t = C.table({ imports: [snap("1", 1, { a: 1 })], events: [{ u: "constructor", k: "dm", at: 5 }], profiles: {}, now: 9 });
  assert.equal(t.rows.find((r) => r.u === "constructor").follower, false);
});

test("people unfollowed since the export leave the lists", () => {
  const imports = [{ at: 1, followers: { m: 1 }, following: { m: 1, idol: 1, other: 1 }, hasFollowing: true }];
  const t = C.table({ imports, events: [], profiles: {}, now: 2, gone: ["idol", "m"] });
  assert.deepEqual(C.pick(t.rows, "notback", "az", "").map((r) => r.u), ["other"]);
  assert.deepEqual(C.pick(t.rows, "youdont", "az", "").map((r) => r.u), ["m"]);
});
