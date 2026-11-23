
// Rhizome Box in the browser: the editor, the views and the graph. Ported from the mockup (version 13).
// The server holds the pages; this file keeps a light index of all pages and the full text of the open one.
import { esc, indOf, isUrl, isImg, linksOf as linksOfLines, webLinksOf as webOfLines, imagesOf, textLines as textOfLines, codeBlocks as codeOfLines, makeInline, ytLine, plain, kindOfFile, resolveFile, plainText } from './notation.js';
import { pictureKind, looksLikePicture, cleanPicture } from './picture.js';

(() => {
const BOOT = JSON.parse((document.getElementById('boot') || {}).textContent || '{}');
const OWNER = !!BOOT.owner;
const ME = BOOT.me || 'tatsuki';
const KEY = 'rb';
const DAY = 864e5;
const AVATAR = '/files/avatar.jpg';
const svgUri = s => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s);

const load = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k) || 'null'); return v == null ? d : v; } catch (e) { return d; } };
const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } };
let pages = [];

const S = { visitor: !OWNER, cur:null, edit:-1, edInd:0, touched:new Set(), prevNodes:{}, gpos:{}, graph:null, graphSig:'', gstyle: load(KEY + '-gstyle', 'cards'), gwords: load(KEY + '-gwords', true), stray:{}, ac:null, q:'',
  range:null, undo:{}, redo:{}, css: load(KEY + '-css', true), heat:new Map(), heatMax:1, look: load(KEY + '-look', 'paper'), linkSort:'count', linkQ:'', ctT:null, lastIcon:null,
  meta: new Map(), searchIds: new Set(), searchQ: '', notice: '', conflict: null, newId: '' };
const $ = s => document.querySelector(s);

const title = p => (p.lines ? p.lines[0] : p.title) || 'Untitled';
const byTitle = t => pages.find(p => title(p).toLowerCase() === String(t).toLowerCase());
const byId = id => pages.find(p => p.id === id);
const visible = p => !S.visitor || p.pub;
const visPages = () => pages.filter(visible);
const pageUrl = t => '/' + encodeURIComponent(t).replace(/%20/g, '_');

/* ---------- talking to the server ---------- */
async function api(method, path, body, raw) {
  const o = { method, headers: {}, credentials: 'same-origin' };
  if (method !== 'GET') o.headers['X-RB'] = '1';
  if (body !== undefined) {
    if (raw) { o.body = body; o.headers['Content-Type'] = body.type || 'application/octet-stream'; }
    else { o.body = JSON.stringify(body); o.headers['Content-Type'] = 'application/json'; }
  }
  const r = await fetch(path, o);
  let data = {}; try { data = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, data };
}
async function loadPage(p) {
  const r = await api('GET', '/_/api/page/' + p.id);
  if (r.ok) { p.lines = r.data.page.lines; p.rev = r.data.page.rev; p.saved = true; }
  return r.ok;
}
async function refreshIndex() {
  const r = await api('GET', '/_/api/index').catch(() => null);
  if (!r || !r.ok) return;
  const old = new Map(pages.map(p => [p.id, p]));
  pages = r.data.pages.map(e => {
    const o = old.get(e.id);
    if (o && o.lines && (dirty.has(e.id) || e.id === S.cur)) return Object.assign(o, { rev: dirty.has(e.id) ? o.rev : e.rev });
    return Object.assign(e, { saved: true });
  });
  for (const o of old.values()) if (!o.saved && !byId(o.id)) pages.unshift(o);
}

/* ---------- saving: a moment after you stop typing; kept on this device while offline ---------- */
const dirty = new Set(); let flushT = 0, flushing = false;
const PENDING = KEY + '-pending';
const bodyEmpty = p => p.lines.slice(1).every(l => !l.trim());
function save(p) {
  p = p || byId(S.cur); if (!p || !OWNER || !p.lines) return;
  p.updated = Date.now(); dirty.add(p.id);
  clearTimeout(flushT); flushT = setTimeout(flush, 700);
}
function keepPending(p) { const all = load(PENDING, {}); all[p.id] = { lines: p.lines, pub: p.pub, pin: p.pin || 0, rev: p.rev }; store(PENDING, all); }
function dropPending(id) { const all = load(PENDING, {}); if (all[id]) { delete all[id]; store(PENDING, all); } }
function restorePending() {
  const all = load(PENDING, {});
  for (const [id, v] of Object.entries(all)) {
    let p = byId(id);
    if (!p) { p = { id, links: [], images: [], web: [], descr: '', image: '' }; pages.unshift(p); }
    Object.assign(p, { lines: v.lines, pub: v.pub, pin: v.pin, rev: v.rev });
    dirty.add(id);
  }
  if (dirty.size) flush();
}
async function flush() {
  if (flushing) { clearTimeout(flushT); flushT = setTimeout(flush, 400); return; }
  flushing = true;
  let offline = false;
  try {
    for (const id of [...dirty]) {
      const p = byId(id); dirty.delete(id);
      if (!p || !p.lines || !(p.lines[0] || '').trim()) continue;
      if (p.fresh && bodyEmpty(p)) continue; // a page only becomes real once it has something in it
      const sent = JSON.stringify(p.lines);
      let r;
      try { r = await api('PUT', '/_/api/page/' + id, { lines: p.lines, pub: !!p.pub, pin: p.pin || 0, baseRev: p.rev }); }
      catch (e) { keepPending(p); offline = true; dirty.add(id); continue; }
      if (r.ok) {
        const e = r.data.page;
        Object.assign(p, { rev: e.rev, links: e.links, image: e.image, images: e.images, web: e.web, descr: e.descr, css: e.css || '', updated: e.updated, saved: true, fresh: false, titleTaken: false });
        p.title = e.title;
        dropPending(id);
        if (JSON.stringify(p.lines) !== sent) dirty.add(id);
        if (r.data.renamed && r.data.renamed.length) refreshIndex();
        if (S.cur === id) { const u = pageUrl(title(p)); if (location.pathname !== u) history.replaceState(null, '', u); }
        if (S.notice === 'offline') setNotice('');
      } else if (r.status === 409 && r.data.error === 'conflict') { S.conflict = { id, theirs: r.data.page }; renderBanner(); }
      else if (r.status === 409 && r.data.error === 'title_taken') { if (!p.titleTaken) toast('Another page already has this title. Change it to save this page.'); p.titleTaken = true; }
      else if (r.status === 401) { keepPending(p); setNotice('signedout'); }
      else if (r.status >= 500 || r.status === 0) { keepPending(p); offline = true; dirty.add(id); }
      else toast('This page could not be saved (' + (r.data.error || r.status) + ').');
    }
  } finally {
    flushing = false;
    if (offline) { setNotice('offline'); clearTimeout(flushT); flushT = setTimeout(flush, 15000); }
    else if (dirty.size) { clearTimeout(flushT); flushT = setTimeout(flush, 700); }
  }
}
function setNotice(kind) { if (S.notice !== kind) { S.notice = kind; renderBanner(); } }
window.addEventListener('online', () => { if (dirty.size || Object.keys(load(PENDING, {})).length) flush(); });
window.addEventListener('beforeunload', e => {
  if (S.edit >= 0) commit();
  if (!dirty.size) return;
  for (const id of dirty) {
    const p = byId(id); if (!p || !p.lines || !(p.lines[0] || '').trim() || (p.fresh && bodyEmpty(p))) continue;
    keepPending(p);
    try { fetch('/_/api/page/' + id, { method: 'PUT', keepalive: true, credentials: 'same-origin', headers: { 'X-RB': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ lines: p.lines, pub: !!p.pub, pin: p.pin || 0, baseRev: p.rev }) }); } catch (err) {}
  }
});

/* Files and images */

const resolve = resolveFile;
function imgHtml(u, cls) {
  const src = resolve(u);
  if (!src) return `<span class="img-miss">image not found</span>`;
  return `<img class="image ${cls || ''}" src="${esc(src)}" alt="" loading="lazy" data-orig="${esc(u)}">`;
}
function titleImage(p) {
  const u = p.lines ? imagesOf(p.lines)[0] : p.image;
  return u ? resolve(u) : null;
}
function initialIcon(t) {
  const ch = [...String(t)][0] || '?';
  return svgUri(`<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><rect width='40' height='40' rx='8' fill='#eef5fc'/><text x='20' y='27' font-size='20' text-anchor='middle' font-family='sans-serif' fill='#2c6cb3'>${ch.replace(/[<&'"]/g, '')}</text></svg>`);
}

/* Links on a page: page links, tags and icons. Skips code, formulas, URLs, images, other notebooks and [[bold]]. */
const linkCache = new Map();
function linksOf(p) {
  if (!p.lines) return new Set(p.links || []);
  const sig = p.lines.join('\n');
  const c = linkCache.get(p.id);
  if (c && c.sig === sig) return c.set;
  const set = new Set(linksOfLines(p.lines));
  linkCache.set(p.id, { sig, set });
  return set;
}
const hasLink = (p, w) => { const lw = w.toLowerCase(); for (const x of linksOf(p)) if (x.toLowerCase() === lw) return true; return false; };

/* Link heat: count how many visible pages link to each word, then scale to 1-100 on a log curve. */
function computeHeat() {
  const m = new Map();
  for (const p of visPages()) {
    const own = title(p).toLowerCase();
    for (const w of linksOf(p)) { const k = w.toLowerCase(); if (k === own) continue; m.set(k, (m.get(k) || 0) + 1); }
  }
  S.heat = m; S.heatMax = Math.max(1, ...m.values());
}
const countOf = t => S.heat.get(String(t).toLowerCase()) || 0;
const levelOf = t => { const c = countOf(t); return c ? Math.max(1, Math.round(100 * Math.log(1 + c) / Math.log(1 + S.heatMax))) : 0; };

/* Inline notation */
function linkHtml(t, label) {
  const p = byTitle(t);
  if (p && !visible(p)) return `<span class="hid">${esc(label)}</span>`;
  if (!p && S.visitor) return `<span class="hid">${esc(label)}</span>`;
  const lv = levelOf(t), c = countOf(t);
  const tip = `${c} page${c === 1 ? '' : 's'} link here · heat ${lv}/100${p ? (p.pub ? '' : ' · private') : ' · no page yet'}`;
  if (p) return `<a class="page-link${p.pub ? '' : ' priv'}" href="${esc(pageUrl(title(p)))}" data-go="${p.id}" style="--h:${lv}" data-heat="${lv}" title="${esc(tip)}">${esc(label)}</a>`;
  return `<a class="page-link empty-page-link" data-new="${esc(t)}" style="--h:${lv}" data-heat="${lv}" title="${esc(tip)}">${esc(label)}</a>`;
}
const ext = (u, l) => `<a class="ext" href="${esc(u)}" target="_blank" rel="noopener">${esc(l)}</a>`;
function iconHtml(name, n) {
  const p = byTitle(name);
  if (p && !visible(p)) return `<span class="hid">${esc(name)}</span>`;
  const src = (p && titleImage(p)) || initialIcon(name);
  const one = `<img class="icon" src="${esc(src)}" alt="${esc(name)}" title="${esc(name)}">`;
  const body = one.repeat(Math.min(n || 1, 30));
  return p ? `<a class="page-link icon-link" data-go="${p.id}">${body}</a>` : `<a class="page-link icon-link" data-new="${esc(name)}">${body}</a>`;
}
const inl = makeInline({ link: (t, l) => linkHtml(t, l), icon: (n, c) => iconHtml(n, c), img: (u, c) => imgHtml(u, c), ext });
/* Page body, line by line */
function codeBlocks(p, name) {
  if (p.lines) return codeOfLines(p.lines, name);
  return name === 'style.css' && p.css ? [p.css] : [];
}
function bodyHtml(p) {
  let h = '', blk = null, sec = 0;
  const flush = () => {
    if (blk && blk.k === 'code' && /\.html?$/i.test(blk.name) && !(S.edit > blk.start && S.edit <= blk.end)) {
      h += `<div class="html-prev" contenteditable="false"><div class="eb">Preview of ${esc(blk.name)}</div><iframe sandbox="" title="Preview of ${esc(blk.name)}" srcdoc="${esc(blk.body.join('\n'))}"></iframe></div>`;
    }
    blk = null;
  };
  const cssLive = (title(p) === 'settings' || title(p) === ME) && S.css;
  p.lines.forEach((raw, i) => {
    const ind = indOf(raw), t = raw.slice(ind);
    if (blk && (ind <= blk.ind || !raw.trim())) flush();
    let cls = 'line', inner, pad = ind, dot = '';
    if (S.touched.has(p.id + ':' + i)) cls += ' tel';
    if (S.range && i >= S.range[0] && i <= S.range[1]) cls += ' sel';
    if (i === 0) { cls += ' line-title'; inner = raw ? esc(raw) : '<span class="ph">Untitled</span>'; }
    else if (blk) {
      blk.end = i;
      if (blk.k === 'code') { cls += ' code-block'; inner = esc(raw.slice(blk.ind + 1)) || ' '; pad = blk.ind; blk.body.push(raw.slice(blk.ind + 1)); }
      else { cls += ' table-block' + (blk.first ? ' thead' : ''); blk.first = false; pad = blk.ind;
        inner = `<span class="row">${raw.slice(blk.ind + 1).split('\t').map(c => `<span class="cell">${inl(c) || '&nbsp;'}</span>`).join('')}</span>`; }
    }
    else if (t.startsWith('code:')) {
      const name = t.slice(5).trim();
      blk = { k:'code', ind, name, body:[], start:i, end:i };
      cls += ' code-block-start';
      inner = `<span class="name">${esc(name)}</span>${name === 'style.css' && cssLive ? '<span class="applied">Applied</span>' : ''}`;
    }
    else if (t.startsWith('table:')) { blk = { k:'table', ind, first:true, start:i, end:i }; cls += ' table-block-start'; inner = `<span class="name">${esc(t.slice(6))}</span>`; }
    else if (t.startsWith('>')) { cls += ' quote'; inner = `<span class="q">${inl(t.replace(/^>\s?/, '')) || '&nbsp;'}</span>`; }
    else if (/^[$%]\s/.test(t)) { cls += ' cli'; inner = `<span class="p">${t[0]}</span>${esc(t.slice(2))}`; }
    else if (ytLine(t)) {
      const u = ytLine(t), lab = (t.match(/^\[(.+?)\s+https?:/) || t.match(/^\[https?:\S+\s+(.+)\]$/) || [])[1];
      inner = `<a class="ytcard" href="${esc(u)}" target="_blank" rel="noopener">${metaImg(u) ? `<img class="ytthumb" src="${esc(metaImg(u))}" alt="" loading="lazy" referrerpolicy="no-referrer">` : webIcon('youtube', '')}<span><b>${esc(lab || metaTitle(u) || guessTitle(u))}</b><small>YouTube · ${esc(hostOf(u))}</small></span></a>`;
      if (ind > 0) dot = `<span class="dot" style="left:calc(${ind - 1} * 1.5em + .45em)"></span>`;
    }
    else {
      inner = inl(t) || '&nbsp;';
      if (ind === 0 && /^\[\*{2,}\s[^\]]*\]$/.test(t)) { sec++; cls += ' sec'; inner = `<span class="sec-n">${sec}</span>` + inner; }
      if (ind > 0) dot = `<span class="dot" style="left:calc(${ind - 1} * 1.5em + .45em)"></span>`;
    }
    if (i === S.edit) { cls += ' editing'; pad = S.edInd; dot = ''; inner = `<textarea id="ed" rows="1" spellcheck="false" autocapitalize="off" autocomplete="off" aria-label="Line ${i + 1}"></textarea><div id="ac" hidden></div>`; }
    h += `<div class="${cls}" data-i="${i}" style="padding-left:calc(${pad} * 1.5em)">${dot}${inner}</div>`;
  });
  flush();
  return h;
}
/* Redraw only the lines that changed, so an open PDF or a playing video is not reloaded by every click. */
const lineKey = new WeakMap();
const keyOf = el => el.outerHTML.replace(/ data-i="\d+"/, '').replace(/^(<div class="line[^"]*?) tel\b/, '$1');
function markBody() { const b = $('#body'); if (b) { for (const el of b.children) if (!lineKey.has(el)) lineKey.set(el, keyOf(el)); fileSizes(); } }
function setBody(p) {
  const b = $('#body'); if (!b) return;
  const t = document.createElement('template'); t.innerHTML = bodyHtml(p);
  const next = [...t.content.children], old = [...b.children].filter(el => lineKey.has(el) || (el.remove(), false));
  const nk = next.map(keyOf), ok = old.map(el => el.querySelector('#ed') ? '' : lineKey.get(el));
  const same = (i, j) => nk[i] === ok[j] && !next[i].querySelector('#ed');
  const keep = (i, j) => { if (old[j].dataset.i !== undefined) old[j].dataset.i = next[i].dataset.i; if (old[j].className !== next[i].className) old[j].className = next[i].className; };
  const put = (i, before) => { lineKey.set(next[i], nk[i]); b.insertBefore(next[i], before); };
  let a = 0; while (a < next.length && a < old.length && same(a, a)) { keep(a, a); a++; }
  let z = 0; while (z < next.length - a && z < old.length - a && same(next.length - 1 - z, old.length - 1 - z)) { keep(next.length - 1 - z, old.length - 1 - z); z++; }
  const nEnd = next.length - z, oEnd = old.length - z;
  if (nEnd - a === oEnd - a) for (let i = a; i < nEnd; i++) { if (same(i, i)) keep(i, i); else { put(i, old[i]); old[i].remove(); } }
  else { for (let i = a; i < nEnd; i++) put(i, old[a] || null); for (let j = a; j < oEnd; j++) old[j].remove(); }
  fileSizes();
}
/* The size of each file on the page, asked once. */
const sizeOf = new Map();
const fmtSize = n => n >= 1e9 ? (n / 1e9).toFixed(n >= 1e10 ? 0 : 2).replace(/\.?0+$/, '') + ' GB' : n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, '') + ' MB' : n >= 1e3 ? Math.round(n / 1e3) + ' KB' : n + ' B';
function fileSizes() {
  document.querySelectorAll('#body [data-fsize]:empty').forEach(async el => {
    const n = el.dataset.fsize;
    if (!sizeOf.has(n)) sizeOf.set(n, fetch('/files/' + n, { method: 'HEAD', credentials: 'same-origin' }).then(r => r.ok ? +r.headers.get('Content-Length') : -1).catch(() => 0));
    const v = await sizeOf.get(n);
    if (!v) { sizeOf.delete(n); return; }
    if (v < 0) { sizeOf.delete(n); const box = el.closest('.fbox'); if (box) box.classList.add('gone'); el.textContent = ' · not found'; return; }
    el.textContent = ' · ' + fmtSize(v);
  });
}
function applyUserCss() {
  let css = '';
  if (S.css) {
    const set = byTitle('settings'); if (set) css += codeBlocks(set, 'style.css').join('\n');
    const me = byTitle(ME); if (me && !S.visitor) css += '\n' + codeBlocks(me, 'style.css').join('\n');
  }
  const el = $('#usercss'); if (el.textContent !== css) el.textContent = css;
}

/* Related pages, Scrapbox-style: direct links + back links, then 2-hop groups by shared word. */
function related(p) {
  const vis = visPages(), mine = [...linksOf(p)], seen = new Set([p.id]);
  const direct = [];
  for (const w of mine) { const q = byTitle(w); if (q && visible(q) && !seen.has(q.id)) { direct.push(q); seen.add(q.id); } }
  for (const q of vis) if (!seen.has(q.id) && hasLink(q, title(p))) { direct.push(q); seen.add(q.id); }
  const groups = [];
  for (const w of mine) {
    const lp = byTitle(w); if (lp && !visible(lp)) continue;
    const ps = vis.filter(q => !seen.has(q.id) && hasLink(q, w));
    if (ps.length) { groups.push({ w, ps, empty: !lp }); ps.forEach(q => seen.add(q.id)); }
  }
  return { direct, groups };
}

/* Graph of real links. One layout engine, three looks: A dots (Obsidian), B roots (rhizome), C board (Miro). */
const GSTYLES = {
  obsidian: { name: 'A Dots', L: 80, K: 2600, G: .012, gy: 1 },
  rhizome: { name: 'B Roots', L: 95, K: 2600, G: .008, gy: 3 },
  board: { name: 'C Board', L: 190, K: 9000, G: .01, gy: 1.3 },
};
const hash = s => { let h = 7; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h); };
const heatL = lv => S.look === 'ink' ? 56 + lv * .3 : 60 - lv * .42;
function graphData(scope, p) {
  let set;
  if (scope === 'local') { const { direct, groups } = related(p); set = [p, ...direct, ...groups.flatMap(g => g.ps)]; }
  else set = visPages();
  const ids = new Set(set.map(q => q.id));
  const nodes = set.map(q => ({ id: 'p:' + q.id, kind: 'page', t: title(q), pid: q.id, img: titleImage(q), priv: !q.pub, lv: levelOf(title(q)) }));
  const words = new Map();
  if (!S.visitor) set.forEach(q => linksOf(q).forEach(w => {
    if (byTitle(w)) return;
    const k = w.toLowerCase(); if (!words.has(k)) words.set(k, { w, by: new Set() }); words.get(k).by.add(q.id);
  }));
  words.forEach((v, k) => { if (S.gwords && (v.by.size >= 2 || (scope === 'local' && v.by.has(p.id)))) nodes.push({ id: 'w:' + k, kind: 'word', t: v.w, lv: levelOf(v.w) }); });
  const nid = new Set(nodes.map(n => n.id));
  const em = new Map();
  const add = (a, b) => { if (a === b) return; const k = a < b ? a + '|' + b : b + '|' + a; const e = em.get(k) || { a: a < b ? a : b, b: a < b ? b : a, fwd: false, back: false }; if (a < b) e.fwd = true; else e.back = true; em.set(k, e); };
  set.forEach(q => linksOf(q).forEach(w => {
    const lp = byTitle(w);
    if (lp && ids.has(lp.id)) add('p:' + q.id, 'p:' + lp.id);
    else if (nid.has('w:' + w.toLowerCase())) add('p:' + q.id, 'w:' + w.toLowerCase());
  }));
  const edges = [...em.values()];
  const deg = {}; edges.forEach(e => { deg[e.a] = (deg[e.a] || 0) + 1; deg[e.b] = (deg[e.b] || 0) + 1; });
  nodes.forEach(n => n.deg = deg[n.id] || 0);
  return { nodes, edges };
}
const cjkW = s => [...s].reduce((a, c) => a + (c.charCodeAt(0) > 255 ? 2 : 1.1), 0);
function clip(s, max) { if (cjkW(s) <= max) return s; let o = ''; for (const c of s) { if (cjkW(o + c) > max - 2) break; o += c; } return o + '…'; }

function mountGraph(host, data, o) {
  const st = GSTYLES[o.style] || GSTYLES.obsidian, uid = Math.random().toString(36).slice(2, 7);
  const cache = S.gpos[o.key + ':' + o.style] || (S.gpos[o.key + ':' + o.style] = {});
  const N = data.nodes.map(n => ({ ...n }));
  const byN = new Map(N.map(n => [n.id, n]));
  const E = data.edges.filter(e => byN.has(e.a) && byN.has(e.b)).map(e => ({ ...e, A: byN.get(e.a), B: byN.get(e.b) }));
  const adj = new Map(N.map(n => [n.id, new Set()]));
  E.forEach(e => { adj.get(e.a).add(e.b); adj.get(e.b).add(e.a); });
  let cached = 0;
  N.forEach(n => {
    if (o.center && n.id === o.center) { n.x = 0; n.y = 0; n.fixed = true; return; }
    const c = cache[n.id];
    if (c) { n.x = c.x; n.y = c.y; cached++; return; }
    const nb = [...adj.get(n.id)].map(id => cache[id]).find(Boolean);
    const a = (hash(n.id) % 628) / 100, r = nb ? 24 : 60 + (hash(n.id) % 80);
    n.x = (nb ? nb.x : 0) + Math.cos(a) * r; n.y = (nb ? nb.y : 0) + Math.sin(a) * r * (o.style === 'rhizome' ? .4 : 1);
  });
  N.forEach(n => {
    n.vx = 0; n.vy = 0;
    if (o.style === 'board') { n.w = n.kind === 'word' ? Math.max(70, cjkW(n.t) * 6.6 + 28) : 140; n.h = n.kind === 'word' ? 30 : (n.img ? 54 : 40); }
    else if (o.style === 'rhizome') n.r = 2.5 + Math.min(6, Math.sqrt(n.deg) * 1.5) + (n.id === o.center ? 2 : 0);
    else n.r = 4 + Math.min(10, Math.sqrt(n.deg) * 2.3) + (n.id === o.center ? 3 : 0);
  });
  host.innerHTML = `<svg class="gsvg g-${o.style}" role="img" aria-label="Graph of linked pages">
    <defs>
      <pattern id="gdots-${uid}" width="22" height="22" patternUnits="userSpaceOnUse"><rect class="pbg" width="22" height="22"/><circle class="pdot" cx="1" cy="1" r="1"/></pattern>
      <marker id="ah-${uid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="arrowhead" d="M0,0 L10,5 L0,10 z"/></marker>
    </defs>
    <rect class="gbg" width="100%" height="100%" ${o.style === 'board' ? `fill="url(#gdots-${uid})"` : ''}/>
    <g class="vp"><g class="ge"></g><g class="gn"></g></g></svg>
    <div class="gctl"><button data-gz="in" aria-label="Zoom in">+</button><button data-gz="out" aria-label="Zoom out">−</button><button class="wide" data-gz="fit">Fit</button></div>`;
  const svg = host.querySelector('svg'), vp = host.querySelector('.vp'), ge = host.querySelector('.ge'), gn = host.querySelector('.gn');
  const NS = 'http://www.w3.org/2000/svg';
  const mk = (tag, attrs, parent) => { const el = document.createElementNS(NS, tag); for (const k in attrs) el.setAttribute(k, attrs[k]); if (parent) parent.appendChild(el); return el; };
  E.forEach(e => {
    const fresh = o.fresh.has(e.a) || o.fresh.has(e.b);
    e.el = mk('path', { class: 'gedge' + (fresh ? ' grow' : ''), pathLength: 1 }, ge);
    if (o.style === 'rhizome') e.el.setAttribute('stroke-width', (1 + Math.max(e.A.lv, e.B.lv) / 100 * 2.6).toFixed(2));
    if (o.style === 'board') { if (e.fwd) e.el.setAttribute('marker-end', `url(#ah-${uid})`); if (e.back) e.el.setAttribute('marker-start', `url(#ah-${uid})`); }
  });
  N.forEach((n, i) => {
    const g = n.el = mk('g', { class: `gnode ${n.kind}${n.id === o.center ? ' cur' : ''}${n.priv ? ' priv' : ''}${o.fresh.has(n.id) ? ' new' : ''}`, 'data-id': n.id }, gn);
    mk('title', {}, g).textContent = `${n.t}${n.lv ? ` · heat ${n.lv}/100` : ''}${n.kind === 'word' ? ' · no page yet' : ''}`;
    const gs = mk('g', { class: 'gs' }, g);
    const col = n.kind === 'word' ? 'hsl(26 85% 50%)' : n.id === o.center ? 'var(--accent)' : `hsl(212 62% ${heatL(n.lv)}%)`;
    if (o.style === 'board') {
      mk('rect', { class: 'card-r', x: -n.w / 2, y: -n.h / 2, width: n.w, height: n.h, rx: 7 }, gs);
      if (n.kind === 'page') mk('rect', { x: -n.w / 2, y: -n.h / 2 + 7, width: 3, height: n.h - 14, rx: 1.5, fill: col }, gs);
      let tx = -n.w / 2 + 13;
      if (n.img) { const cp = mk('clipPath', { id: `gc-${uid}-${i}` }, gs); mk('rect', { x: -n.w / 2 + 10, y: -16, width: 32, height: 32, rx: 5 }, cp); mk('image', { href: n.img, x: -n.w / 2 + 10, y: -16, width: 32, height: 32, preserveAspectRatio: 'xMidYMid slice', 'clip-path': `url(#gc-${uid}-${i})` }, gs); tx = -n.w / 2 + 50; }
      mk('text', { x: tx, y: 4, 'font-weight': n.id === o.center ? 700 : 500 }, gs).textContent = n.kind === 'word' ? '[' + clip(n.t, 14) + ']' : clip(n.t, n.img ? 13 : 19);
    } else if (o.style === 'rhizome') {
      const h = hash(n.id);
      for (let k = 0; k < 3; k++) {
        const a = ((h >> (k * 4)) % 628) / 100, L = 7 + ((h >> (k * 3)) % 9);
        const x1 = Math.cos(a) * (n.r + L), y1 = Math.sin(a) * (n.r + L), cx = Math.cos(a + .5) * (n.r + L * .6), cy = Math.sin(a + .5) * (n.r + L * .6);
        mk('path', { class: 'rootlet', d: `M0,0 Q${cx.toFixed(1)},${cy.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}` }, gs);
      }
      mk('circle', { r: n.r, fill: n.kind === 'word' ? 'var(--bg)' : col, stroke: n.kind === 'word' ? col : 'none', 'stroke-width': 1.5 }, gs);
      mk('text', { x: n.r + 6, y: 4 }, g).textContent = clip(n.t, 22);
    } else {
      if (n.id === o.center) mk('circle', { class: 'ring', r: n.r + 4 }, gs);
      if (n.img && n.kind === 'page') {
        const cp = mk('clipPath', { id: `gc-${uid}-${i}` }, gs); mk('circle', { r: n.r }, cp);
        mk('circle', { class: 'dotc', r: n.r, fill: col }, gs);
        mk('image', { href: n.img, x: -n.r, y: -n.r, width: n.r * 2, height: n.r * 2, preserveAspectRatio: 'xMidYMid slice', 'clip-path': `url(#gc-${uid}-${i})` }, gs);
      } else mk('circle', { class: 'dotc', r: n.r, fill: col }, gs);
      mk('text', { y: n.r + 14, 'text-anchor': 'middle' }, g).textContent = clip(n.t, 20);
    }
  });

  /* Camera eases toward a target, so zoom, Fit and pan all glide instead of jumping. */
  const cam = { z: 1, x: 0, y: 0 }, tgt = { z: 1, x: 0, y: 0 }, vel = { x: 0, y: 0 };
  let alpha = cached >= N.length - 1 ? .3 : 1, raf = 0, running = false, alive = true, userView = false, drag = null, pan = null;
  const W = () => host.clientWidth, H = () => host.clientHeight;
  function edgePath(e) {
    const a = e.A, b = e.B, dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, nx = -dy / len, ny = dx / len;
    if (o.style === 'obsidian') return `M${a.x.toFixed(1)},${a.y.toFixed(1)} L${b.x.toFixed(1)},${b.y.toFixed(1)}`;
    if (o.style === 'rhizome') {
      const h = hash(e.a + e.b), cap = Math.min(.25 * len, 45), w1 = ((h % 50) / 50 - .5) * 2 * cap, w2 = (((h >> 5) % 50) / 50 - .5) * 2 * cap;
      return `M${a.x.toFixed(1)},${a.y.toFixed(1)} C${(a.x + dx * .3 + nx * w1).toFixed(1)},${(a.y + dy * .3 + ny * w1).toFixed(1)} ${(a.x + dx * .7 + nx * w2).toFixed(1)},${(a.y + dy * .7 + ny * w2).toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`;
    }
    const pt = (n, sx, sy) => { const t = Math.min((n.w / 2 + 2) / Math.abs(sx || 1e-6), (n.h / 2 + 2) / Math.abs(sy || 1e-6)); return [n.x + sx * t, n.y + sy * t]; };
    const [x1, y1] = pt(a, dx, dy), [x2, y2] = pt(b, -dx, -dy);
    const horiz = Math.abs(dx) * .6 > Math.abs(dy), k = horiz ? (x2 - x1) * .45 : (y2 - y1) * .45;
    return horiz ? `M${x1.toFixed(1)},${y1.toFixed(1)} C${(x1 + k).toFixed(1)},${y1.toFixed(1)} ${(x2 - k).toFixed(1)},${y2.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`
                 : `M${x1.toFixed(1)},${y1.toFixed(1)} C${x1.toFixed(1)},${(y1 + k).toFixed(1)} ${x2.toFixed(1)},${(y2 - k).toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
  }
  function draw() {
    vp.setAttribute('transform', `translate(${(W() / 2 + cam.x).toFixed(2)},${(H() / 2 + cam.y).toFixed(2)}) scale(${cam.z.toFixed(4)})`);
    /* Labels keep about the same size on screen, and step back when zoomed far out, as in Obsidian. */
    if (o.style !== 'board') { svg.style.setProperty('--tz', Math.max(.75, Math.min(1.7, 1 / cam.z)).toFixed(3)); svg.classList.toggle('far', cam.z < .5); }
    E.forEach(e => e.el.setAttribute('d', edgePath(e)));
    N.forEach(n => n.el.setAttribute('transform', `translate(${n.x.toFixed(2)},${n.y.toFixed(2)})`));
  }
  function fitTarget() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    N.forEach(n => {
      let l, r, t, b;
      if (o.style === 'board') { l = r = n.w / 2; t = b = n.h / 2; }
      else if (o.style === 'rhizome') { l = n.r + 16; r = n.r + 10 + cjkW(clip(n.t, 22)) * 6; t = n.r + 16; b = n.r + 16; }
      else { const lw = cjkW(clip(n.t, 20)) * 3.1; l = r = Math.max(n.r + 4, lw); t = n.r + 6; b = n.r + 20; }
      x0 = Math.min(x0, n.x - l); x1 = Math.max(x1, n.x + r); y0 = Math.min(y0, n.y - t); y1 = Math.max(y1, n.y + b);
    });
    tgt.z = Math.max(.25, Math.min(1.05, (W() - 24) / (x1 - x0 || 1), (H() - 48) / (y1 - y0 || 1)));
    tgt.x = -(x0 + x1) / 2 * tgt.z; tgt.y = -(y0 + y1) / 2 * tgt.z - 12;
  }
  function tick() {
    const n = N.length;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const a = N[i], b = N[j]; let dx = b.x - a.x, dy = b.y - a.y; let d2 = dx * dx + dy * dy;
      if (d2 < .01) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = .5; }
      if (d2 < 360000) { const d = Math.sqrt(d2), f = st.K / d2 * alpha; const fx = dx / d * f, fy = dy / d * f; a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy; }
    }
    E.forEach(e => { const a = e.A, b = e.B, dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1, f = (d - st.L) / d * .06 * alpha; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f; });
    N.forEach(nd => {
      if (nd.fixed) { nd.vx = nd.vy = 0; return; }
      nd.vx -= nd.x * st.G * alpha; nd.vy -= nd.y * st.G * st.gy * alpha;
      nd.vx *= .6; nd.vy *= .6; nd.x += nd.vx; nd.y += nd.vy;
    });
    if (o.style === 'board') for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const a = N[i], b = N[j], dx = b.x - a.x, dy = b.y - a.y;
      const ox = (a.w + b.w) / 2 + 14 - Math.abs(dx), oy = (a.h + b.h) / 2 + 14 - Math.abs(dy);
      if (ox <= 0 || oy <= 0) continue;
      const wa = a.fixed ? 0 : (b.fixed ? 1 : .5), wb = 1 - wa;
      if (ox < oy) { const m = Math.sign(dx || 1) * ox * .5; a.x -= m * wa; b.x += m * wb; } else { const m = Math.sign(dy || 1) * oy * .5; a.y -= m * wa; b.y += m * wb; }
    }
    alpha *= .985;
  }
  function frame() {
    if (!alive) return;
    let busy = false;
    if (alpha > .012 || drag) { tick(); tick(); busy = true; if (!userView && !drag) fitTarget(); }
    if (!pan && (Math.abs(vel.x) > .05 || Math.abs(vel.y) > .05)) { tgt.x += vel.x; tgt.y += vel.y; vel.x *= .88; vel.y *= .88; busy = true; }
    const k = drag || pan ? 1 : .18;
    for (const p of ['z', 'x', 'y']) { const d = tgt[p] - cam[p]; if (Math.abs(d) > (p === 'z' ? .0005 : .05)) { cam[p] += d * k; busy = true; } else cam[p] = tgt[p]; }
    draw();
    if (busy) raf = requestAnimationFrame(frame);
    else { running = false; N.forEach(n => cache[n.id] = { x: n.x, y: n.y }); }
  }
  const wake = () => { if (!running && alive) { running = true; raf = requestAnimationFrame(frame); } };
  const local = e => { const r = svg.getBoundingClientRect(); return { x: e.clientX - r.left - W() / 2, y: e.clientY - r.top - H() / 2 }; };
  const toWorld = e => { const l = local(e); return { x: (l.x - cam.x) / cam.z, y: (l.y - cam.y) / cam.z }; };
  svg.addEventListener('pointerdown', e => {
    if (e.button && e.button !== 0) return;
    const g = e.target.closest('.gnode');
    vel.x = vel.y = 0;
    if (g) { const n = byN.get(g.dataset.id); drag = { n, sx: e.clientX, sy: e.clientY, moved: 0, wasFixed: n.fixed }; n.fixed = true; g.classList.add('drag'); }
    else { pan = { lx: e.clientX, ly: e.clientY, t: performance.now() }; svg.classList.add('panning'); }
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener('pointermove', e => {
    if (drag) {
      drag.moved = Math.max(drag.moved, Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy));
      if (drag.moved > 4) { const w = toWorld(e); drag.n.x = w.x; drag.n.y = w.y; userView = true; alpha = Math.max(alpha, .35); wake(); }
    } else if (pan) {
      const dx = e.clientX - pan.lx, dy = e.clientY - pan.ly, now = performance.now(), dt = Math.max(8, now - pan.t);
      pan.lx = e.clientX; pan.ly = e.clientY; pan.t = now;
      cam.x += dx; cam.y += dy; tgt.x = cam.x; tgt.y = cam.y;
      vel.x = vel.x * .4 + dx * 16 / dt * .6; vel.y = vel.y * .4 + dy * 16 / dt * .6;
      userView = true; draw();
    }
  });
  const end = () => {
    svg.classList.remove('panning');
    if (drag) {
      const d = drag; drag = null; d.n.el.classList.remove('drag');
      if (d.moved <= 4) { d.n.fixed = d.wasFixed; o.onOpen(d.n); return; }
      if (!d.wasFixed) d.n.fixed = false;
      cache[d.n.id] = { x: d.n.x, y: d.n.y }; wake();
    }
    if (pan) { pan = null; vel.x *= .5; vel.y *= .5; wake(); }
  };
  svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
  const zoomAt = (f, cx, cy) => {
    const nz = Math.max(.2, Math.min(3, tgt.z * f));
    tgt.x = cx - (cx - tgt.x) * nz / tgt.z; tgt.y = cy - (cy - tgt.y) * nz / tgt.z; tgt.z = nz;
    userView = true; wake();
  };
  svg.addEventListener('wheel', e => {
    if (!o.full && !e.ctrlKey && !e.metaKey) return;
    e.preventDefault(); const l = local(e);
    zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? .01 : .0018)), l.x, l.y);
  }, { passive: false });
  svg.addEventListener('pointerover', e => {
    const g = e.target.closest('.gnode'); if (!g || pan) return;
    const id = g.dataset.id, nb = adj.get(id);
    svg.classList.add('hl');
    N.forEach(n => n.el.classList.toggle('on', n.id === id || nb.has(n.id)));
    E.forEach(ed => ed.el.classList.toggle('on', ed.a === id || ed.b === id));
  });
  svg.addEventListener('pointerout', e => { if (e.target.closest('.gnode') && !drag) svg.classList.remove('hl'); });
  host.querySelector('.gctl').addEventListener('click', e => {
    const b = e.target.closest('[data-gz]'); if (!b) return;
    if (b.dataset.gz === 'fit') { userView = false; fitTarget(); wake(); } else zoomAt(b.dataset.gz === 'in' ? 1.3 : 1 / 1.3, 0, 0);
  });
  /* Lay out fully before the first paint, so the graph opens settled. Changes after that glide. */
  if (alpha === 1) { for (let k = 0; k < 400; k++) tick(); alpha = .01; N.forEach(n => cache[n.id] = { x: n.x, y: n.y }); }
  fitTarget(); Object.assign(cam, tgt); draw();
  if (alpha > .012) wake();
  return {
    destroy() { alive = false; cancelAnimationFrame(raf); N.forEach(n => cache[n.id] = { x: n.x, y: n.y }); },
    resize() { if (!userView) { fitTarget(); Object.assign(cam, tgt); } draw(); }
  };
}
function openNode(n) {
  if (n.kind === 'page') go(n.pid);
  else if (!S.visitor) newPage(n.t);
}
const VIEW_NAMES = { cards: 'Cards', obsidian: 'Dots', rhizome: 'Roots', board: 'Board' };
const gstyleSeg = withCards => `<div class="seg sm" role="group" aria-label="How to show connections">${Object.keys(VIEW_NAMES).filter(k => withCards || k !== 'cards').map(k => `<button data-gstyle="${k}" aria-pressed="${(withCards ? S.gstyle : graphStyle()) === k}">${VIEW_NAMES[k]}</button>`).join('')}</div>`;
const graphStyle = () => S.gstyle === 'cards' ? 'obsidian' : S.gstyle;

function strayFor(p) {
  const vis = visPages().filter(q => (!p || q.id !== p.id) && (q.lines ? q.lines.length > 1 : !!q.descr));
  let pool = vis;
  if (p) { const { direct, groups } = related(p); const near = new Set([...direct.map(q => q.id), ...groups.flatMap(g => g.ps.map(q => q.id))]); pool = vis.filter(q => !near.has(q.id)); }
  if (!pool.length) pool = vis;
  pool = [...pool].sort((a, b) => a.updated - b.updated).slice(0, 5);
  const key = p ? p.id : 'home';
  let pick = pool.find(q => q.id === S.stray[key]);
  if (!pick) { pick = pool[Math.floor(Math.random() * pool.length)]; S.stray[key] = pick && pick.id; }
  return pick;
}
/* Readable text of a page, without code and table blocks */
function textLines(p) { return p.lines ? textOfLines(p.lines) : (p.descr ? [p.descr] : []); }
function strayLine(q) {
  const ls = textLines(q).filter(l => l.length > 6 && !l.startsWith('#'));
  return ls.length ? ls[(q.id.length * 7) % ls.length] : '';
}
function strayHtml(p) {
  const q = strayFor(p);
  if (!q) return '';
  return `<section class="stray" aria-label="A stray page">
    <div class="eb">A stray page arrived</div>
    <div class="row" data-go="${q.id}" role="link" tabindex="0" aria-label="Open ${esc(title(q))}">
      <svg class="sv" viewBox="0 0 70 44" aria-hidden="true"><path d="M2 36 C16 44 22 10 40 22 S56 26 58 22" fill="none" stroke="#999" stroke-width="1.4" stroke-dasharray="3 4" stroke-linecap="round"/><circle cx="60" cy="21" r="7" fill="#6ba6de"/></svg>
      <div class="tt"><b>${esc(title(q))}</b><p class="quote">${esc(strayLine(q))}</p></div>
    </div>
    <div class="acts">
      ${p && !S.visitor ? `<button class="btn sm primary" data-fit="${q.id}">Connect it here</button>` : ''}
      <button class="btn sm" data-go="${q.id}">Open</button>
      <button class="btn sm" data-another="${p ? p.id : 'home'}">Another</button>
    </div>
  </section>`;
}

/* Views */
const tabsHtml = which => `<nav class="tabs" aria-label="Views">${[['home', 'Pages'], ['links', 'Links'], ['web', 'Web links'], ['images', 'Images'], ['graph', 'Graph'], ...(OWNER && !S.visitor ? [['storage', 'Storage']] : [])].map(([k, l]) => `<a href="${k === 'home' ? '/' : '/_/' + k}" ${which === k ? 'aria-current="page"' : ''}>${l}</a>`).join('')}</nav>`;
/* On a phone the tabs scroll sideways: bring the current one into view. */
const showTab = () => { const c = $('.tabs a[aria-current]'); if (c) { const t = c.parentNode; t.scrollLeft = Math.max(0, c.getBoundingClientRect().right + t.scrollLeft - t.getBoundingClientRect().right); } };
const dropGraph = () => { if (S.graph) { S.graph.destroy(); S.graph = null; } S.graphSig = ''; };
function renderHome() {
  dropGraph();
  S.cur = null; S.edit = -1; S.range = null;
  const all = visPages().sort((a, b) => ((b.pin ? 1 : 0) - (a.pin ? 1 : 0)) || (a.pin && b.pin ? a.pin - b.pin : b.updated - a.updated));
  const q = S.q.trim().toLowerCase();
  const list = q ? all.filter(p => matchQ(p, q)) : all;
  const pub = pages.filter(p => p.pub).length;
  $('#main').innerHTML = `
    ${q ? '' : `<div class="home-stray">${strayHtml(null)}</div>`}
    ${tabsHtml('home')}
    <div class="home-head"><span>${q ? `Results for “${esc(S.q.trim())}”` : 'Newest first'}</span>
      <span>${S.visitor ? `${list.length} public pages` : `${list.length} pages · ${pub} public · ${pages.length - pub} private`}</span></div>
    ${list.length ? `<ul class="grid">${list.map(p => { const im = titleImage(p); const lv = levelOf(title(p)); return `<li class="${p.pin ? 'pinned' : ''}"><div class="card${p.pub ? '' : ' priv'}${p.pin ? ' pin' : ''}" data-go="${p.id}" role="link" tabindex="0" style="--h:${lv}" title="${countOf(title(p))} pages link here · heat ${lv}/100">
      <div class="t">${esc(title(p))}</div>${im ? `<div class="thumb"><img src="${esc(im)}" alt=""></div>` : `<div class="s">${esc(textLines(p).slice(0, 5).join(' '))}</div>`}
      ${!S.visitor && !p.pub ? '<span class="tag">Private</span>' : ''}</div></li>`; }).join('')}</ul>`
      : `<p class="empty-note">No page has “${esc(S.q.trim())}”. Press Enter to make a page with this title.</p>`}`;
}
function renderLinks() {
  dropGraph();
  S.cur = null; S.edit = -1; S.range = null;
  const words = new Map();
  for (const p of visPages()) for (const w of linksOf(p)) { const k = w.toLowerCase(); if (!words.has(k)) words.set(k, w); }
  let rows = [...words.values()].map(w => { const p = byTitle(w); return { w, p, c: countOf(w), lv: levelOf(w) }; })
    .filter(r => !(r.p && !visible(r.p)) && !(S.visitor && !r.p));
  const lq = S.linkQ.trim().toLowerCase();
  if (lq) rows = rows.filter(r => r.w.toLowerCase().includes(lq));
  if (S.linkSort === 'az') rows.sort((a, b) => a.w.localeCompare(b.w, 'ja'));
  else if (S.linkSort === 'new') rows = rows.filter(r => !r.p).sort((a, b) => b.c - a.c);
  else rows.sort((a, b) => b.c - a.c || a.w.localeCompare(b.w, 'ja'));
  const emptyN = [...words.values()].filter(w => !byTitle(w)).length;
  $('#main').innerHTML = `
    ${tabsHtml('links')}
    <div class="links-tools">
      <div class="seg" role="group" aria-label="Sort links">
        <button data-sort="count" aria-pressed="${S.linkSort === 'count'}">Most linked</button>
        <button data-sort="az" aria-pressed="${S.linkSort === 'az'}">A–Z</button>
        ${S.visitor ? '' : `<button data-sort="new" aria-pressed="${S.linkSort === 'new'}">No page yet (${emptyN})</button>`}
      </div>
      <label class="search" style="max-width:240px"><input id="lq" type="search" placeholder="Filter links" aria-label="Filter links" value="${esc(S.linkQ)}"></label>
      <span class="legend">1<i></i>100</span>
    </div>
    <ul class="lrows">${rows.map(r => `<li class="lrow" style="--h:${r.lv}">
      <span class="w">${r.p ? `<a class="page-link${r.p.pub ? '' : ' priv'}" data-go="${r.p.id}" style="--h:${r.lv}">${esc(r.w)}</a>` : `<a class="page-link empty-page-link" data-new="${esc(r.w)}" style="--h:${r.lv}">${esc(r.w)}</a><span class="chip">No page yet</span>`}${r.p && !r.p.pub ? '<span class="chip">Private</span>' : ''}</span>
      <span class="bar" title="Heat ${r.lv}/100"><i></i></span>
      <span class="n">${r.c} page${r.c === 1 ? '' : 's'}</span></li>`).join('') || '<li class="empty-note">No links match.</li>'}</ul>`;
  document.querySelectorAll('[data-sort]').forEach(b => b.onclick = () => { S.linkSort = b.dataset.sort; renderLinks(); });
  const li = $('#lq'); li.oninput = () => { S.linkQ = li.value; const pos = li.selectionStart; renderLinks(); const n = $('#lq'); n.focus(); n.setSelectionRange(pos, pos); };
}
function renderGraph() {
  S.cur = null; S.edit = -1; S.range = null;
  $('#main').innerHTML = `
    ${tabsHtml('graph')}
    <div class="gtools">${gstyleSeg(false)}
      ${S.visitor ? '' : `<label><input type="checkbox" id="gwords" ${S.gwords ? 'checked' : ''}> Words without a page</label>`}
      <span>Drag a dot to move it. Drag the background to pan. Scroll to zoom. Click to open.</span></div>
    <div class="gbox gfull" id="graph"></div>`;
  mountMain();
  const gw = $('#gwords'); if (gw) gw.onchange = () => { S.gwords = gw.checked; store(KEY + '-gwords', S.gwords); renderGraph(); };
}
function mountMain() {
  if (S.graph) S.graph.destroy();
  computeHeat();
  const box = $('#graph'); box.classList.remove('swap'); void box.offsetWidth; box.classList.add('swap');
  S.graph = mountGraph(box, graphData('all'), { key: 'all', style: graphStyle(), full: true, fresh: new Set(), onOpen: openNode });
}
/* One page card, Scrapbox style: title, then the page picture or its first lines. */
function cardHtml(p, i) {
  const im = titleImage(p), lv = levelOf(title(p));
  return `<li class="rin" style="--i:${i}"><div class="card${p.pub ? '' : ' priv'}${p.pin ? ' pin' : ''}" data-go="${p.id}" role="link" tabindex="0" style="--h:${lv}" title="${countOf(title(p))} pages link here · heat ${lv}/100">
    <div class="t">${esc(title(p))}</div>${im ? `<div class="thumb"><img src="${esc(im)}" alt=""></div>` : `<div class="s">${esc(textLines(p).slice(0, 5).join(' '))}</div>`}
    ${!S.visitor && !p.pub ? '<span class="tag">Private</span>' : ''}</div></li>`;
}
function renderPage(p) {
  S.cur = p.id;
  $('#main').innerHTML = `
    <div class="crumb"><a data-home>← All pages</a></div>
    <article class="page${title(p) === ME ? ' profile' : ''}">
      <div class="ph-bar">
        ${S.visitor ? '' : `<button class="pinbtn" id="pinBtn" type="button" aria-pressed="${!!p.pin}"><svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M15 3l6 6-3 1-4 4 1 5-2 2-4-4-5 5-1-1 5-5-4-4 2-2 5 1 4-4z"/></svg><span>${p.pin ? 'Pinned' : 'Pin'}</span></button><button class="vis" id="vis" role="switch" aria-checked="${p.pub}"><span class="knob"></span><b>${p.pub ? 'Public' : 'Private'}</b></button><span class="pagetools" id="pagetools"><button id="copyBtn" type="button">Copy text</button><button id="histBtn" type="button">History</button><button id="delBtn" class="danger" type="button">Delete</button></span>`}
        <span>Edited ${new Date(p.updated).toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' })}</span>
      </div>
      ${title(p) === ME ? profileHero(p) : ''}
      <div id="body">${bodyHtml(p)}</div>
      ${S.visitor ? '' : `<div class="addfile"><button class="btn sm" id="addFile" type="button">Add file</button><input id="filePick" type="file" multiple hidden aria-label="Choose files"></div>`}
      <div class="pfoot" id="pfoot">${pfootHtml(p)}</div>
    </article>
    <section class="conn" aria-label="Connections">
      <div class="conn-head"><h2>Connections</h2>${gstyleSeg(true)}</div>
      <p class="hint" id="relHint"></p>
      <div id="connBody"></div>
    </section>
    <div class="side">
      <div id="newLinks"></div>
      ${strayHtml(p)}
    </div>`;
  markBody();
  renderRelated(p);
  const af = $('#addFile'); if (af) { af.onclick = () => $('#filePick').click(); $('#filePick').onchange = e => { addFiles([...e.target.files], p); e.target.value = ''; }; }
  const v = $('#vis'); if (v) v.onclick = () => toggleVis(p);
  const pb = $('#pinBtn'); if (pb) pb.onclick = () => togglePin(p);
  const cb = $('#copyBtn'); if (cb) cb.onclick = () => copyText(p);
  const hb = $('#histBtn'); if (hb) hb.onclick = () => openHistory(p);
  const db = $('#delBtn'); if (db) db.onclick = () => askDelete(p);
  if (S.edit >= 0) mountEditor(p);
  if (S.range) drawRangeBar();
}
function renderRelated(p) {
  if (p.lines) ensureMeta(webOfLines(p.lines).map(w => w.url), () => { if (S.cur === p.id && S.edit < 0) rerenderBody(); });
  const host = $('#connBody'); if (!host || !S.cur) return;
  computeHeat();
  const { direct, groups } = related(p);
  const data = graphData('local', p);
  const ids = new Set(data.nodes.map(n => n.id));
  const prev = S.prevNodes[p.id];
  const freshIds = new Set(prev ? [...ids].filter(k => !prev.has(k)) : []);
  S.prevNodes[p.id] = ids;
  const hop = groups.reduce((a, g) => a + g.ps.length, 0);
  const total = direct.length + hop;
  const view = S.gstyle;
  $('#relHint').textContent = !total ? 'Link a word with [ ] and related pages show up here.'
    : view === 'cards' ? `${direct.length} linked here, then pages grouped by the words they share.`
    : `${direct.length} linked here, ${hop} more through shared words. Bigger, darker dots have more links.`;
  const sig = view + '|' + [...ids].sort().join() + '#' + data.edges.map(e => e.a + e.b).sort().join() + '#' + groups.map(g => g.w).join();
  if (S.graphSig !== sig || !host.firstChild) {
    if (S.graph) { S.graph.destroy(); S.graph = null; }
    if (view === 'cards') {
      let i = 0;
      const label = (w, n, cls, attr) => `<li class="rin" style="--i:${i++}"><div class="rlabel ${cls}" ${attr}><b>${esc(w)}</b><small>${n} page${n === 1 ? '' : 's'}</small></div></li>`;
      let h = '';
      if (direct.length) { h += label('Links', direct.length, 'links', ''); direct.forEach(q => h += cardHtml(q, i++)); }
      groups.forEach(g => {
        const lp = byTitle(g.w);
        h += label(g.w, g.ps.length, lp ? '' : 'empty', lp ? `data-go="${lp.id}" role="link" tabindex="0"` : (S.visitor ? '' : `data-new="${esc(g.w)}" role="link" tabindex="0"`));
        g.ps.forEach(q => h += cardHtml(q, i++));
      });
      host.innerHTML = h ? `<ul class="grid rgrid">${h}</ul>` : '';
    } else {
      host.innerHTML = `<div class="gbox glocal swap" id="graph"></div><a class="gopen" href="#graph">Open the whole graph</a>`;
      S.graph = mountGraph($('#graph'), data, { key: 'p:' + p.id, center: 'p:' + p.id, style: view, full: false, fresh: freshIds, onOpen: openNode });
    }
    S.graphSig = sig;
  }
  if (freshIds.size) { const f = data.nodes.find(n => freshIds.has(n.id)); if (f) toast(`Connected: ${f.t}`); }
  updateFoot();
  const nl = $('#newLinks');
  if (nl) {
    const empties = S.visitor ? [] : [...linksOf(p)].filter(w => !byTitle(w));
    nl.innerHTML = empties.length ? `<h2 class="eb" style="margin:0 0 6px">New links</h2><div class="newlinks">${empties.map(w => linkHtml(w, w)).join('')}</div>` : '';
  }
}
function rerenderBody() {
  const p = byId(S.cur); if (!p) return;
  computeHeat(); applyUserCss();
  setBody(p);
  if (S.edit >= 0) mountEditor(p);
  if (S.range) drawRangeBar();
}

/* Undo and redo, per page */
function snapshot(p) {
  const st = S.undo[p.id] || (S.undo[p.id] = []);
  const cur = JSON.stringify(p.lines);
  if (st[st.length - 1] !== cur) { st.push(cur); if (st.length > 200) st.shift(); }
  S.redo[p.id] = [];
}
function undoRedo(p, back) {
  commit();
  const from = back ? (S.undo[p.id] || []) : (S.redo[p.id] || []);
  const to = back ? (S.redo[p.id] || (S.redo[p.id] = [])) : (S.undo[p.id] || (S.undo[p.id] = []));
  const cur = JSON.stringify(p.lines);
  let prev;
  while (from.length && (prev = from.pop()) === cur) prev = undefined;
  if (prev === undefined) return toast(back ? 'Nothing to undo.' : 'Nothing to redo.');
  to.push(cur);
  p.lines = JSON.parse(prev); p.updated = Date.now(); save();
  S.edit = -1; S.range = null; hideSel(); rerenderBody(); renderRelated(p, false);
}

/* Editing */
function startEdit(i, caret) {
  const p = byId(S.cur); if (!p || S.visitor) return;
  if (S.edit >= 0) commit();
  S.range = null; hideSel();
  snapshot(p);
  S.edit = i;
  S.edInd = i === 0 ? 0 : indOf(p.lines[i]);
  setBody(p);
  mountEditor(p, caret);
}
function mountEditor(p, caret) {
  const ta = $('#ed'); if (!ta) return;
  ta.value = p.lines[S.edit].slice(S.edInd);
  autosize(ta); ta.focus({ preventScroll: true });
  const c = caret == null ? ta.value.length : Math.max(0, Math.min(caret, ta.value.length));
  ta.setSelectionRange(c, c);
  ta.addEventListener('input', onInput);
  ta.addEventListener('keydown', onKey);
  ta.addEventListener('paste', onPaste);
  ['select', 'mouseup', 'keyup'].forEach(ev => ta.addEventListener(ev, () => setTimeout(() => checkSel(ta), 0)));
  ta.addEventListener('blur', () => setTimeout(() => {
    if (document.activeElement && document.activeElement.closest && document.activeElement.closest('.selpop')) return;
    if (document.activeElement !== ta && S.edit >= 0 && $('#ed') === ta) stopEdit();
  }, 160));
}
function autosize(ta) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }
function commit() {
  const p = byId(S.cur), ta = $('#ed'); if (!p || !ta || S.edit < 0) return;
  const v = ' '.repeat(S.edInd) + ta.value;
  if (p.lines[S.edit] !== v) { p.lines[S.edit] = v; p.updated = Date.now(); S.touched.add(p.id + ':' + S.edit); save(); }
}
function stopEdit() {
  commit(); S.last = { id: S.cur, line: S.edit }; S.edit = -1; hideAc(); hideSel();
  const p = byId(S.cur); if (p) { rerenderBody(); renderRelated(p, false); }
}
function onInput(e) {
  const ta = e.target, p = byId(S.cur);
  autosize(ta);
  const before = linksOf(p).size;
  commit();
  if (linksOf(p).size !== before || S.edit === 0) renderRelated(p, false);
  if (title(p) === 'settings' || title(p) === ME) applyUserCss();
  checkAc(ta); checkSel(ta);
}
function relayout(p, i, caret) { S.edit = -1; startEdit(i, caret); }
function blockEnd(p, i) { const base = indOf(p.lines[i]); let j = i + 1; while (j < p.lines.length && p.lines[j].trim() && indOf(p.lines[j]) > base) j++; return j - 1; }
function moveLines(p, a, b, dir, block) {
  if (dir < 0 && a <= 1) return null;
  if (dir > 0 && b >= p.lines.length - 1) return null;
  snapshot(p);
  const chunk = p.lines.splice(a, b - a + 1);
  let at;
  if (dir < 0) at = a - 1;
  else at = (block ? blockEnd(p, a) : a) + 1;
  p.lines.splice(at, 0, ...chunk);
  for (let k = 0; k < chunk.length; k++) S.touched.add(p.id + ':' + (at + k));
  p.updated = Date.now(); save();
  return at;
}
function insertAtCaret(ta, text) {
  const s = ta.selectionStart, e = ta.selectionEnd;
  ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
  const c = s + text.length; ta.setSelectionRange(c, c);
  ta.dispatchEvent(new Event('input'));
}
function onKey(e) {
  const ta = e.target, p = byId(S.cur), i = S.edit;
  if (e.isComposing || e.keyCode === 229) return;
  const mod = e.metaKey || e.ctrlKey;
  if (S.ac && !$('#ac').hidden) {
    const n = S.ac.list.length;
    if (e.key === 'ArrowDown' || (e.key === 'Tab' && !e.shiftKey)) { e.preventDefault(); S.ac.sel = (S.ac.sel + 1) % n; drawAc(); return; }
    if (e.key === 'ArrowUp' || (e.key === 'Tab' && e.shiftKey)) { e.preventDefault(); S.ac.sel = (S.ac.sel - 1 + n) % n; drawAc(); return; }
    if (e.key === 'Enter') { e.preventDefault(); pickAc(S.ac.list[S.ac.sel], false); return; }
    if (e.ctrlKey && e.key.toLowerCase() === 'i') { e.preventDefault(); pickAc(S.ac.list[S.ac.sel], true); return; }
    if (e.key === 'Escape') { e.preventDefault(); hideAc(); return; }
  }
  const pos = ta.selectionStart, end = ta.selectionEnd;
  /* Selection + key wraps it, as in Scrapbox */
  if (pos !== end && !mod && !e.altKey) {
    const wraps = { '[': ['[', ']'], '*': ['[* ', ']'], '/': ['[/ ', ']'], '-': ['[- ', ']'], '!': ['[! ', ']'], '%': ['[% ', ']'], '|': ['[| ', ']'], '~': ['[~ ', ']'] };
    if (wraps[e.key]) { e.preventDefault(); wrapSel(ta, ...wraps[e.key]); return; }
  }
  if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); undoRedo(p, !e.shiftKey); return; }
  if (mod && e.key.toLowerCase() === 'g') { e.preventDefault(); stopEdit(); $('#q').focus(); return; }
  if (e.ctrlKey && e.key.toLowerCase() === 'i') {
    e.preventDefault();
    const before = ta.value.slice(0, pos), re = new RegExp('\\[' + ME + '\\.icon(?:\\*(\\d+))?\\]$');
    const m = before.match(re);
    if (m && pos === end) { const n = +(m[1] || 1) + 1; ta.value = before.slice(0, -m[0].length) + `[${ME}.icon*${n}]` + ta.value.slice(pos); const c = pos - m[0].length + `[${ME}.icon*${n}]`.length; ta.setSelectionRange(c, c); ta.dispatchEvent(new Event('input')); }
    else insertAtCaret(ta, `[${ME}.icon]`);
    return;
  }
  if (e.ctrlKey && e.key.toLowerCase() === 't') {
    e.preventDefault();
    const d = new Date(), z = n => String(n).padStart(2, '0');
    const fm = [`${d.getFullYear()}/${z(d.getMonth() + 1)}/${z(d.getDate())}`, `${d.getFullYear()}/${z(d.getMonth() + 1)}/${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`, `${z(d.getHours())}:${z(d.getMinutes())}`];
    const before = ta.value.slice(0, pos);
    if (S.ctT && before.endsWith(fm[S.ctT.k]) && S.ctT.at === pos) {
      const k = (S.ctT.k + 1) % fm.length, base = pos - fm[S.ctT.k].length;
      ta.value = ta.value.slice(0, base) + fm[k] + ta.value.slice(pos);
      const c = base + fm[k].length; ta.setSelectionRange(c, c); S.ctT = { k, at: c }; ta.dispatchEvent(new Event('input'));
    } else { insertAtCaret(ta, fm[0]); S.ctT = { k: 0, at: ta.selectionStart }; }
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault(); commit(); snapshot(p);
    const v = ta.value, ind = i === 0 ? 0 : S.edInd;
    p.lines[i] = ' '.repeat(S.edInd) + v.slice(0, pos);
    p.lines.splice(i + 1, 0, ' '.repeat(ind) + v.slice(end));
    S.touched.add(p.id + ':' + (i + 1)); p.updated = Date.now(); save();
    relayout(p, i + 1, 0); return;
  }
  const indent = d => { commit(); snapshot(p); S.edInd = Math.max(0, S.edInd + d); p.lines[i] = ' '.repeat(S.edInd) + ta.value; save(); relayout(p, i, pos); };
  if (i > 0 && e.key === 'Tab') { e.preventDefault(); indent(e.shiftKey ? -1 : 1); return; }
  if (i > 0 && e.ctrlKey && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) { e.preventDefault(); indent(e.key === 'ArrowRight' ? 1 : -1); return; }
  if (e.key === 'Backspace' && pos === 0 && end === 0 && i > 0) {
    e.preventDefault(); commit(); snapshot(p);
    if (S.edInd > 0) { S.edInd--; p.lines[i] = ' '.repeat(S.edInd) + ta.value; save(); relayout(p, i, 0); return; }
    const prevLen = p.lines[i - 1].length, prevInd = i - 1 === 0 ? 0 : indOf(p.lines[i - 1]);
    p.lines[i - 1] += ta.value; p.lines.splice(i, 1); save();
    relayout(p, i - 1, prevLen - prevInd); return;
  }
  if (i > 0 && (e.ctrlKey || e.altKey) && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
    e.preventDefault(); commit();
    const b = e.altKey ? blockEnd(p, i) : i;
    const at = moveLines(p, i, b, e.key === 'ArrowUp' ? -1 : 1, e.altKey);
    if (at != null) relayout(p, at, pos);
    return;
  }
  if (e.shiftKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown') && caretRow(ta) !== 'mid' && i > 0) {
    const j = Math.min(p.lines.length - 1, Math.max(1, i + (e.key === 'ArrowUp' ? -1 : 1)));
    if (j !== i && (pos === end)) { e.preventDefault(); setRange(i, j); return; }
  }
  if (e.key === 'ArrowUp' && !e.shiftKey && i > 0 && ['any', 'first'].includes(caretRow(ta))) { e.preventDefault(); relayout(p, i - 1, pos + S.edInd - (i - 1 === 0 ? 0 : indOf(p.lines[i - 1]))); return; }
  if (e.key === 'ArrowDown' && !e.shiftKey && i < p.lines.length - 1 && ['any', 'last'].includes(caretRow(ta))) { e.preventDefault(); relayout(p, i + 1, pos + S.edInd - indOf(p.lines[i + 1])); return; }
  if (e.key === 'Escape') { e.preventDefault(); stopEdit(); }
}
/* A one-row line moves on both keys; a wrapped line moves only from its start (up) or its end (down). */
function caretRow(ta) {
  const lh = parseFloat(getComputedStyle(ta).lineHeight) || 28;
  if (ta.scrollHeight <= lh * 1.5) return 'any';
  if (ta.selectionStart === 0) return 'first';
  if (ta.selectionStart === ta.value.length) return 'last';
  return 'mid';
}

/* Add files: the button, paste and drop all come here. Pieces of 16 MB, one file at a time, with a progress row each. */
const MAX_FILE = 500e6, ups = [];
let upBusy = false;
const upFail = { size: 'Files can be up to 500 MB each.', empty: 'This file is empty.', full: 'There is no room for this file. Free some in Storage.', 401: 'Sign in again to add files.' };
function addFiles(files, p, o = {}) {
  if (!OWNER || S.visitor || !p || !files.length) return;
  const batch = { p, caret: o.caret, after: S.edit >= 0 && S.cur === p.id ? S.edit : S.last && S.last.id === p.id ? S.last.line : -1, last: null };
  for (const f of files) ups.push({ f, name: f.name || 'file', size: f.size, batch, done: 0, state: 'wait', xhr: null });
  drawUps(); runUps();
}
async function runUps() {
  if (upBusy) return;
  upBusy = true;
  try { for (let u; (u = ups.find(x => x.state === 'wait'));) await sendUp(u); }
  finally { upBusy = false; }
}
const upErr = m => Object.assign(new Error(m), { say: m });
async function sendUp(u) {
  u.state = 'send'; drawUps();
  let key = '';
  try {
    let f = await preparePicture(u.f);
    if (f.name) u.name = f.name;
    u.size = f.size;
    if (!f.size) throw upErr(upFail.empty);
    if (f.size > MAX_FILE) throw upErr(upFail.size);
    const r = await api('POST', '/_/api/files/start', { name: u.name, size: f.size });
    if (!r.ok) throw upErr(upFail[r.data.error] || upFail[r.status] || 'The upload could not start. Try again.');
    key = u.key = r.data.name;
    const parts = [];
    for (let n = 1; n <= r.data.parts; n++) {
      const blob = f.slice((n - 1) * r.data.part, n * r.data.part);
      for (let tries = 0; ; tries++) {
        if (u.state === 'stop') throw upErr('');
        try { parts.push(await putPart(u, key, n, blob, (n - 1) * r.data.part)); break; }
        catch (e) { if (u.state === 'stop' || e.final || tries >= 3) throw e; await new Promise(z => setTimeout(z, 1500 * (tries + 1))); }
      }
    }
    let d;
    for (let tries = 0; ; tries++) {
      d = await api('POST', '/_/api/files/done/' + key, { parts }).catch(() => null);
      if ((d && d.status < 500) || tries >= 3) break;
      await new Promise(z => setTimeout(z, 1500 * (tries + 1)));
    }
    if (!d || !d.ok) throw upErr('');
    key = '';
    u.done = u.size; u.state = 'ok';
    placeFile(u, d.data);
    setTimeout(() => { ups.splice(ups.indexOf(u), 1); drawUps(); }, 1600);
  } catch (e) {
    if (key) api('DELETE', '/_/api/files/upload/' + key).catch(() => {});
    if (u.state === 'stop') ups.splice(ups.indexOf(u), 1);
    else { u.state = 'bad'; u.err = e.say || 'The upload stopped. Check the connection and try again.'; }
  }
  drawUps();
}
// XMLHttpRequest because fetch cannot tell how much has been sent
function putPart(u, key, n, blob, before) {
  return new Promise((ok, no) => {
    const x = u.xhr = new XMLHttpRequest();
    x.open('PUT', `/_/api/files/part/${key}/${n}`);
    x.setRequestHeader('X-RB', '1'); x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = e => { u.done = before + e.loaded; drawUps(); };
    x.onload = () => { if (x.status === 200) { try { return ok(JSON.parse(x.responseText)); } catch (e) { return no(e); } } const e = x.status === 401 ? upErr(upFail[401]) : new Error('part'); e.final = x.status >= 400 && x.status < 500; no(e); };
    x.onerror = x.onabort = () => no(new Error('net'));
    x.send(blob);
  });
}
function drawUps() {
  let box = $('#ups');
  if (!ups.length) { if (box) box.remove(); return; }
  if (!box) {
    box = document.createElement('div'); box.id = 'ups'; box.setAttribute('role', 'status'); box.setAttribute('aria-label', 'Uploads'); document.body.appendChild(box);
    box.addEventListener('click', e => {
      const b = e.target.closest('[data-up]'); if (!b) return; e.stopPropagation();
      const u = ups[+b.dataset.up]; if (!u) return;
      if (b.dataset.act === 'retry') { u.state = 'wait'; u.err = ''; u.done = 0; drawUps(); return runUps(); }
      if (u.state === 'send') { u.state = 'stop'; if (u.xhr) u.xhr.abort(); drawUps(); }
      else { ups.splice(ups.indexOf(u), 1); drawUps(); }
    });
  }
  box.innerHTML = ups.map((u, i) => {
    const pc = u.size ? Math.min(100, Math.floor(u.done / u.size * 100)) : 0;
    const say = u.state === 'bad' ? u.err : u.state === 'wait' ? 'Waiting' : u.state === 'ok' ? 'Added' : u.state === 'stop' ? 'Stopping' : `${pc}% of ${fmtSize(u.size)}`;
    return `<div class="up ${u.state}"><b>${esc(u.name)}</b><span>${esc(say)}</span>${u.state === 'ok' || u.state === 'stop' ? '' : `<span class="act">${u.state === 'bad' ? `<button type="button" data-up="${i}" data-act="retry">Try again</button>` : ''}<button type="button" data-up="${i}" data-act="x">${u.state === 'bad' ? 'Dismiss' : 'Cancel'}</button></span>`}<i class="bar"><i style="width:${u.state === 'bad' ? 0 : pc}%"></i></i></div>`;
  }).join('');
}
/* Write the new file into the page it was meant for: at the caret for a pasted picture, else on its own line. */
function placeFile(u, d) {
  const p = byId(u.batch.p.id);
  if (!p || !p.lines) return toast('The file is in Storage. Its page is no longer open.');
  const label = u.name.replace(/[\[\]`]/g, ' ').replace(/\s+/g, ' ').trim();
  const ref = d.kind === 'image' ? `[${d.url}]` : `[${d.url} ${label}]`;
  const here = S.cur === p.id, ta = $('#ed');
  if (u.batch.caret && here && ta && S.edit === u.batch.caret.line && d.kind === 'image') { insertAtCaret(ta, ref); return; }
  const caret = here && ta ? ta.selectionStart : 0;
  if (here) commit();
  snapshot(p);
  let at = u.batch.last ? p.lines.indexOf(u.batch.last) + 1 : 0;
  if (!at) at = u.batch.after > 0 ? Math.min(u.batch.after + 1, p.lines.length) : p.lines.length;
  if (at < 1) at = 1;
  // never in the middle of a code or table block
  for (let k = 1, blk = -1; k < p.lines.length; k++) { const ind = indOf(p.lines[k]); if (blk >= 0 && ind > blk && p.lines[k].trim()) { if (k === at) at = k + 1; continue; } blk = /^(code|table):/.test(p.lines[k].slice(ind)) ? ind : -1; }
  if (at === p.lines.length && at > 1 && !p.lines[at - 1].trim() && !(here && S.edit === at - 1)) at--;
  p.lines.splice(at, 0, ref); u.batch.last = ref;
  if (here && S.edit >= at) S.edit++;
  S.touched.add(p.id + ':' + at); p.updated = Date.now(); save(p);
  if (here) { setBody(p); if (S.edit >= 0) mountEditor(p, caret); renderRelated(p, false); }
}
window.addEventListener('beforeunload', e => { if (ups.some(u => u.state === 'send' || u.state === 'wait')) { e.preventDefault(); e.returnValue = ''; } });
window.addEventListener('pagehide', () => { for (const u of ups) if (u.state === 'send' && u.key) { try { fetch('/_/api/files/upload/' + u.key, { method: 'DELETE', keepalive: true, credentials: 'same-origin', headers: { 'X-RB': '1' } }); } catch (e) {} } });

/* Pictures before they leave the device: rebuilt from what is needed to draw them (picture.js), so the place they were
   taken stays behind. HEIC and AVIF are drawn again as JPEG where the browser can read them. A picture that cannot be
   rebuilt, or a kind this page does not know, is never sent without asking. */
async function redraw(f) {
  const bm = await createImageBitmap(f);
  const k = Math.min(1, 4096 / Math.max(bm.width, bm.height));
  const c = document.createElement('canvas'); c.width = Math.round(bm.width * k); c.height = Math.round(bm.height * k);
  const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); g.drawImage(bm, 0, 0, c.width, c.height); bm.close();
  const b = await new Promise(z => c.toBlob(z, 'image/jpeg', .92));
  if (!b || b.type !== 'image/jpeg') throw new Error('draw');
  return new File([b], (f.name || 'picture').replace(/\.\w{1,5}$/, '') + '.jpg', { type: 'image/jpeg' });
}
async function preparePicture(f) {
  const start = new Uint8Array(await f.slice(0, 32).arrayBuffer());
  if (!looksLikePicture(f.name, f.type, start)) return f;
  if (f.size > MAX_FILE) throw upErr(upFail.size);
  try {
    const kind = pictureKind(start);
    const bytes = cleanPicture(new Uint8Array(await f.arrayBuffer()), kind);
    if (!bytes) return await redraw(f);
    const ext = kind === 'jpeg' ? '.jpg' : '.' + kind, right = kind === 'jpeg' ? /\.jpe?g$/i : new RegExp('\\' + ext + '$', 'i');
    return new File([bytes], right.test(f.name) ? f.name : (f.name || 'picture').replace(/\.\w{1,5}$/, '') + ext, { type: 'image/' + kind });
  } catch (e) {
    if (confirm(`The place where “${f.name}” was taken could not be removed from the picture. Anyone who can open its page could find it. Add it anyway?`)) return f;
    throw upErr('Not added: its location data could not be removed.');
  }
}
function onPaste(e) {
  const ta = e.target, p = byId(S.cur), cd = e.clipboardData; if (!cd) return;
  const fs = [...(cd.files || [])];
  if (fs.length) { e.preventDefault(); addFiles(fs, p, fs.length === 1 ? { caret: { line: S.edit } } : {}); return; }
  const text = cd.getData('text/plain');
  if (!text) return;
  if (isImg(text.trim()) && !text.includes('\n')) { e.preventDefault(); insertAtCaret(ta, '[' + text.trim() + ']'); return; }
  if (text.includes('\n') && S.edit > 0) {
    e.preventDefault(); commit(); snapshot(p);
    const parts = text.replace(/\r/g, '').replace(/\t/g, ' ').split('\n');
    const pos = ta.selectionStart, end = ta.selectionEnd, v = ta.value, i = S.edit;
    const head = ' '.repeat(S.edInd) + v.slice(0, pos) + parts[0];
    const mids = parts.slice(1).map(l => ' '.repeat(S.edInd) + l);
    const tailLen = mids[mids.length - 1].length;
    mids[mids.length - 1] += v.slice(end);
    p.lines.splice(i, 1, head, ...mids);
    mids.forEach((_, k) => S.touched.add(p.id + ':' + (i + 1 + k)));
    p.updated = Date.now(); save();
    relayout(p, i + mids.length, tailLen - S.edInd);
  }
}
document.addEventListener('dragover', e => { if (S.cur && !S.visitor && e.target.closest && e.target.closest('.page')) { e.preventDefault(); e.target.closest('.page').classList.add('drop'); } });
document.addEventListener('dragleave', e => { const pg = e.target.closest && e.target.closest('.page'); if (pg) pg.classList.remove('drop'); });
document.addEventListener('drop', e => {
  const pg = e.target.closest && e.target.closest('.page'); if (!pg || !S.cur || S.visitor) return;
  e.preventDefault(); pg.classList.remove('drop');
  addFiles([...(e.dataTransfer.files || [])], byId(S.cur));
});

/* Selection menu inside a line */
function hideSel() { const s = $('.selpop'); if (s) s.remove(); }
function checkSel(ta) {
  if ($('#ed') !== ta) return;
  const s = ta.selectionStart, e = ta.selectionEnd;
  const pop = $('.selpop');
  if (s === e) { if (pop && !pop.querySelector('input')) hideSel(); return; }
  if (pop) return;
  S.selRange = [s, e];
  const line = ta.closest('.line');
  const el = document.createElement('div');
  el.className = 'selpop'; el.setAttribute('role', 'toolbar'); el.setAttribute('aria-label', 'Selection');
  el.innerHTML = `<button data-w="link">Link</button><button data-w="bold">Bold</button><button data-w="big">Bigger</button><button data-w="it">Italic</button><button data-w="st">Strike</button><span class="sep"></span>
    <button class="sw" data-w="red" aria-label="Red"><i style="background:#e66"></i></button><button class="sw" data-w="orange" aria-label="Orange"><i style="background:#ea2"></i></button><button class="sw" data-w="green" aria-label="Green"><i style="background:#6a6"></i></button><button class="sw" data-w="blue" aria-label="Blue"><i style="background:#6ae"></i></button><span class="sep"></span>
    <button data-w="url">Web link</button><button data-w="code">Code</button><button data-w="newpage">New page</button>`;
  line.appendChild(el);
  const top = -el.offsetHeight - 6;
  el.style.top = top + 'px'; el.style.left = '0px';
  el.addEventListener('mousedown', ev => { if (!ev.target.closest('input')) ev.preventDefault(); });
  el.addEventListener('click', ev => {
    const b = ev.target.closest('[data-w]'); if (!b) return;
    const t = $('#ed'); if (!t) return;
    t.setSelectionRange(S.selRange[0], S.selRange[1]);
    const w = b.dataset.w;
    const map = { link: ['[', ']'], bold: ['[* ', ']'], big: ['[** ', ']'], it: ['[/ ', ']'], st: ['[- ', ']'], red: ['[! ', ']'], orange: ['[% ', ']'], green: ['[| ', ']'], blue: ['[~ ', ']'], code: ['`', '`'] };
    if (map[w]) return wrapSel(t, ...map[w]);
    if (w === 'url') {
      el.innerHTML = `<input id="urlIn" type="url" placeholder="Paste a web address, then Enter" aria-label="Web address">`;
      const inp = $('#urlIn'); inp.focus();
      inp.addEventListener('keydown', k => {
        if (k.key === 'Enter') { k.preventDefault(); const u = inp.value.trim(); const t2 = $('#ed'); if (!t2) return; t2.focus(); t2.setSelectionRange(S.selRange[0], S.selRange[1]); hideSel(); if (isUrl(u)) wrapSel(t2, '[', ' ' + u + ']'); else toast('Start the address with https://'); }
        if (k.key === 'Escape') { k.preventDefault(); hideSel(); const t2 = $('#ed'); if (t2) t2.focus(); }
      });
      inp.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== inp) { hideSel(); const t2 = $('#ed'); if (t2 && document.activeElement !== t2) stopEdit(); } }, 160));
      return;
    }
    if (w === 'newpage') return splitSelection(t);
  });
}
function wrapSel(ta, pre, post) {
  const s = ta.selectionStart, e = ta.selectionEnd, v = ta.value;
  ta.value = v.slice(0, s) + pre + v.slice(s, e) + post + v.slice(e);
  ta.setSelectionRange(s + pre.length, e + pre.length);
  hideSel(); autosize(ta); ta.focus();
  ta.dispatchEvent(new Event('input'));
  setTimeout(hideSel, 0);
}
function uniqueTitle(t) { let n = t.slice(0, 60).trim() || 'Untitled', k = 2; const base = n; while (byTitle(n)) n = `${base} ${k++}`; return n; }
function splitSelection(ta) {
  const p = byId(S.cur), s = ta.selectionStart, e = ta.selectionEnd;
  const sel = ta.value.slice(s, e).trim(); if (!sel) return;
  const ex = byTitle(sel);
  const name = ex ? title(ex) : uniqueTitle(sel);
  if (!ex) { const np = addPage({ lines: [name, '', '[' + title(p) + ']'] }); np.fresh = false; save(np); }
  ta.value = ta.value.slice(0, s) + '[' + name + ']' + ta.value.slice(e);
  const c = s + name.length + 2; ta.setSelectionRange(c, c);
  hideSel(); ta.dispatchEvent(new Event('input'));
  toast(ex ? `Linked to ${name}` : `New page: ${name}`);
}

/* Several lines selected */
function setRange(a, b) {
  const p = byId(S.cur); if (!p) return;
  if (S.edit >= 0) commit();
  S.edit = -1; hideAc(); hideSel();
  S.range = [Math.max(1, Math.min(a, b)), Math.max(a, b)];
  setBody(p);
  drawRangeBar();
}
function drawRangeBar() {
  hideSel();
  const first = document.querySelector(`#body .line[data-i="${S.range[0]}"]`); if (!first) return;
  const el = document.createElement('div');
  el.className = 'selpop'; el.setAttribute('role', 'toolbar'); el.setAttribute('aria-label', 'Selected lines');
  const n = S.range[1] - S.range[0] + 1;
  el.innerHTML = `<button data-r="split">New page from ${n} line${n === 1 ? '' : 's'}</button><button data-r="out">Outdent</button><button data-r="in">Indent</button><button data-r="copy">Copy</button><button data-r="del">Delete</button><button data-r="x" aria-label="Clear selection">Esc</button>`;
  $('#body').appendChild(el);
  el.style.top = Math.max(-40, first.offsetTop - el.offsetHeight - 6) + 'px'; el.style.left = '0px';
  el.addEventListener('mousedown', ev => ev.preventDefault());
  el.addEventListener('click', ev => { const b = ev.target.closest('[data-r]'); if (b) rangeAct(b.dataset.r); });
}
function rangeAct(act) {
  const p = byId(S.cur); if (!p || !S.range) return;
  const [a, b] = S.range;
  if (act === 'x') { S.range = null; rerenderBody(); return; }
  if (act === 'copy') {
    const txt = p.lines.slice(a, b + 1).join('\n');
    if (navigator.clipboard) navigator.clipboard.writeText(txt).then(() => toast('Copied.'), () => toast('Copy did not work here. Select the text and copy it.'));
    return;
  }
  snapshot(p);
  if (act === 'del') { p.lines.splice(a, b - a + 1); if (p.lines.length < 2) p.lines.push(''); S.range = null; }
  else if (act === 'in' || act === 'out') { for (let k = a; k <= b; k++) p.lines[k] = act === 'in' ? ' ' + p.lines[k] : p.lines[k].replace(/^ /, ''); }
  else if (act === 'split') {
    const ls = p.lines.slice(a, b + 1);
    const base = indOf(ls[0]);
    const name = uniqueTitle(plain(ls[0].trim()));
    const body = ls.slice(1).map(l => ' '.repeat(Math.max(0, indOf(l) - base - 1)) + l.trim());
    { const np = addPage({ pub: p.pub, lines: [name, ...body, '', '[' + title(p) + ']'] }); np.fresh = false; save(np); }
    p.lines.splice(a, b - a + 1, ' '.repeat(base) + '[' + name + ']');
    S.touched.add(p.id + ':' + a); S.range = null;
    toast(`Split into a new page: ${name}`);
  }
  p.updated = Date.now(); save();
  rerenderBody(); renderRelated(p, false);
}

/* Link suggestions */
function checkAc(ta) {
  const before = ta.value.slice(0, ta.selectionStart);
  const m = before.match(/\[([^\[\]]*)$/);
  if (!m || /^[!"#%&'()*+,\-./{|}<>_~$]+\s/.test(m[1]) || /^https?:/.test(m[1])) return hideAc();
  const q = m[1].toLowerCase();
  computeHeat();
  const cur = title(byId(S.cur));
  const list = visPages().map(title).filter(t => t !== cur && t.toLowerCase().includes(q)).sort((a, b) => countOf(b) - countOf(a)).slice(0, 6);
  if (q && !list.some(t => typeof t === 'string' && t.toLowerCase() === q)) list.push({ make: m[1] });
  if (!list.length) return hideAc();
  S.ac = { list, sel: 0, q: m[1] }; drawAc();
}
function drawAc() {
  const box = $('#ac'); if (!box || !S.ac) return;
  box.hidden = false;
  box.innerHTML = S.ac.list.map((t, k) => `<div role="option" data-k="${k}" aria-selected="${k === S.ac.sel}">${typeof t === 'string' ? `${esc(t)}<small>${countOf(t)} links</small>` : `${esc(t.make)}<small>new page</small>`}</div>`).join('') + `<div class="foot">Tab to choose · Enter links · Ctrl+I makes an icon</div>`;
  box.onmousedown = ev => { const d = ev.target.closest('[data-k]'); if (d) { ev.preventDefault(); pickAc(S.ac.list[+d.dataset.k], false); } else ev.preventDefault(); };
}
function hideAc() { S.ac = null; const b = $('#ac'); if (b) b.hidden = true; }
function pickAc(t, asIcon) {
  const ta = $('#ed'); if (!ta) return;
  const name = typeof t === 'string' ? t : t.make;
  const pos = ta.selectionStart, before = ta.value.slice(0, pos), after = ta.value.slice(pos);
  const start = before.lastIndexOf('[');
  const rest = after.startsWith(']') ? after.slice(1) : after;
  const ins = '[' + name + (asIcon ? '.icon' : '') + ']';
  ta.value = before.slice(0, start) + ins + rest;
  const c = start + ins.length; ta.setSelectionRange(c, c);
  hideAc(); autosize(ta);
  ta.dispatchEvent(new Event('input')); hideAc();
}

/* Footer: share the page on X, or the notebook from the list views. Private pages can't be shared. */
const SITE = (BOOT.base || location.origin) + '/';
const COFFEE = 'https://buymeacoffee.com/tatsuki3284';
const PROFILE_SITE = 'https://tatsuki.tany.im/?lang=ja';
/* Hashtags on every shared post: the author, what this is, and the company. */
const SHARE_TAGS = ['TatsukiMitsuhashi', 'Notes', 'TANYLLC'];
/* The page's own #tags (outside code blocks), up to 3, plus the notebook's tag. */
function pageTags(p) {
  const out = []; let blk = -1;
  p.lines.slice(1).forEach(raw => {
    const ind = indOf(raw);
    if (blk >= 0 && ind > blk) return; blk = -1;
    const t = raw.slice(ind);
    if (/^(code|table):/.test(t)) { blk = ind; return; }
    for (const m of t.replace(/`[^`]*`/g, '').matchAll(/(?:^|\s)#([^\s\[\]#]+)/g)) if (!out.includes(m[1])) out.push(m[1]);
  });
  return [...out.slice(0, 3), 'RhizomeBox'];
}
function shareHref(p) {
  const url = p ? SITE + pageUrl(title(p)).slice(1) : SITE;
  const text = p ? title(p) : 'Rhizome Box: notes that grow like roots';
  const tags = SHARE_TAGS;
  return `https://x.com/intent/post?text=${encodeURIComponent(text)}&url=${encodeURIComponent(url)}&hashtags=${encodeURIComponent(tags.join(','))}`;
}
function pfootHtml(p) {
  const priv = !p.pub;
  const me = byTitle(ME), img = (me && titleImage(me)) || AVATAR;
  return `<div class="row">
      <a class="sbtn x sm" href="${esc(shareHref(p))}" target="_blank" rel="noopener" aria-disabled="${priv}">${XICON}Share on X</a>
      <span class="sharewrap"><button class="sbtn more sm" type="button" data-share="${p.id}" aria-haspopup="true" aria-expanded="false" ${priv ? 'aria-disabled="true"' : ''}>${SHAREICON}Share</button></span>
      <a class="sbtn coffee sm" href="${COFFEE}" target="_blank" rel="noopener">${CUPICON}Buy me a coffee</a>
    </div>
    ${priv ? '<p class="preview">This page is private. Turn it public to share it.</p>' : ''}
    <a class="acard" href="${PROFILE_SITE}" target="_blank" rel="noopener" aria-label="About me: open Tatsuki Mitsuhashi's profile site">
      <img src="${esc(img)}" alt="">
      <span class="who"><span class="eb">About me</span><b>Tatsuki Mitsuhashi</b><span class="intro">夜寝る前に「明日も楽しみだな」と思える人を、世界に増やしたい。</span><span class="roles">空白デザイナー · 多言語間翻訳作家 · 卒成長経営者</span></span>
      <span class="go">Profile site ↗</span>
    </a>
    <p class="copy">© 2026 Tatsuki Mitsuhashi · TANY LLC · Rhizome Box</p>`;
}
const XICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M17.75 3h3.07l-6.7 7.66L22 21h-6.17l-4.83-6.32L5.47 21H2.4l7.17-8.2L2 3h6.33l4.37 5.78zm-1.08 16.17h1.7L7.4 4.74H5.58z"/></svg>`;
const CUPICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M4 9h12v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5zM16 10h1.5a2.5 2.5 0 0 1 0 5H16M8 3v2M12 3v2"/></svg>`;
const SHAREICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M12 3v12M7 8l5-5 5 5M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6"/></svg>`;
/* Other places to share. Each opens that service's own share page; nothing is posted until the reader confirms there. */
function shareTargets(p) {
  const url = p ? SITE + pageUrl(title(p)).slice(1) : SITE;
  const text = (p ? title(p) : 'Rhizome Box: notes that grow like roots') + ' ' + SHARE_TAGS.map(t => '#' + t).join(' ');
  const e = encodeURIComponent;
  return [
    ['Threads', `https://www.threads.net/intent/post?text=${e(text + ' ' + url)}`],
    ['Bluesky', `https://bsky.app/intent/compose?text=${e(text + ' ' + url)}`],
    ['LinkedIn', `https://www.linkedin.com/sharing/share-offsite/?url=${e(url)}`],
    ['Facebook', `https://www.facebook.com/sharer/sharer.php?u=${e(url)}`],
    ['LINE', `https://social-plugins.line.me/lineit/share?url=${e(url)}`],
  ];
}
function closeShare() { document.querySelectorAll('.sharepop').forEach(x => x.remove()); document.querySelectorAll('[data-share][aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false')); }
function openShare(btn) {
  const open = btn.getAttribute('aria-expanded') === 'true';
  closeShare(); if (open) return;
  const p = btn.dataset.share === 'home' ? (S.cur && byId(S.cur)) || null : byId(btn.dataset.share);
  if (p && !p.pub) { toast('This page is private. Turn it public to share it.'); return; }
  const pop = document.createElement('div');
  pop.className = 'sharepop'; pop.setAttribute('role', 'menu');
  pop.innerHTML = shareTargets(p).map(([n, u]) => `<a role="menuitem" href="${esc(u)}" target="_blank" rel="noopener">${n}</a>`).join('') + `<button role="menuitem" type="button" data-copylink="${esc(p ? SITE + pageUrl(title(p)).slice(1) : SITE)}">Copy link</button>`;
  btn.parentNode.appendChild(pop); btn.setAttribute('aria-expanded', 'true');
}
function togglePin(p) {
  p.pin = p.pin ? 0 : Date.now(); save();
  const b = $('#pinBtn'); if (b) { b.setAttribute('aria-pressed', !!p.pin); b.lastChild.textContent = p.pin ? 'Pinned' : 'Pin'; }
  toast(p.pin ? 'Pinned. It stays at the top of the page list.' : 'Unpinned.');
}
function setLook(look) {
  S.look = look; store(KEY + '-look', look);
  document.documentElement.dataset.look = look;
  const b = $('#lookBtn'); if (b) { b.setAttribute('aria-pressed', look === 'ink'); b.setAttribute('aria-label', look === 'ink' ? 'Switch to the light look' : 'Switch to the dark look'); }
  S.graphSig = ''; route();
}
function updateFoot() {
  const p = S.cur && byId(S.cur);
  const a = $('#shareX'), note = $('#shareNote'); if (!a) return;
  const priv = p && !p.pub;
  a.href = shareHref(p);
  const pf = $('#pfoot'); if (pf && p) pf.innerHTML = pfootHtml(p);
  a.setAttribute('aria-disabled', priv ? 'true' : 'false');
  note.hidden = !priv;
  const img = $('#meImg'); const me = byTitle(ME); if (img) img.src = (me && titleImage(me)) || AVATAR;
}

/* Profile header on the owner's own page */
function profileHero(p) {
  const img = titleImage(p) || AVATAR;
  computeHeat();
  return `<header class="prof">
    <img class="avatar" src="${esc(img)}" alt="Tatsuki Mitsuhashi">
    <div class="pinfo">
      <h1>Tatsuki Mitsuhashi</h1>
      <div class="handle">@tatsuki3284 · writing Rhizome Box</div>
      <div class="roles"><span>空白デザイナー</span><span>多言語間翻訳作家</span><span>卒成長経営者</span></div>
      <p class="mission">夜寝る前に「明日も楽しみだな」と思える人を、世界に増やしたい。</p>
      <div class="plinks">
        <a class="plink strong" href="https://tatsuki.tany.im/?lang=ja" target="_blank" rel="noopener">Full profile ↗</a>
        <a class="plink" href="https://x.com/tatsuki3284" target="_blank" rel="noopener">Follow on X</a>
        <a class="plink" href="https://tany.im/campus/" target="_blank" rel="noopener">Newsletter</a>
        <a class="plink coffee" href="${COFFEE}" target="_blank" rel="noopener">Buy me a coffee</a>
      </div>
      <div class="stats"><span><b>${visPages().length}</b> pages</span><span><b>${S.heat.size}</b> linked words</span><span><b>50+</b> countries</span></div>
    </div>
  </header>`;
}

/* Outside links and images across the notebook */
const metaTitle = u => (S.meta.get(u) || {}).title || '';
const metaImg = u => { const m = S.meta.get(u); return m && /^https:\/\//.test(m.image || '') ? m.image : ''; };
let metaBusy = false;
async function ensureMeta(urls, done) {
  const want = [...new Set(urls)].filter(u => !S.meta.has(u)).slice(0, 100);
  if (!want.length || metaBusy) return;
  metaBusy = true;
  try {
    const r = await api('GET', '/_/api/linkmeta?' + want.map(u => 'u=' + encodeURIComponent(u)).join('&'));
    want.forEach(u => S.meta.set(u, (r.ok && r.data[u]) || {}));
    if (r.ok && Object.keys(r.data).length && done) done();
  } catch (e) {} finally { metaBusy = false; }
}
function hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return ''; } }
function kindOf(u) {
  const h = hostOf(u);
  if (/(^|\.)youtube\.com$|^youtu\.be$/.test(h)) return 'youtube';
  if (/wikipedia\.org$/.test(h)) return 'wikipedia';
  if (/scrapbox\.io$|cosen\.se$/.test(h)) return 'scrapbox';
  return 'web';
}
const KIND_NAME = { youtube: 'YouTube', wikipedia: 'Wikipedia', scrapbox: 'Scrapbox', web: 'Web' };
/* A readable name for a link that has no label: the search words, or the last part of the path. */
function guessTitle(u) {
  try {
    const x = new URL(u);
    const q = x.searchParams.get('search_query') || x.searchParams.get('q');
    if (q) return q.replace(/\+/g, ' ');
    const last = decodeURIComponent(x.pathname.split('/').filter(Boolean).pop() || '').replace(/_/g, ' ');
    return last || hostOf(u);
  } catch (e) { return u; }
}
function webLinksOf(p) { return p.lines ? webOfLines(p.lines) : (p.web || []); }
function collectWeb() {
  const map = new Map();
  for (const p of visPages()) for (const { url, label } of webLinksOf(p)) {
    const e = map.get(url) || { url, label: '', pages: new Map(), kind: kindOf(url), host: hostOf(url) };
    if (!e.label && label) e.label = label;
    e.pages.set(p.id, p); map.set(url, e);
  }
  return [...map.values()].map(e => ({ ...e, title: e.label || metaTitle(e.url) || guessTitle(e.url), n: e.pages.size }));
}
function webIcon(kind, host) {
  if (kind === 'youtube') return `<span class="wicon yt" aria-hidden="true"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M9 7.5v9l7.5-4.5z"/></svg></span>`;
  if (kind === 'wikipedia') return `<span class="wicon wk" aria-hidden="true">W</span>`;
  if (kind === 'scrapbox') return `<span class="wicon sb" aria-hidden="true">S</span>`;
  return `<span class="wicon wb" aria-hidden="true">${esc((host[0] || '?').toUpperCase())}</span>`;
}
function renderWeb() {
  dropGraph(); S.cur = null; S.edit = -1; S.range = null;
  const all = collectWeb();
  ensureMeta(all.map(e => e.url), () => { if (location.pathname === '/_/web') renderWeb(); });
  const counts = { all: all.length }; all.forEach(e => counts[e.kind] = (counts[e.kind] || 0) + 1);
  const f = S.webKind || 'all';
  const rows = all.filter(e => f === 'all' || e.kind === f).sort((a, b) => b.n - a.n || a.title.localeCompare(b.title, 'ja'));
  const hosts = new Map(); all.forEach(e => hosts.set(e.host, (hosts.get(e.host) || 0) + e.n));
  const topHosts = [...hosts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  const shared = all.filter(e => e.n > 1).length;
  $('#main').innerHTML = `
    ${tabsHtml('web')}
    <div class="links-tools">
      <div class="seg" role="group" aria-label="Filter web links">${['all', 'youtube', 'wikipedia', 'scrapbox', 'web'].filter(k => k === 'all' || counts[k]).map(k => `<button data-wk="${k}" aria-pressed="${f === k}">${k === 'all' ? 'All' : KIND_NAME[k]} ${counts[k] || 0}</button>`).join('')}</div>
      <span class="legend">${shared} link${shared === 1 ? '' : 's'} used on more than one page</span>
    </div>
    <div class="hosts">${topHosts.map(([h, n]) => `<span class="hostchip">${esc(h)} <b>${n}</b></span>`).join('')}</div>
    <ul class="wlist">${rows.map((e, i) => `<li class="wrow rin" style="--i:${i}">
      ${metaImg(e.url) ? `<img class="wthumb" src="${esc(metaImg(e.url))}" alt="" loading="lazy" referrerpolicy="no-referrer">` : webIcon(e.kind, e.host)}
      <div class="wmain">
        <a class="wtitle" href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>
        <div class="wsub"><span>${esc(e.host)}</span><span class="dot-sep">·</span><span>${KIND_NAME[e.kind]}</span>${e.n > 1 ? `<span class="shared">On ${e.n} pages</span>` : ''}</div>
        <div class="wpages">${[...e.pages.values()].map(p => `<a class="wpage" data-go="${p.id}">${esc(title(p))}</a>`).join('')}</div>
      </div>
      <a class="wopen" href="${esc(e.url)}" target="_blank" rel="noopener" aria-label="Open ${esc(e.title)}">Open ↗</a>
    </li>`).join('') || '<li class="empty-note">No web links yet. Paste a URL into any page.</li>'}</ul>`;
  document.querySelectorAll('[data-wk]').forEach(b => b.onclick = () => { S.webKind = b.dataset.wk; renderWeb(); });
}
function collectImages() {
  const out = [];
  for (const p of visPages()) (p.lines ? imagesOf(p.lines) : (p.images || [])).forEach(u => out.push({ src: resolve(u), p, url: u }));
  return out;
}
function renderImages() {
  dropGraph(); S.cur = null; S.edit = -1; S.range = null;
  const imgs = collectImages();
  $('#main').innerHTML = `
    ${tabsHtml('images')}
    <div class="home-head"><span>Every picture in the notebook. Press one to open its page.</span><span>${imgs.length} image${imgs.length === 1 ? '' : 's'}</span></div>
    <ul class="igrid">${imgs.map((x, i) => `<li class="rin" style="--i:${i}"><a class="icard" data-go="${x.p.id}" role="link" tabindex="0"><span class="iframe"><img src="${esc(x.src)}" alt="" loading="lazy"></span><span class="ititle">${esc(title(x.p))}</span></a></li>`).join('') || '<li class="empty-note">No images yet. Add one to a page.</li>'}</ul>`;
}
/* Storage: how much room is used and what uses it. Owner only. */
const KIND_LABEL = { image: 'Pictures', pdf: 'PDF', video: 'Video', audio: 'Audio', other: 'Other' };
async function renderStorage() {
  dropGraph(); S.cur = null; S.edit = -1; S.range = null;
  const st = S.store || (S.store = { data: null, kind: 'all', ask: '' });
  if (!st.data) $('#main').innerHTML = `${tabsHtml('storage')}<p class="empty-note">Loading…</p>`;
  else drawStorage();
  const r = await api('GET', '/_/api/storage').catch(() => null);
  if (location.pathname !== '/_/storage' || S.visitor) return;
  if (!r || !r.ok) { if (!st.data) $('#main').innerHTML = `${tabsHtml('storage')}<p class="empty-note">${r && r.status === 401 ? 'Sign in again to see Storage.' : 'Storage could not be loaded. Check the connection and reload.'}</p>`; return; }
  st.data = r.data; drawStorage();
}
function drawStorage() {
  const st = S.store, d = st.data, left = Math.max(0, d.cap - d.used - d.uploading), pc = (d.used + d.uploading) / d.cap * 100;
  const kinds = Object.keys(KIND_LABEL).filter(k => d.kinds[k]);
  const unused = d.files.filter(f => !f.pages.length && !f.keep);
  const list = st.kind === 'all' ? d.files : st.kind === 'unused' ? unused : d.files.filter(f => f.kind === st.kind);
  const name = f => f.orig || (f.keep ? 'Profile picture' : f.name);
  const warn = pc >= 95 ? 'Almost full. New uploads stop at ' + fmtSize(d.cap) + '.' : pc >= 80 ? 'Getting full. Delete large or unused files to make room.' : '';
  $('#main').innerHTML = `
    ${tabsHtml('storage')}
    <section class="meter" aria-label="Storage used">
      <p class="mnum"><b>${fmtSize(d.used)}</b> of ${fmtSize(d.cap)} used</p>
      <div class="mbar" role="meter" aria-valuemin="0" aria-valuemax="${d.cap}" aria-valuenow="${d.used}" aria-valuetext="${fmtSize(d.used)} of ${fmtSize(d.cap)}">${kinds.map(k => `<i class="mk-${k}" style="width:${d.kinds[k].size / d.cap * 100}%" title="${KIND_LABEL[k]} ${fmtSize(d.kinds[k].size)}"></i>`).join('')}</div>
      <p class="mleft"><b>${fmtSize(left)} left</b><span>${pc > 0 && pc < .1 ? 'under 0.1' : pc.toFixed(1)}%</span>${d.uploading ? `<span>${fmtSize(d.uploading)} held by uploads under way</span>` : ''}</p>
      ${warn ? `<p class="mwarn">${warn}</p>` : ''}
      <ul class="mkinds">${kinds.map(k => `<li><i class="mk-${k}"></i>${KIND_LABEL[k]} <b>${fmtSize(d.kinds[k].size)}</b><span>${d.kinds[k].n}</span></li>`).join('')}</ul>
      <p class="hint">Up to ${fmtSize(d.maxFile)} per file. ${fmtSize(d.cap)} stays inside the free plan.</p>
    </section>
    <div class="links-tools"><div class="seg" role="group" aria-label="Filter files">${[['all', 'All', d.files.length], ...kinds.map(k => [k, KIND_LABEL[k], d.kinds[k].n]), ['unused', 'Not used', unused.length]].map(([k, l, n]) => `<button data-sk="${k}" aria-pressed="${st.kind === k}">${l} ${n}</button>`).join('')}</div>
      <button class="btn sm" id="recount" type="button">Recount</button></div>
    <ul class="slist">${list.map(f => `<li class="srow">
      ${f.kind === 'image' ? `<img class="sthumb" src="/files/${esc(f.name)}" alt="" loading="lazy">` : `<span class="sthumb mk-${f.kind}">${esc(f.kind === 'other' ? (f.name.split('.').pop() || '').slice(0, 4).toUpperCase() : KIND_LABEL[f.kind])}</span>`}
      <div class="wmain"><a class="wtitle" href="/files/${esc(f.name)}" target="_blank" rel="noopener">${esc(name(f))}</a>
        <div class="wsub"><span>${fmtSize(f.size)}</span><span class="dot-sep">·</span><span>${new Date(f.created).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</span>${f.pages.length ? '' : `<span class="dot-sep">·</span><span>${f.keep ? 'Used by the site' : 'Not used on any page'}</span>`}</div>
        ${f.pages.length ? `<div class="wpages">${f.pages.map(p => `<a class="wpage" data-go="${esc(p.id)}">${esc(p.title)}${p.pub ? '' : ' · private'}</a>`).join('')}</div>` : ''}
        ${st.ask === f.name ? `<div class="sask"><span>${f.pages.length ? `Still on ${f.pages.length} page${f.pages.length === 1 ? '' : 's'}. It will disappear there, also from earlier versions.` : 'It cannot be brought back.'}</span><button class="btn sm danger" data-sdel="${esc(f.name)}" type="button">Delete ${fmtSize(f.size)}</button><button class="btn sm" data-sno type="button">Keep</button></div>` : ''}
      </div>
      ${f.keep || st.ask === f.name ? '' : `<button class="sdel" data-sask="${esc(f.name)}" type="button" aria-label="Delete ${esc(name(f))}">Delete</button>`}</li>`).join('') || '<li class="empty-note">No files here.</li>'}</ul>`;
  showTab();
  document.querySelectorAll('[data-sk]').forEach(b => b.onclick = () => { st.kind = b.dataset.sk; drawStorage(); });
  document.querySelectorAll('[data-sask]').forEach(b => b.onclick = () => { st.ask = b.dataset.sask; drawStorage(); });
  document.querySelectorAll('[data-sno]').forEach(b => b.onclick = () => { st.ask = ''; drawStorage(); });
  document.querySelectorAll('[data-sdel]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    const r = await api('DELETE', '/_/api/files/' + b.dataset.sdel).catch(() => null);
    if (!r || !r.ok) { b.disabled = false; return toast('It could not be deleted. Check the connection and try again.'); }
    st.ask = ''; sizeOf.delete(b.dataset.sdel); toast('Deleted.'); renderStorage();
  });
  $('#recount').onclick = async () => {
    const r = await api('POST', '/_/api/storage/recount', {}).catch(() => null);
    if (!r || !r.ok) return toast('The recount did not work. Try again.');
    const n = r.data.added + r.data.removed + r.data.fixed;
    toast(n ? `Corrected ${n} file${n === 1 ? '' : 's'}.` : 'The numbers match the stored files.'); renderStorage();
  };
}
/* Hover a web link in a page to see what it is and where else it is used */
function peekLink(a) {
  hidePeek();
  const u = a.getAttribute('href'); if (!u) return;
  const e = collectWeb().find(x => x.url === u) || { url: u, title: guessTitle(u), kind: kindOf(u), host: hostOf(u), n: 0, pages: new Map() };
  const others = [...e.pages.values()].filter(p => p.id !== S.cur);
  const el = document.createElement('div');
  el.className = 'peek'; el.setAttribute('role', 'tooltip');
  el.innerHTML = `${metaImg(u) ? `<img src="${esc(metaImg(u))}" alt="" referrerpolicy="no-referrer">` : ''}<div class="prow">${webIcon(e.kind, e.host)}<div><b>${esc(e.title)}</b><span>${esc(e.host)} · ${KIND_NAME[e.kind]}</span></div></div>${others.length ? `<div class="pmore">Also on ${others.map(p => esc(title(p))).join(', ')}</div>` : ''}`;
  document.body.appendChild(el);
  const r = a.getBoundingClientRect();
  const left = Math.min(Math.max(8, r.left), window.innerWidth - el.offsetWidth - 8);
  const top = r.bottom + 8 + el.offsetHeight > window.innerHeight ? r.top - el.offsetHeight - 8 : r.bottom + 8;
  el.style.left = left + 'px'; el.style.top = top + 'px';
}
function hidePeek() { document.querySelectorAll('.peek').forEach(x => x.remove()); }
document.addEventListener('mouseover', e => { const a = e.target.closest && e.target.closest('#body a.ext, #body a.ytcard'); if (a && !(e.relatedTarget && a.contains(e.relatedTarget))) peekLink(a); });
document.addEventListener('mouseout', e => { const a = e.target.closest && e.target.closest('#body a.ext, #body a.ytcard'); if (a && !(e.relatedTarget && a.contains(e.relatedTarget))) hidePeek(); });
document.addEventListener('scroll', hidePeek, { passive: true });

/* Actions */
function newId() { return (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)).replace(/-/g, '').slice(0, 24); }
function addPage(o) {
  const p = Object.assign({ id: newId(), pub: false, pin: 0, updated: Date.now(), links: [], images: [], web: [], descr: '', image: '', fresh: true }, o);
  pages.unshift(p); save(p); return p;
}
function newPage(t) {
  if (!OWNER) return;
  const name = (t || '').trim();
  const ex = name && byTitle(name); if (ex) return go(ex.id);
  if (S.edit >= 0) { commit(); S.edit = -1; }
  leavePage();
  const p = addPage({ lines: [name, ''] });
  S.edit = name ? 1 : 0; S.edInd = 0;
  if (name) { history.pushState(null, '', pageUrl(name)); } else { S.newId = p.id; history.pushState(null, '', '/_/new'); }
  route(); window.scrollTo({ top: 0 });
}
/* An untouched new page disappears when you leave it, as in Scrapbox. */
function leavePage() {
  const p = S.cur && byId(S.cur);
  if (!p || !p.lines || !p.fresh) return;
  if (!(p.lines[0] || '').trim() || bodyEmpty(p)) { pages = pages.filter(x => x !== p); dirty.delete(p.id); }
}
function toggleVis(p) {
  p.pub = !p.pub; save(p);
  const v = $('#vis'); v.setAttribute('aria-checked', p.pub); v.querySelector('b').textContent = p.pub ? 'Public' : 'Private';
  toast(p.pub ? 'Public. Anyone can open and search this page.' : 'Private. Only you can see this page.');
  updateFoot();
}
function fit(id) {
  const p = byId(S.cur), q = byId(id); if (!p || !q) return;
  if (S.edit >= 0) commit();
  snapshot(p);
  const last = p.lines[p.lines.length - 1];
  if (!last.trim()) p.lines[p.lines.length - 1] = '[' + title(q) + ']'; else p.lines.push('[' + title(q) + ']');
  S.touched.add(p.id + ':' + (p.lines.length - 1)); save(p);
  S.edit = -1; delete S.stray[p.id];
  renderPage(p);
}
function setVisitor(on) {
  if (!OWNER) return;
  if (S.edit >= 0) commit();
  S.visitor = on; S.edit = -1; S.range = null;
  $('#asMe').setAttribute('aria-pressed', !on); $('#asVisitor').setAttribute('aria-pressed', on);
  $('#newPage').hidden = on;
  route();
}
function nav(path, replace) {
  if (S.edit >= 0) { commit(); S.edit = -1; }
  S.range = null; leavePage();
  if (location.pathname !== path) history[replace ? 'replaceState' : 'pushState'](null, '', path);
  route(); window.scrollTo({ top: 0 });
}
function go(id) { const p = byId(id); if (p) nav(pageUrl(title(p))); }
function renderMissing() {
  dropGraph(); S.cur = null;
  $('#main').innerHTML = `<div class="crumb"><a data-home>← All pages</a></div><p class="empty-note">There is no page here. It may have moved, or it may not be public.</p>`;
}
async function route() {
  dropGraph(); hidePeek(); closeShare(); renderBanner(); computeHeat(); applyUserCss();
  const m = $('#main'); m.classList.remove('fade'); void m.offsetWidth; m.classList.add('fade');
  setTimeout(() => { updateFoot(); showTab(); }, 0);
  const path = location.pathname;
  if (path === '/') return renderHome();
  if (path.startsWith('/_/')) {
    const v = path.slice(3);
    if (v === 'links') return renderLinks();
    if (v === 'web') return renderWeb();
    if (v === 'images') return renderImages();
    if (v === 'graph') return renderGraph();
    if (v === 'storage' && OWNER && !S.visitor) return renderStorage();
    if (v === 'code' || v === 'login') return renderCode();
    if (v === 'new') { const p = byId(S.newId); if (p && OWNER) return renderPage(p); return nav('/', true); }
    return renderMissing();
  }
  let raw; try { raw = decodeURIComponent(path.slice(1)); } catch (e) { raw = path.slice(1); }
  const p = byTitle(raw) || byTitle(raw.replace(/_/g, ' '));
  if (p) {
    if (!visible(p)) return renderMissing();
    if (!p.lines) {
      S.cur = null;
      m.innerHTML = '<p class="empty-note">Loading…</p>';
      const ok = await loadPage(p).catch(() => false);
      if (location.pathname !== path) return;
      if (!ok) { m.innerHTML = '<p class="empty-note">This page could not be loaded. Check the connection and reload.</p>'; return; }
    }
    return renderPage(p);
  }
  if (OWNER && !S.visitor) {
    const t = raw.replace(/_/g, ' ').trim();
    if (!t) return renderMissing();
    const np = addPage({ lines: [t, ''] });
    S.edit = 1; S.edInd = 0;
    history.replaceState(null, '', pageUrl(t));
    return renderPage(np);
  }
  renderMissing();
}
/* Copy text: the whole page with its title, as plain words. */
async function copyText(p) {
  if (S.edit >= 0) commit();
  const text = plainText(p.lines);
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {
    const t = document.createElement('textarea'); t.value = text; t.style.cssText = 'position:fixed;top:0;opacity:0'; document.body.appendChild(t); t.select();
    try { ok = document.execCommand('copy'); } catch (e2) {}
    t.remove();
  }
  toast(ok ? `Copied. ${[...text.trim()].length.toLocaleString('en-US')} characters.` : 'Copy did not work here. Select the text and copy it.');
}
let toastT;
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => t.hidden = true, 2600); }

/* Banner: visitor preview, saving problems, and two devices editing the same page */
function renderBanner() {
  const parts = [];
  if (OWNER && S.visitor) parts.push(`<div class="banner"><span>Viewing as a visitor. This is what search engines see: private pages and links to them are hidden.</span><button id="backMe">Back to my view</button></div>`);
  if (S.notice === 'offline') parts.push(`<div class="notice warn"><span>Not saved yet. Your changes are kept on this device and will be sent when the connection is back.</span><button id="retryNow">Try now</button></div>`);
  if (S.notice === 'signedout') parts.push(`<div class="notice warn"><span>You are signed out, so changes are only kept on this device.</span><a href="/_/code" data-nav="/_/code">Enter a code</a></div>`);
  if (S.conflict) { const p = byId(S.conflict.id); parts.push(`<div class="notice warn"><span>“${esc(p ? title(p) : '')}” was also changed on another device.</span><button id="keepMine">Keep my version</button><button id="useTheirs">Use the other version</button></div>`); }
  $('#banner').innerHTML = parts.join('');
  const b = $('#backMe'); if (b) b.onclick = () => setVisitor(false);
  const rn = $('#retryNow'); if (rn) rn.onclick = () => flush();
  const km = $('#keepMine'); if (km) km.onclick = () => { const c = S.conflict, p = byId(c.id); S.conflict = null; if (p) { p.rev = c.theirs.rev; save(p); } renderBanner(); };
  const ut = $('#useTheirs'); if (ut) ut.onclick = () => { const c = S.conflict, p = byId(c.id); S.conflict = null; if (p) { snapshot(p); p.lines = c.theirs.lines; p.rev = c.theirs.rev; p.pub = c.theirs.pub; dirty.delete(p.id); dropPending(p.id); if (S.cur === p.id) { S.edit = -1; renderPage(p); } } renderBanner(); };
}

/* The owner's way in. It is called "Code", not "Sign in", so visitors do not think they need an account. */
function renderCode() {
  dropGraph(); S.cur = null;
  if (OWNER) { $('#main').innerHTML = `<div class="login"><h1>You are in</h1><p>You can write and see private pages on this device for 90 days.</p><a class="btn primary" href="/" data-nav="/">Go to my pages</a><button class="btn" id="codeOut" type="button">Sign out on this device</button></div>`; $('#codeOut').onclick = async () => { await flush(); await api('POST', '/_/api/logout').catch(() => null); location.href = '/'; }; return; }
  $('#main').innerHTML = `<div class="login">
    <h1>Code</h1>
    <p>For the writer of this notebook only. Visitors need no code: every public page is open to read.</p>
    <form id="askForm" novalidate><label for="em" class="eb">1. Writer's email</label>
      <div class="codeline"><input id="em" type="email" autocomplete="email" autocapitalize="none" required><button class="btn" type="submit">Send code</button></div>
      <p class="say" id="askSay" role="status" hidden></p></form>
    <form id="codeForm" novalidate><label for="code" class="eb">2. 6-digit code from the email</label>
      <div class="codeline"><input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" required><button class="btn primary" type="submit">Open</button></div>
      <p class="say" id="codeSay" role="status" hidden></p></form></div>`;
  const tell = (id, text, bad) => { const p = $(id); p.textContent = text; p.classList.toggle('err', !!bad); p.hidden = false; };
  $('#askForm').onsubmit = async ev => {
    ev.preventDefault();
    const email = $('#em').value.trim(), btn = $('#askForm button');
    if (!/^\S+@\S+\.\S+$/.test(email)) return tell('#askSay', 'Enter an email address.', true);
    btn.disabled = true;
    const r = await api('POST', '/_/api/login', { email }).catch(() => null);
    btn.disabled = false;
    if (!r || !r.ok) return tell('#askSay', r && r.status === 429 ? 'Too many tries. Wait 10 minutes and try again.' : 'The code could not be sent. Check the connection and try again.', true);
    $('#codeSay').hidden = true;
    tell('#askSay', "If this is the writer's address, a code is on its way. It works for 10 minutes, in this browser.");
    $('#code').value = ''; $('#code').focus();
  };
  let busy = false;
  const enter = async () => {
    const code = $('#code').value.replace(/\D/g, '');
    if (code.length !== 6) return tell('#codeSay', 'Enter the 6 digits from the email.', true);
    if (busy) return; busy = true;
    const v = await api('POST', '/_/api/verify', { code }).catch(() => null);
    busy = false;
    if (v && v.ok) { location.href = '/'; return; }
    tell('#codeSay', !v ? 'The code could not be checked. Check the connection and try again.' : v.status === 429 ? 'Too many tries. Wait 10 minutes and try again.' : v.data.error === 'no_code' ? 'Send a code first, with step 1.' : 'That code does not work. It may have run out: send a new one with step 1.', true);
  };
  $('#codeForm').onsubmit = ev => { ev.preventDefault(); enter(); };
  $('#em').focus();
}

/* History: earlier versions of the open page, and putting one back */
async function openHistory(p) {
  const r = await api('GET', '/_/api/versions/' + p.id).catch(() => null);
  const list = r && r.ok ? r.data.versions : [];
  const box = document.createElement('div');
  box.className = 'histpop';
  box.innerHTML = `<div class="histbox" role="dialog" aria-modal="true" aria-label="Earlier versions"><h2>Earlier versions of ${esc(title(p))}</h2>
    ${list.length ? `<div class="histlist">${list.map(v => `<button data-rev="${v.rev}">${new Date(v.at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</button>`).join('')}</div><div class="histprev" id="histPrev"><p class="empty-note">Pick a time to see that version.</p></div>` : '<p class="empty-note">No earlier versions yet. A version is kept at most every 5 minutes while you write.</p>'}
    <div class="row" style="display:flex;gap:8px;justify-content:flex-end"><button class="btn" id="histClose">Close</button><button class="btn primary" id="histRestore" disabled>Put this version back</button></div></div>`;
  document.body.appendChild(box);
  let chosen = null;
  const close = () => box.remove();
  box.addEventListener('click', async e => {
    if (e.target === box || e.target.id === 'histClose') return close();
    const b = e.target.closest('[data-rev]');
    if (b) {
      box.querySelectorAll('[data-rev]').forEach(x => x.setAttribute('aria-pressed', x === b));
      const v = await api('GET', `/_/api/versions/${p.id}/${b.dataset.rev}`).catch(() => null);
      if (!v || !v.ok) return;
      chosen = v.data.lines;
      const keepEdit = S.edit; S.edit = -1;
      $('#histPrev').innerHTML = bodyHtml({ id: 'hist', lines: chosen });
      S.edit = keepEdit;
      $('#histRestore').disabled = false;
    }
    if (e.target.id === 'histRestore' && chosen) {
      if (S.edit >= 0) commit();
      snapshot(p); p.lines = chosen.slice(); save(p); S.edit = -1; close(); renderPage(p); toast('The earlier version is back. The one before it is kept in history.');
    }
  });
  document.addEventListener('keydown', function esc1(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc1); } });
}
function askDelete(p) {
  const tools = $('#pagetools'); if (!tools) return;
  tools.innerHTML = `<span>Delete this page?</span><button class="danger" id="delYes">Delete</button><button id="delNo">Cancel</button>`;
  $('#delNo').onclick = () => renderPage(p);
  $('#delYes').onclick = async () => {
    if (S.edit >= 0) { commit(); S.edit = -1; }
    dirty.delete(p.id);
    if (p.saved) { const r = await api('DELETE', '/_/api/page/' + p.id).catch(() => null); if (!r || !r.ok) { toast('It could not be deleted. Check the connection and try again.'); return renderPage(p); } }
    pages = pages.filter(x => x !== p); dropPending(p.id);
    nav('/'); toast('Deleted. Its earlier versions stay on the server.');
  };
}
function matchQ(p, q) {
  return (title(p) + ' ' + (p.descr || '')).toLowerCase().includes(q) || (S.searchQ === q && S.searchIds.has(p.id));
}
let searchT;
function serverSearch(q) {
  clearTimeout(searchT);
  if (!q) return;
  searchT = setTimeout(async () => {
    const r = await api('GET', '/_/api/search?q=' + encodeURIComponent(q)).catch(() => null);
    if (!r || !r.ok || S.q.trim().toLowerCase() !== q) return;
    S.searchQ = q; S.searchIds = new Set(r.data.ids); if (location.pathname === '/') renderHome();
  }, 220);
}

/* Events */
document.addEventListener('click', e => {
  if (e.target.closest('.selpop') || e.target.closest('.histpop')) return;
  const navA = e.target.closest('a[data-nav], a[href^="/"]');
  if (navA && !navA.target && !navA.hasAttribute('data-go') && !navA.hasAttribute('data-new') && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && !navA.getAttribute('href').startsWith('/files/')) {
    e.preventDefault(); return nav(navA.getAttribute('data-nav') || navA.getAttribute('href'));
  }
  const sb = e.target.closest('[data-share]');
  if (sb) { if (sb.getAttribute('aria-disabled') !== 'true') openShare(sb); return; }
  const cl = e.target.closest('[data-copylink]');
  if (cl) { const u = cl.dataset.copylink; closeShare(); if (navigator.clipboard) navigator.clipboard.writeText(u).then(() => toast('Link copied.'), () => toast(u)); else toast(u); return; }
  if (!e.target.closest('.sharepop')) closeShare();
  if (e.target.closest('[data-me]')) {
    const me = $('#me'); if (!me) return;
    me.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
    me.classList.remove('flash'); void me.offsetWidth; me.classList.add('flash'); me.focus({ preventScroll: true });
    return;
  }
  const gs = e.target.closest('[data-gstyle]');
  if (gs) {
    S.gstyle = gs.dataset.gstyle; store(KEY + '-gstyle', S.gstyle);
    document.querySelectorAll('[data-gstyle]').forEach(b => b.setAttribute('aria-pressed', b.dataset.gstyle === S.gstyle));
    if (location.pathname === '/_/graph') mountMain(); else { const p = S.cur && byId(S.cur); if (p) { S.graphSig = ''; renderRelated(p); } }
    return;
  }
  if (e.target.closest('.gbox')) return;
  const a = e.target.closest('[data-go],[data-new],[data-home],[data-fit],[data-another]');
  if (a) {
    if (e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    if (a.dataset.fit) return fit(a.dataset.fit);
    if (a.dataset.another) { const k = a.dataset.another; const cur = S.stray[k]; let tries = 0; do { delete S.stray[k]; strayFor(k === 'home' ? null : byId(k)); tries++; } while (S.stray[k] === cur && tries < 8); return k === 'home' ? renderHome() : renderPage(byId(k)); }
    if (a.hasAttribute('data-home')) return nav('/');
    if (a.dataset.go) return go(a.dataset.go);
    if (a.dataset.new) { if (S.visitor) return; return newPage(a.dataset.new); }
    return;
  }
  if (e.target.closest('a[href]') || e.target.closest('.html-prev') || e.target.closest('.fbox')) return;
  const line = e.target.closest('#body .line');
  if (line && !e.target.closest('#ed') && !S.visitor) {
    const i = +line.dataset.i;
    if (e.shiftKey && (S.edit > 0 || S.range) && i > 0) { const from = S.edit > 0 ? S.edit : S.range[0]; setRange(from, i); return; }
    startEdit(i); return;
  }
  const body = e.target.closest('#body');
  if (body && !S.visitor && !line) {
    const p = byId(S.cur); if (!p || !p.lines) return;
    if (p.lines[p.lines.length - 1].trim()) p.lines.push('');
    startEdit(p.lines.length - 1);
    return;
  }
  if (S.range && !e.target.closest('#body')) { S.range = null; rerenderBody(); }
  if (!e.target.closest('#help') && !e.target.closest('#helpBtn')) { $('#help').hidden = true; $('#helpBtn').setAttribute('aria-expanded', 'false'); }
});
document.addEventListener('keydown', e => {
  if (e.target.closest && e.target.closest('input,textarea')) {
    if (e.target.id === 'q' && e.key === 'Enter') { const t = S.q.trim().toLowerCase(); const hit = visPages().filter(p => matchQ(p, t)); if (hit.length === 1) go(hit[0].id); else if (!hit.length && t && OWNER && !S.visitor) newPage(S.q.trim()); }
    if (e.target.id === 'q' && e.key === 'Escape') { e.target.value = ''; S.q = ''; e.target.blur(); renderHome(); }
    return;
  }
  if ($('.histpop')) return;
  if (S.range) {
    if (e.key === 'Escape') { e.preventDefault(); return rangeAct('x'); }
    if (e.key === 'Backspace' || e.key === 'Delete') { e.preventDefault(); return rangeAct('del'); }
    if (e.key === 'Tab') { e.preventDefault(); return rangeAct(e.shiftKey ? 'out' : 'in'); }
  }
  const p = S.cur && byId(S.cur);
  if (p && !S.visitor && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); return undoRedo(p, !e.shiftKey); }
  if (e.key === '/' || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'g')) { e.preventDefault(); $('#q').focus(); }
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('[data-go][tabindex]')) { e.preventDefault(); go(e.target.dataset.go); }
});
$('#q').addEventListener('input', e => { S.q = e.target.value; if (location.pathname !== '/') { history.pushState(null, '', '/'); dropGraph(); } renderHome(); serverSearch(S.q.trim().toLowerCase()); });
const on = (id, fn) => { const el = $('#' + id); if (el) el.onclick = fn; };
on('asMe', () => setVisitor(false));
on('asVisitor', () => setVisitor(true));
on('newPage', () => newPage(''));
on('lookBtn', () => setLook(S.look === 'ink' ? 'paper' : 'ink'));
document.documentElement.dataset.look = S.look; $('#lookBtn').setAttribute('aria-pressed', S.look === 'ink');
on('helpBtn', () => { const h = $('#help'); h.hidden = !h.hidden; $('#helpBtn').setAttribute('aria-expanded', String(!h.hidden)); });
const cssBtn = $('#cssToggle');
const drawCssBtn = () => { if (!cssBtn) return; cssBtn.setAttribute('aria-pressed', S.css); cssBtn.textContent = 'Custom CSS: ' + (S.css ? 'on' : 'off'); };
drawCssBtn();
on('cssToggle', () => { S.css = !S.css; store(KEY + '-css', S.css); drawCssBtn(); applyUserCss(); if (S.cur) rerenderBody(); toast(S.css ? 'Custom CSS is on.' : 'Custom CSS is off. Use this if a style breaks the page.'); });
on('openSettings', () => { $('#help').hidden = true; const s = byTitle('settings'); if (s) go(s.id); else newPage('settings'); });
on('exportBtn', async () => {
  const r = await api('GET', '/_/api/export').catch(() => null);
  if (!r || !r.ok) return toast('The download could not be made. Try again.');
  const url = URL.createObjectURL(new Blob([JSON.stringify(r.data, null, 1)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = `rhizomebox-${new Date().toISOString().slice(0, 10)}.json`; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
});
on('signOut', async () => { if (S.edit >= 0) commit(); await flush(); await api('POST', '/_/api/logout').catch(() => null); location.href = '/'; });
window.addEventListener('popstate', () => { if (S.edit >= 0) { commit(); S.edit = -1; } leavePage(); route(); });
let rT; window.addEventListener('resize', () => { clearTimeout(rT); rT = setTimeout(() => { if (S.graph) S.graph.resize(); }, 150); });

async function start() {
  try { const r = await api('GET', '/_/api/index'); if (r.ok) pages = r.data.pages.map(e => Object.assign(e, { saved: true })); } catch (e) {}
  if (BOOT.page) {
    const p = byId(BOOT.page.id);
    if (p) Object.assign(p, { lines: BOOT.page.lines, rev: BOOT.page.rev });
    else pages.push(Object.assign({}, BOOT.page, { saved: true }));
  }
  if (OWNER) restorePending();
  route();
}
start();
})();
