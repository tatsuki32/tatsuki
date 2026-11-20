// Rhizome Box notation: Scrapbox-style brackets, read the same way on the server (pages for search engines
// and share cards) and in the browser (the editor). Pure functions only: no DOM, no network.

export const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
export const indOf = (l) => l.match(/^\s*/)[0].length;
export const isUrl = (u) => /^https?:\/\/[^\s<>"]+$/.test(u);
export const FILE_PATH = '/files/';
// An image: a URL ending in a picture type, or a file stored in this notebook.
export const isImg = (u) => /^https?:\/\/\S+\.(png|jpe?g|gif|webp|svg|avif)(\?\S*)?$/i.test(u) || /^(https?:\/\/[^/\s]+)?\/files\/[\w.-]+$/.test(u);
const DECO = /^[!"#%&'()*+,\-./{|}<>_~$]+\s/;

/* Walk the body lines (title excluded), skipping the inside of code and table blocks. */
function eachText(lines, fn) {
  let blk = -1;
  for (let i = 1; i < lines.length; i++) {
    const raw = lines[i] || '';
    const ind = indOf(raw), t = raw.slice(ind);
    if (blk >= 0 && ind > blk && raw.trim()) continue;
    blk = -1;
    if (/^(code|table):/.test(t)) { blk = ind; continue; }
    fn(t, i);
  }
}

/* Page links, tags and icons: [page], #tag, [page.icon]. Not code, formulas, URLs, images, other notebooks or [[bold]]. */
export function linksOf(lines) {
  const out = [], seen = new Set();
  const add = (w) => { const k = w.toLowerCase(); if (!seen.has(k)) { seen.add(k); out.push(w); } };
  eachText(lines, (t0) => {
    const t = t0.replace(/`[^`]*`/g, '').replace(/\[\[[^\[\]]+\]\]/g, '');
    for (const m of t.matchAll(/\[([^\[\]]+)\]/g)) {
      let w = m[1].trim();
      if (!w || DECO.test(w) || /https?:\/\//.test(w) || w.startsWith('/')) continue;
      const ic = w.match(/^(.+)\.icon(?:\*\d+)?$/); if (ic) w = ic[1];
      add(w);
    }
    for (const m of t.matchAll(/(?:^|\s)#([^\s\[\]#]+)/g)) add(m[1]);
  });
  return out;
}

/* Outside links with their labels: [label url], [url label], [url], and bare URLs. Images are left out. */
export function webLinksOf(lines) {
  const out = [];
  eachText(lines, (t) => {
    const clean = t.replace(/`[^`]*`/g, '');
    let rest = clean;
    for (const m of clean.matchAll(/\[\[?([^\[\]]+)\]?\]/g)) {
      rest = rest.replace(m[0], ' ');
      const c = m[1].trim();
      if (DECO.test(c)) continue;
      const parts = c.split(/\s+/);
      const urls = parts.filter(isUrl).filter((u) => !isImg(u));
      if (!urls.length) continue;
      const label = parts.filter((x) => !isUrl(x)).join(' ');
      urls.forEach((url) => out.push({ url, label }));
    }
    for (const m of rest.matchAll(/https?:\/\/[^\s\]<>"]+/g)) if (!isImg(m[0])) out.push({ url: m[0], label: '' });
  });
  return out;
}

/* Every image on the page, in order. The first one is the page's picture. */
export function imagesOf(lines) {
  const out = [];
  eachText(lines, (t) => {
    for (const m of t.matchAll(/\[\[?([^\[\]]+)\]?\]/g)) m[1].trim().split(/\s+/).filter(isImg).forEach((u) => out.push(u));
  });
  return out;
}

export const plain = (s) => s
  .replace(/\[\[([^\[\]]+)\]\]/g, '$1')
  .replace(/\[([!"#%&'()*+,\-./{|}<>_~$]+\s+)?([^\[\]]+)\]/g, (a, b, c) => (isUrl(c.trim()) || /\.icon(\*\d+)?$/.test(c) || isImg(c.trim()) ? '' : c))
  .replace(/`/g, '');

/* Readable lines of a page, without code and table blocks. */
export function textLines(lines) {
  const out = [];
  eachText(lines, (t) => { const s = plain(t.trim()).trim(); if (s) out.push(s); });
  return out;
}

/* The bodies of code blocks with a given file name (all code blocks when no name). */
export function codeBlocks(lines, name) {
  const out = []; let blk = null;
  for (const raw of lines) {
    const ind = indOf(raw || '');
    if (blk && ind > blk.ind && raw.trim()) { blk.lines.push(raw.slice(blk.ind + 1)); continue; }
    if (blk && !raw.trim()) { blk.lines.push(''); continue; }
    blk = null;
    const m = (raw || '').slice(ind).match(/^code:(.+)$/);
    if (m && (!name || m[1].trim() === name)) { blk = { ind, lines: [] }; out.push(blk); }
  }
  return out.map((b) => b.lines.join('\n').replace(/\n+$/, ''));
}

/* What the page list, the graph and the lists need, computed once when a page is saved. */
export function derive(lines) {
  const text = textLines(lines);
  const images = imagesOf(lines);
  return {
    title: (lines[0] || '').trim(),
    links: linksOf(lines),
    image: images[0] || '',
    images,
    web: webLinksOf(lines),
    descr: text.slice(0, 6).join(' ').slice(0, 400),
    css: codeBlocks(lines, 'style.css').join('\n'),
  };
}

/* A line that is only a YouTube link (bare, [label url] or [url label]) → the URL, else ''. */
export function ytLine(t) {
  const s = t.trim();
  const m = s.match(/^(https?:\/\/\S+)$/) || s.match(/^\[[^\[\]]*?(https?:\/\/[^\s\]]+)[^\[\]]*\]$/);
  if (!m) return '';
  try { const h = new URL(m[1]).hostname.replace(/^www\./, ''); return /(^|\.)youtube\.com$|^youtu\.be$/.test(h) ? m[1] : ''; } catch (e) { return ''; }
}

/* Inline notation → HTML. ctx decides how page links, icons, images and web links look:
   ctx.link(title, label), ctx.icon(name, count), ctx.img(url, cls), ctx.ext(url, label). */
export function makeInline(ctx) {
  const ext = ctx.ext || ((u, l) => `<a class="ext" href="${esc(u)}" target="_blank" rel="noopener">${esc(l)}</a>`);
  function bracket(c) {
    let d;
    if ((d = c.match(/^\$\s+(.+)$/))) return `<span class="formula">${esc(d[1])}</span>`;
    if ((d = c.match(/^([!"#%&'()*+,\-./{|}<>_~]+)\s+(.+)$/))) {
      const sy = d[1], lv = (sy.match(/\*/g) || []).length;
      const cls = ['deco', ...new Set([...sy].filter((ch) => ch !== '*').map((ch) => 'deco-' + ch))];
      const inner = inl(d[2]);
      return `<span class="${esc(cls.join(' '))}">${lv ? `<strong class="level-${Math.min(lv, 5)}">${inner}</strong>` : inner}</span>`;
    }
    if ((d = c.match(/^(\/[^\/\s]+\/)?(.+?)\.icon(?:\*(\d+))?$/))) return ctx.icon(d[2], Math.min(+(d[3] || 1), 30));
    const parts = c.trim().split(/\s+/);
    if (parts.length === 2 && isUrl(parts[0]) && isUrl(parts[1])) {
      const img = parts.find(isImg), href = parts.find((u) => u !== img);
      if (img && href) return `<a href="${esc(href)}" target="_blank" rel="noopener">${ctx.img(img, '')}</a>`;
    }
    if (parts.length === 1 && (isUrl(parts[0]) || isImg(parts[0]))) return isImg(parts[0]) ? ctx.img(parts[0], '') : ext(parts[0], parts[0]);
    if ((d = c.match(/^(https?:\/\/\S+)\s+(.+)$/)) && isUrl(d[1])) return ext(d[1], d[2]);
    if ((d = c.match(/^(.+?)\s+(https?:\/\/\S+)$/)) && isUrl(d[2])) return ext(d[2], d[1]);
    if (c.startsWith('/')) return `<a class="proj" title="A page in another notebook">${esc(c)}</a>`;
    if (!c.trim()) return esc('[' + c + ']');
    return ctx.link(c.trim(), c);
  }
  function inl(s) {
    const re = /`([^`]+)`|\[\[([^\[\]]+)\]\]|\[([^\[\]]+)\]|(^|\s)#([^\s\[\]#]+)|(https?:\/\/[^\s\]<>"]+)/g;
    let out = '', last = 0, m;
    while ((m = re.exec(s))) {
      out += esc(s.slice(last, m.index));
      if (m[1] !== undefined) out += `<code class="code">${esc(m[1])}</code>`;
      else if (m[2] !== undefined) out += isImg(m[2].trim()) ? ctx.img(m[2].trim(), 'strong-image') : `<strong>${inl(m[2])}</strong>`;
      else if (m[3] !== undefined) out += bracket(m[3]);
      else if (m[5] !== undefined) out += esc(m[4]) + ctx.link(m[5], '#' + m[5]);
      else out += ext(m[6], m[6]);
      last = re.lastIndex;
    }
    return out + esc(s.slice(last));
  }
  return inl;
}

/* A whole page as plain HTML (no editor): what search engines, share previews and the first paint see. */
export function renderStatic(lines, ctx) {
  const inl = makeInline(ctx);
  let h = '', blk = null, sec = 0;
  const flush = () => {
    if (blk && blk.k === 'code' && /\.html?$/i.test(blk.name)) {
      h += `<div class="html-prev"><div class="eb">Preview of ${esc(blk.name)}</div><iframe sandbox="" title="Preview of ${esc(blk.name)}" srcdoc="${esc(blk.body.join('\n'))}"></iframe></div>`;
    }
    blk = null;
  };
  lines.forEach((raw0, i) => {
    const raw = raw0 || '';
    const ind = indOf(raw), t = raw.slice(ind);
    if (blk && (ind <= blk.ind || !raw.trim())) flush();
    let cls = 'line', inner, pad = ind, dot = '';
    if (i === 0) { cls += ' line-title'; inner = esc(raw); h += `<h1 class="${cls}">${inner}</h1>`; return; }
    if (blk) {
      if (blk.k === 'code') { cls += ' code-block'; inner = esc(raw.slice(blk.ind + 1)) || ' '; pad = blk.ind; blk.body.push(raw.slice(blk.ind + 1)); }
      else { cls += ' table-block' + (blk.first ? ' thead' : ''); blk.first = false; pad = blk.ind;
        inner = `<span class="row">${raw.slice(blk.ind + 1).split('\t').map((c) => `<span class="cell">${inl(c) || '&nbsp;'}</span>`).join('')}</span>`; }
    } else if (t.startsWith('code:')) { const name = t.slice(5).trim(); blk = { k: 'code', ind, name, body: [] }; cls += ' code-block-start'; inner = `<span class="name">${esc(name)}</span>`; }
    else if (t.startsWith('table:')) { blk = { k: 'table', ind, first: true }; cls += ' table-block-start'; inner = `<span class="name">${esc(t.slice(6))}</span>`; }
    else if (t.startsWith('>')) { cls += ' quote'; inner = `<span class="q">${inl(t.replace(/^>\s?/, '')) || '&nbsp;'}</span>`; }
    else if (/^[$%]\s/.test(t)) { cls += ' cli'; inner = `<span class="p">${esc(t[0])}</span>${esc(t.slice(2))}`; }
    else {
      inner = inl(t) || '&nbsp;';
      if (ind === 0 && /^\[\*{2,}\s[^\]]*\]$/.test(t)) { sec++; cls += ' sec'; inner = `<span class="sec-n">${sec}</span>` + inner; }
      if (ind > 0) dot = `<span class="dot" style="left:calc(${ind - 1} * 1.5em + .45em)"></span>`;
    }
    h += `<div class="${cls}" style="padding-left:calc(${pad} * 1.5em)">${dot}${inner}</div>`;
  });
  flush();
  return h;
}
