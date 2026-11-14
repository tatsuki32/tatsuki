import { test } from "node:test";
import assert from "node:assert/strict";
import { mime, digestEmail, b64, dayLabel } from "../src/mail.js";
import { readAnswer, prompt, summarize } from "../src/summary.js";

const decode = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\s+/g, "")), (c) => c.charCodeAt(0)));

test("base64 keeps Japanese text", () => {
  assert.equal(decode(b64("日本のニュース · ok")), "日本のニュース · ok");
});

test("digest email: brief, folders, links, escaping", () => {
  const m = digestEmail({
    day: "2026-11-15", brief: "Two threads today.", total: 2, appUrl: "https://sift.tany.im/app",
    groups: [{ folder: "World", items: [{ title: "A <b>", url: "https://a.example/1", site: "A", line: "One line.", later: "https://sift.tany.im/m/1/later/x", task: "https://sift.tany.im/m/1/task/x" }] }, { folder: "Tech", items: [{ title: "B", url: "https://b.example/2", site: "B", line: "", later: "l", task: "t" }] }],
  });
  assert.equal(m.subject, "Sift Daily · Nov 15 · 2 new");
  assert.match(m.html, /A &lt;b&gt;/);
  assert.match(m.html, /Two threads today\./);
  assert.match(m.text, /WORLD\n- A <b> \(A\)\n  One line\.\n  https:\/\/a\.example\/1/);
  assert.ok(m.html.indexOf("World") < m.html.indexOf("Tech"));
});

test("MIME: encoded subject, two parts, CRLF", () => {
  const raw = mime({ from: "news@mail.example.com", fromName: "Sift Daily", to: "me@example.com", subject: "Sift Daily · Nov 15", text: "hi", html: "<p>hi</p>", id: "abc" });
  assert.match(raw, /^From: Sift Daily <news@mail.example.com>\r\n/);
  assert.match(raw, /Subject: =\?UTF-8\?B\?/);
  assert.equal((raw.match(/--sift-abc/g) || []).length, 3);
  assert.match(raw, /Message-ID: <abc@mail.example.com>/);
});

test("day label", () => assert.equal(dayLabel("2026-01-03"), "Jan 3"));

test("summary answer: JSON inside text, missing lines become empty", () => {
  const r = readAnswer('Here: {"lines": {"1": "First.", "3": "Third."}, "brief": "Day."}', 3);
  assert.deepEqual(r.lines, ["First.", "", "Third."]);
  assert.equal(r.brief, "Day.");
  assert.throws(() => readAnswer("no json", 1));
});

test("prompt numbers each article with its site", () => {
  assert.equal(prompt([{ site: "S", title: "T", descr: "D" }]), "1. [S] T\n   D");
});

test("summarize calls the API once and reads usage", async () => {
  let sent;
  const fake = async (url, opt) => {
    sent = JSON.parse(opt.body);
    return { ok: true, json: async () => ({ content: [{ type: "text", text: '{"lines":{"1":"L"},"brief":"B"}' }], usage: { input_tokens: 100, output_tokens: 20 } }) };
  };
  const r = await summarize({ ANTHROPIC_KEY: "k" }, [{ site: "S", title: "T" }], fake);
  assert.equal(sent.model, "claude-haiku-5-5");
  assert.deepEqual(r, { lines: ["L"], brief: "B", tin: 100, tout: 20 });
});
