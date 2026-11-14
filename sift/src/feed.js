// Reading RSS 2.0 and Atom without a library (Workers has no DOMParser), and finding a site's feed from its home page.
// Only the title, link, short description and date of each entry are kept. The article page itself is never fetched.

export const UA = "Mozilla/5.0 (compatible; SiftDaily/1.0; +https://sift.tany.im)";
const MAX_XML = 400_000; // characters read from one feed; the newest entries come first
const MAX_ITEMS = 40;
const DESC_MAX = 500;

const NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", hellip: "…", mdash: "—", ndash: "–", rsquo: "’", lsquo: "‘", rdquo: "”", ldquo: "“" };

export function decode(s) {
  return String(s || "").replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === "#") {
      const n = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      try { return String.fromCodePoint(n); } catch { return m; }
    }
    return NAMED[e.toLowerCase()] ?? m;
  });
}

function unCdata(s) {
  return String(s || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1");
}

// plain text from a feed field: CDATA opened, tags removed, entities decoded (twice: many feeds escape their HTML)
export function text(s, max = DESC_MAX) {
  let t = unCdata(s);
  t = decode(t);
  t = t.replace(/<(script|style)[\s\S]*?<\/\1>/gi, " ").replace(/<[^>]+>/g, " ");
  t = decode(t).replace(/\s+/g, " ").trim();
  return t.length > max ? t.slice(0, max - 1).trimEnd() + "…" : t;
}

// a title: real tags removed, then entities decoded once, so "&lt;one&gt;" stays "<one>"
export function line(s, max = 300) {
  const t = decode(unCdata(s).replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
  return t.length > max ? t.slice(0, max - 1).trimEnd() + "…" : t;
}

function tag(block, names) {
  for (const n of names) {
    const re = new RegExp(`<${n}(?:\\s[^>]*)?>([\\s\\S]*?)</${n}>`, "i");
    const m = block.match(re);
    if (m && m[1].trim()) return m[1];
  }
  return "";
}

function attr(tagText, name) {
  const m = tagText.match(new RegExp(`\\s${name}\\s*=\\s*("([^"]*)"|'([^']*)')`, "i"));
  return m ? decode(m[2] ?? m[3] ?? "") : "";
}

function atomLink(block) {
  const links = block.match(/<link\b[^>]*>/gi) || [];
  let first = "";
  for (const l of links) {
    const href = attr(l, "href");
    if (!href) continue;
    const rel = attr(l, "rel").toLowerCase();
    if (!rel || rel === "alternate") return href;
    if (!first && rel !== "self" && rel !== "enclosure") first = href;
  }
  return first;
}

function when(s) {
  const t = Date.parse(text(s, 100));
  return Number.isFinite(t) ? t : 0;
}

function absolute(href, base) {
  try {
    const u = new URL(href.trim(), base || undefined);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : "";
  } catch {
    return "";
  }
}

export function looksLikeFeed(body) {
  const head = String(body || "").slice(0, 2000);
  return /<rss[\s>]/i.test(head) || /<feed[\s>]/i.test(head) || /<rdf:RDF[\s>]/i.test(head);
}

// { title, site, items: [{ url, title, desc, published }] } — throws when it is not a feed
export function parseFeed(body, feedUrl = "") {
  const xml = String(body || "").slice(0, MAX_XML);
  if (!looksLikeFeed(xml)) throw new Error("not an RSS or Atom feed");
  const atom = /<feed[\s>]/i.test(xml.slice(0, 2000)) && !/<rss[\s>]/i.test(xml.slice(0, 2000));
  const blocks = xml.match(atom ? /<entry[\s>][\s\S]*?<\/entry>/gi : /<item[\s>][\s\S]*?<\/item>/gi) || [];
  const headEnd = xml.search(atom ? /<entry[\s>]/i : /<item[\s>]/i);
  const head = headEnd > 0 ? xml.slice(0, headEnd) : xml;
  const title = line(tag(head, ["title"]), 120);
  let site = atom ? atomLink(head) : text(tag(head, ["link"]), 500);
  site = absolute(site, feedUrl) || feedUrl;
  const items = [];
  const seen = new Set();
  for (const b of blocks.slice(0, MAX_ITEMS)) {
    let link = atom ? atomLink(b) : text(tag(b, ["link"]), 2000) || attr((b.match(/<link\b[^>]*>/i) || [""])[0], "href");
    if (!link) {
      const g = tag(b, ["guid"]);
      if (/^https?:/i.test(text(g, 2000))) link = text(g, 2000);
    }
    const url = absolute(link, site || feedUrl);
    if (!url || seen.has(url)) continue;
    seen.add(url);
    const t = line(tag(b, ["title"]));
    const desc = text(tag(b, atom ? ["summary", "content"] : ["description", "content:encoded", "summary"]));
    const published = when(tag(b, atom ? ["published", "updated"] : ["pubDate", "dc:date", "published", "updated"]));
    items.push({ url, title: t || url, desc: desc === t ? "" : desc, published });
  }
  return { title, site, items };
}

// feed links a page announces in its <head>
export function feedLinks(html, pageUrl) {
  const out = [];
  for (const l of String(html || "").slice(0, 300_000).match(/<link\b[^>]*>/gi) || []) {
    const rel = attr(l, "rel").toLowerCase().split(/\s+/);
    const type = attr(l, "type").toLowerCase();
    if (!rel.includes("alternate")) continue;
    if (!/(rss|atom)\+xml|application\/feed\+json|text\/xml|application\/xml/.test(type)) continue;
    if (type.includes("json")) continue; // JSON Feed is not read yet
    const u = absolute(attr(l, "href"), pageUrl);
    if (u && !out.some((x) => x.url === u)) out.push({ url: u, title: attr(l, "title") });
  }
  return out;
}

export const COMMON = ["/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml", "/feeds/posts/default"];

export function cleanUrl(input) {
  let s = String(input || "").trim();
  if (!s) return "";
  if (!/^[a-z]+:\/\//i.test(s)) s = "https://" + s;
  const u = absolute(s);
  if (!u) return "";
  const host = new URL(u).hostname;
  // nothing on this machine or inside a home network
  if (/^(localhost|.*\.local|.*\.internal)$/i.test(host) || /^(127\.|10\.|192\.168\.|169\.254\.|0\.)/.test(host) || /^172\.(1[6-9]|2\d|3[01])\./.test(host) || host.includes(":")) return "";
  return u;
}

export async function get(url, fetcher = fetch, ms = 10_000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetcher(url, { headers: { "user-agent": UA, accept: "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5" }, redirect: "follow", signal: ctl.signal });
    if (!r.ok) throw new Error(`the site answered ${r.status}`);
    return { url: r.url || url, body: await r.text() };
  } catch (e) {
    throw new Error(e.name === "AbortError" ? "the site took longer than 10 seconds" : e.message);
  } finally {
    clearTimeout(timer);
  }
}

// a pasted URL → feeds found there, each with its name and newest 3 titles. Never throws; { found: [], why }
export async function discover(input, fetcher = fetch) {
  const start = cleanUrl(input);
  if (!start) return { found: [], why: "That doesn't look like a public web address." };
  let page;
  try {
    page = await get(start, fetcher);
  } catch (e) {
    return { found: [], why: `Couldn't open the page: ${e.message}.` };
  }
  const found = [];
  const add = (url, body) => {
    try {
      const f = parseFeed(body, url);
      if (!f.items.length || found.some((x) => x.url === url)) return;
      found.push({ url, title: f.title || new URL(url).hostname, site: f.site, sample: f.items.slice(0, 3).map((i) => i.title) });
    } catch { /* not a feed */ }
  };
  if (looksLikeFeed(page.body)) {
    add(page.url, page.body);
    return found.length ? { found } : { found, why: "The feed has no entries." };
  }
  const cands = feedLinks(page.body, page.url).slice(0, 4);
  const origin = new URL(page.url).origin;
  if (!cands.length) for (const p of COMMON) cands.push({ url: origin + p });
  for (const c of cands) {
    if (found.length >= 4) break;
    try {
      const f = await get(c.url, fetcher, 8000);
      add(f.url, f.body);
    } catch { /* try the next one */ }
  }
  return found.length ? { found } : { found, why: "No feed found on that page. Try the site's RSS link if it has one." };
}
