// HTML the server sends. Every page is complete before any script runs (search engines, share previews,
// slow phones); the script in /app/app.js then takes over the same markup.

import { esc } from '../public/app/notation.js';

const SITE_NAME = 'Rhizome Box';
const AUTHOR = 'Tatsuki Mitsuhashi';
const PROFILE = 'https://tatsuki.tany.im/?lang=ja';
const X_HANDLE = 'tatsuki3284';
const COFFEE = 'https://buymeacoffee.com/tatsuki3284';
export const SHARE_TAGS = ['TatsukiMitsuhashi', 'Notes', 'TANYLLC'];
const FONTS = 'https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@1,600&family=IBM+Plex+Mono:wght@400;500&family=Shippori+Mincho+B1:wght@600;700&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap';
const pageUrl = (title) => '/' + encodeURIComponent(title).replace(/%20/g, '_');
const abs = (base, u) => (u.startsWith('/') ? base + u : u);

const XICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M17.75 3h3.07l-6.7 7.66L22 21h-6.17l-4.83-6.32L5.47 21H2.4l7.17-8.2L2 3h6.33l4.37 5.78zm-1.08 16.17h1.7L7.4 4.74H5.58z"/></svg>';
const CUPICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M4 9h12v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5zM16 10h1.5a2.5 2.5 0 0 1 0 5H16M8 3v2M12 3v2"/></svg>';

export function shareHref(base, title) {
  const url = title ? base + pageUrl(title) : base + '/';
  const text = title || 'Rhizome Box: notes that grow like roots';
  return `https://x.com/intent/post?text=${encodeURIComponent(text)}&url=${encodeURIComponent(url)}&hashtags=${encodeURIComponent(SHARE_TAGS.join(','))}`;
}

function head({ title, description, path, base, image, stage, noindex, css, type = 'website', ld }) {
  const full = title ? `${title} | ${SITE_NAME}` : `${SITE_NAME} | ${AUTHOR}`;
  const desc = (description || 'Notes that grow like roots, by Tatsuki Mitsuhashi.').slice(0, 160);
  const img = image ? abs(base, image) : base + '/og.png';
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>${esc(full)}</title>
<meta name="description" content="${esc(desc)}">
${stage || noindex ? '<meta name="robots" content="noindex, nofollow">' : ''}
<link rel="canonical" href="${esc(base + path)}">
<meta property="og:site_name" content="${SITE_NAME}"><meta property="og:type" content="${type}">
<meta property="og:title" content="${esc(title || SITE_NAME)}"><meta property="og:description" content="${esc(desc)}">
<meta property="og:url" content="${esc(base + path)}"><meta property="og:image" content="${esc(img)}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:site" content="@${X_HANDLE}"><meta name="twitter:creator" content="@${X_HANDLE}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="${FONTS}">
<link rel="stylesheet" href="/app/app.css">
<style id="usercss">${css || ''}</style>
${ld ? `<script type="application/ld+json">${JSON.stringify(ld).replace(/</g, '\\u003c')}</script>` : ''}
<script>try{var l=localStorage.getItem('rb-look');if(l==='"ink"')document.documentElement.dataset.look='ink'}catch(e){}</script>
</head><body>`;
}

function header(owner) {
  return `<header class="top"><div class="hd">
<a class="brand" href="/" data-nav="/" aria-label="Rhizome Box home"><svg class="mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3h7v2.2a2 2 0 1 0 4 0V3h7v7h-2.2a2 2 0 1 0 0 4H21v7h-7v-2.2a2 2 0 1 0-4 0V21H3v-7h2.2a2 2 0 1 0 0-4H3z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg><b>Rhizome Box</b><small>by Tatsuki Mitsuhashi</small></a>
<label class="search"><input id="q" type="search" placeholder="Search pages  /" aria-label="Search pages" autocomplete="off"></label>
${owner ? `<div class="seg" role="group" aria-label="View as"><button id="asMe" aria-pressed="true">Me</button><button id="asVisitor" aria-pressed="false">Visitor</button></div>
<button class="btn primary new" id="newPage"><span>New page</span></button>` : ''}
<button class="btn icon lookbtn" id="lookBtn" aria-pressed="false" aria-label="Switch to the dark look"><svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36A5.5 5.5 0 0 1 13.36 3.1 9.3 9.3 0 0 0 12 3z"/></svg></button>
<button class="btn icon" id="helpBtn" aria-label="Notation and keys" aria-expanded="false">?</button>
</div></header>
<div id="banner"></div>`;
}

function footer(base, title) {
  return `<footer class="foot wrap" id="foot" aria-label="Support and profile">
<div class="support">
<p class="lead">Did this page give you something? Share it, or buy me a coffee.</p>
<div class="row">
<a class="sbtn x" id="shareX" href="${esc(shareHref(base, title))}" target="_blank" rel="noopener">${XICON}Share on X</a>
<span class="sharewrap"><button class="sbtn more" type="button" data-share="home" aria-haspopup="true" aria-expanded="false">More ways to share</button></span>
<a class="sbtn coffee" id="coffee" href="${COFFEE}" target="_blank" rel="noopener">${CUPICON}Buy me a coffee</a>
</div>
<p class="note" id="shareNote" hidden>This page is private, so it can't be shared yet.</p>
<a class="follow" href="https://x.com/${X_HANDLE}" target="_blank" rel="noopener">Follow @${X_HANDLE} on X to see new pages as they grow →</a>
</div>
<p class="copy">© 2026 Tatsuki Mitsuhashi · TANY LLC · Rhizome Box. Quotes keep their sources; everything else here is mine.</p>
<section class="me" id="me" aria-label="About the author" tabindex="-1">
<a href="${PROFILE}" target="_blank" rel="noopener" aria-label="Open the profile site"><img id="meImg" src="/files/avatar.jpg" alt=""></a>
<div class="who">
<b><a href="${PROFILE}" target="_blank" rel="noopener" class="mename">Tatsuki Mitsuhashi</a></b>
<span class="roles">空白デザイナー · 多言語間翻訳作家 · 卒成長経営者</span>
<p>夜寝る前に「明日も楽しみだな」と思える人を、世界に増やしたい。ノマド生活は5年目、訪れた国は50か国を超えました。ZEN大学で学びながら、日本語・英語・スペイン語・ロシア語で考えたことを、ここにつないでいます。</p>
<div class="links"><a href="/tatsuki" data-nav="/tatsuki">Profile page</a><a href="${PROFILE}" target="_blank" rel="noopener">Full profile ↗</a><a href="https://x.com/${X_HANDLE}" target="_blank" rel="noopener">X</a><a href="https://tany.im/campus/" target="_blank" rel="noopener">Newsletter</a></div>
</div>
</section>
</footer>`;
}

function help(owner) {
  return `<div class="pop" id="help" hidden>
<h3>Links</h3><dl>
<dt>[page]</dt><dd>Link a page. It does not need to exist yet</dd>
<dt>#tag</dt><dd>Same as a link</dd>
<dt>[text https://…]</dt><dd>Web link. [https://… text] also works</dd>
<dt>[/notebook/page]</dt><dd>A page in another notebook</dd>
<dt>[tatsuki.icon]</dt><dd>That page's picture at text size. [tatsuki.icon*3] repeats it</dd>
<dt>[https://….png]</dt><dd>Image. [[image]] shows it full width. The first image is the page's picture</dd>
</dl>
<h3>Text</h3><dl>
<dt>[* bold]</dt><dd>[** ] to [***** ] grow bigger. [** ] at line start is a numbered heading</dd>
<dt>[[bold]]</dt><dd>Bold</dd>
<dt>[/ italic]</dt><dd>[- strike]. Combine: [-/* text]</dd>
<dt>[! text]</dt><dd>Any symbol works. It becomes the class deco-! for your CSS</dd>
<dt>\`code\`</dt><dd>Inline code. [$ x^2] is a formula</dd>
<dt>&gt; text</dt><dd>Quote. $ or % at line start is a command line</dd>
<dt>code:a.js</dt><dd>Code block. code:a.html shows a live preview</dd>
<dt>table:name</dt><dd>Table. Separate cells with Tab</dd>
</dl>
${owner ? `<h3>Keys</h3><dl>
<dt>select + [</dt><dd>Make the selection a link. * / - ! % | ~ decorate it</dd>
<dt>Shift+click</dt><dd>Select several lines, then split them into a new page</dd>
<dt>[ … Tab</dt><dd>Pick a suggestion. Enter links, Ctrl+I makes an icon</dd>
<dt>Ctrl+I</dt><dd>Insert my icon. Press again to repeat it</dd>
<dt>Ctrl+T</dt><dd>Insert the date. Press again to change the format</dd>
<dt>Ctrl+← →</dt><dd>Outdent and indent. Tab and Shift+Tab also work</dd>
<dt>Ctrl+↑ ↓</dt><dd>Move the line. Alt+↑ ↓ moves it with its children</dd>
<dt>⌘Z / ⌘⇧Z</dt><dd>Undo and redo</dd>
<dt>/ or Ctrl+G</dt><dd>Search. Esc stops editing</dd>
</dl>
<h3>Look and feel</h3>
<p>The code:style.css block on the page <b>settings</b> styles the whole notebook. The one on your own page <b>tatsuki</b> styles it for you only.</p>
<div class="row"><button class="btn sm" id="cssToggle" aria-pressed="true">Custom CSS: on</button><button class="btn sm" id="openSettings">Open settings</button><button class="btn sm" id="exportBtn">Download all pages</button><button class="btn sm" id="signOut">Sign out</button></div>` : ''}
</div>
<div class="toast" id="toast" role="status" hidden></div>`;
}

function tail(boot) {
  return `<script id="boot" type="application/json">${JSON.stringify(boot).replace(/</g, '\\u003c')}</script>
<script type="module" src="/app/app.js"></script>
</body></html>`;
}

const fileSrc = (u) => String(u || '').replace(/^https?:\/\/[^/]+(\/files\/)/, '$1');
function card(p) {
  return `<li class="${p.pin ? 'pinned' : ''}"><a class="card${p.pub ? '' : ' priv'}${p.pin ? ' pin' : ''}" href="${esc(pageUrl(p.title))}"><span class="t">${esc(p.title)}</span>${p.image ? `<span class="thumb"><img src="${esc(fileSrc(p.image))}" alt="" loading="lazy"></span>` : `<span class="s">${esc(p.descr || '')}</span>`}</a></li>`;
}

export function homeHtml({ owner, stage, base, css, me, pages }) {
  return head({ path: '/', base, stage, css, description: 'Notes that grow like roots: thinking in Japanese, English, Spanish and Russian, linked page to page.',
    ld: { '@context': 'https://schema.org', '@type': 'WebSite', name: SITE_NAME, url: base + '/', author: { '@type': 'Person', name: AUTHOR, url: PROFILE } } })
    + header(owner)
    + `<main class="wrap" id="main"><nav class="tabs" aria-label="Views"><a href="/" aria-current="page">Pages</a><a href="/_/links">Links</a><a href="/_/web">Web links</a><a href="/_/images">Images</a><a href="/_/graph">Graph</a></nav>
<ul class="grid">${pages.map(card).join('')}</ul></main>`
    + footer(base, '') + help(owner) + tail({ owner, stage, base, me, view: 'home' });
}

export function pageHtml({ owner, stage, base, css, me, page, body, related }) {
  const isMe = page.title.toLowerCase() === me;
  const ld = page.pub ? { '@context': 'https://schema.org', '@type': isMe ? 'ProfilePage' : 'Article', headline: page.title, dateModified: new Date(page.updated).toISOString(),
    author: { '@type': 'Person', name: AUTHOR, url: PROFILE }, publisher: { '@type': 'Organization', name: 'TANY LLC' }, mainEntityOfPage: base + pageUrl(page.title) } : null;
  return head({ title: page.title, description: page.descr, path: pageUrl(page.title), base, image: page.pub ? page.image : '', stage, noindex: !page.pub, css, type: 'article', ld })
    + header(owner)
    + `<main class="wrap" id="main"><div class="crumb"><a href="/" data-nav="/">← All pages</a></div>
<article class="page${isMe ? ' profile' : ''}"><div id="body">${body}</div>
<div class="pfoot"><div class="row"><a class="sbtn x sm" href="${esc(shareHref(base, page.title))}" target="_blank" rel="noopener">${XICON}Share on X</a><a class="sbtn coffee sm" href="${COFFEE}" target="_blank" rel="noopener">${CUPICON}Buy me a coffee</a></div>
<a class="acard" href="${PROFILE}" target="_blank" rel="noopener"><img src="/files/avatar.jpg" alt=""><span class="who"><span class="eb">About me</span><b>Tatsuki Mitsuhashi</b><span class="intro">夜寝る前に「明日も楽しみだな」と思える人を、世界に増やしたい。</span><span class="roles">空白デザイナー · 多言語間翻訳作家 · 卒成長経営者</span></span><span class="go">Profile site ↗</span></a>
<p class="copy">© 2026 Tatsuki Mitsuhashi · TANY LLC · Rhizome Box</p></div></article>
${related.length ? `<section class="conn" aria-label="Connections"><div class="conn-head"><h2>Connections</h2></div><ul class="grid rgrid">${related.map((p) => card({ ...p, pin: 0 })).join('')}</ul></section>` : ''}
</main>`
    + footer(base, page.pub ? page.title : '') + help(owner) + tail({ owner, stage, base, me, view: 'page', page });
}

export function shellHtml({ owner, stage, base, css, me, view, newTitle }) {
  const names = { links: 'Links', web: 'Web links', images: 'Images', graph: 'Graph', login: 'Sign in', new: newTitle };
  return head({ title: names[view] || '', path: view === 'new' ? pageUrl(newTitle) : '/_/' + view, base, stage, noindex: true, css })
    + header(owner) + `<main class="wrap" id="main"><p class="empty-note">Loading…</p></main>`
    + footer(base, '') + help(owner) + tail({ owner, stage, base, me, view, newTitle: newTitle || '' });
}

export function notFoundHtml({ owner, stage, base, css, me }) {
  return head({ title: 'Not found', path: '/', base, stage, noindex: true, css })
    + header(owner) + `<main class="wrap" id="main"><div class="crumb"><a href="/">← All pages</a></div><p class="empty-note">There is no page here. It may have moved, or it may not be public.</p></main>`
    + footer(base, '') + help(owner) + tail({ owner, stage, base, me, view: 'missing' });
}
