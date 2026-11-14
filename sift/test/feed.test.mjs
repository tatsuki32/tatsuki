// node --test sift/test  (no network: every page is a fake)
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseFeed, feedLinks, discover, cleanUrl, text } from "../src/feed.js";

const RSS = `<?xml version="1.0"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
<title>Example &amp; News</title><link>https://news.example.com/</link>
<atom:link href="https://news.example.com/rss" rel="self"/>
<item><title><![CDATA[First <b>story</b>]]></title><link>https://news.example.com/a</link>
<description>&lt;p&gt;Rates rose by 0.25%.&lt;/p&gt;</description><pubDate>Sat, 14 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Second</title><link>/b</link><description><![CDATA[<p>Hello&nbsp;world</p>]]></description></item>
<item><title>Dup</title><link>https://news.example.com/a</link></item>
<item><title>Guid only</title><guid>https://news.example.com/c</guid></item>
</channel></rss>`;

const ATOM = `<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<title>Tech Blog</title><link rel="self" href="https://blog.example.org/atom.xml"/><link href="https://blog.example.org/"/>
<entry><title type="html">Atom &lt;one&gt;</title><link rel="alternate" href="https://blog.example.org/1"/><updated>2026-11-13T08:00:00Z</updated><summary>Short summary</summary></entry>
<entry><title>Two</title><link href="/2"/><published>2026-11-12T08:00:00Z</published><content type="html">&lt;p&gt;Body&lt;/p&gt;</content></entry>
</feed>`;

test("RSS: titles, links, descriptions, dates, duplicates", () => {
  const f = parseFeed(RSS, "https://news.example.com/rss");
  assert.equal(f.title, "Example & News");
  assert.equal(f.site, "https://news.example.com/");
  assert.equal(f.items.length, 3);
  assert.equal(f.items[0].title, "First story");
  assert.equal(f.items[0].desc, "Rates rose by 0.25%.");
  assert.equal(f.items[0].published, Date.parse("2026-11-14T10:00:00Z"));
  assert.equal(f.items[1].url, "https://news.example.com/b");
  assert.equal(f.items[1].desc, "Hello world");
  assert.equal(f.items[2].url, "https://news.example.com/c");
});

test("Atom: alternate link, relative link, summary or content", () => {
  const f = parseFeed(ATOM, "https://blog.example.org/atom.xml");
  assert.equal(f.title, "Tech Blog");
  assert.equal(f.site, "https://blog.example.org/");
  assert.equal(f.items[0].url, "https://blog.example.org/1");
  assert.equal(f.items[0].title, "Atom <one>");
  assert.equal(f.items[0].desc, "Short summary");
  assert.equal(f.items[1].url, "https://blog.example.org/2");
  assert.equal(f.items[1].desc, "Body");
  assert.equal(f.items[1].published, Date.parse("2026-11-12T08:00:00Z"));
});

test("not a feed throws", () => {
  assert.throws(() => parseFeed("<html><body>hi</body></html>"));
});

test("feed links in a page head", () => {
  const html = `<head><link rel="alternate" type="application/rss+xml" title="All" href="/feed.xml"><link rel="stylesheet" href="/a.css"><link rel="alternate" type="application/atom+xml" href="https://x.example/atom"></head>`;
  assert.deepEqual(feedLinks(html, "https://x.example/news/"), [{ url: "https://x.example/feed.xml", title: "All" }, { url: "https://x.example/atom", title: "" }]);
});

test("addresses: no scheme gets https, local and private ones are refused", () => {
  assert.equal(cleanUrl("example.com"), "https://example.com/");
  assert.equal(cleanUrl("http://localhost:4747"), "");
  assert.equal(cleanUrl("http://192.168.1.2/"), "");
  assert.equal(cleanUrl("ftp://example.com"), "");
});

test("long text is cut with an ellipsis", () => {
  assert.equal(text("a".repeat(10), 5), "aaaa…");
});

function fakeFetch(pages) {
  return async (url) => {
    const body = pages[url];
    return { ok: body !== undefined, status: body === undefined ? 404 : 200, url, text: async () => body ?? "" };
  };
}

test("discover: from the home page's link tag", async () => {
  const r = await discover("news.example.com", fakeFetch({
    "https://news.example.com/": `<head><link rel="alternate" type="application/rss+xml" href="/rss"></head>`,
    "https://news.example.com/rss": RSS,
  }));
  assert.equal(r.found.length, 1);
  assert.equal(r.found[0].url, "https://news.example.com/rss");
  assert.deepEqual(r.found[0].sample, ["First story", "Second", "Guid only"]);
});

test("discover: a pasted feed URL", async () => {
  const r = await discover("https://blog.example.org/atom.xml", fakeFetch({ "https://blog.example.org/atom.xml": ATOM }));
  assert.equal(r.found[0].title, "Tech Blog");
});

test("discover: common paths when the page names none", async () => {
  const r = await discover("https://blog.example.org/", fakeFetch({ "https://blog.example.org/": "<html></html>", "https://blog.example.org/atom.xml": ATOM }));
  assert.equal(r.found[0].url, "https://blog.example.org/atom.xml");
});

test("discover: nothing found gives a reason", async () => {
  const r = await discover("https://none.example/", fakeFetch({ "https://none.example/": "<html></html>" }));
  assert.equal(r.found.length, 0);
  assert.match(r.why, /No feed found/);
});
