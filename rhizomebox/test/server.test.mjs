import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeEnv } from './helpers.mjs';
import { safeTarget, readMeta } from '../src/app.js';

const PRIV = 'secret plan 2027';
async function setup() {
  const t = makeEnv();
  const tok = await t.signIn();
  const put = (id, lines, extra = {}) => t.call('PUT', '/_/api/page/' + id, { body: { lines, ...extra }, cookie: tok, headers: t.W });
  // a private page with a private picture and a private web link
  await t.env.FILES.put('priv.png', Buffer.from('p'), { httpMetadata: { contentType: 'image/png' } });
  await t.env.FILES.put('pub.png', Buffer.from('q'), { httpMetadata: { contentType: 'image/png' } });
  assert.equal((await put('priv0001', [PRIV, '[/files/priv.png]', 'about [リゾーム]', '[secret site https://secret.example.com/x]'])).status, 200);
  assert.equal((await put('pub00001', ['リゾーム', '[/files/pub.png]', 'links to [' + PRIV + '] and [誤配]', '[wiki https://en.wikipedia.org/wiki/Rhizome]'], { pub: true })).status, 200);
  assert.equal((await put('pub00002', ['誤配', 'back to [リゾーム]'], { pub: true })).status, 200);
  return { ...t, tok, put };
}

test('sign-in: code only to the owner, wrong codes run out, session cookie', async () => {
  const t = makeEnv();
  const r1 = await t.call('POST', '/_/api/login', { body: { email: 'stranger@example.com' }, headers: t.W });
  assert.equal(r1.status, 200);
  assert.equal(t.mails.length, 0, 'no mail for a stranger, same answer');
  await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, headers: t.W });
  assert.equal(t.mails.length, 1);
  const code = t.mails[0].subject.match(/(\d{6})/)[1];
  const wrong = code === '000000' ? '111111' : '000000';
  for (let i = 0; i < 5; i++) assert.equal((await t.call('POST', '/_/api/verify', { body: { code: wrong }, headers: t.W })).status, 400);
  const late = await t.call('POST', '/_/api/verify', { body: { code }, headers: t.W });
  assert.equal(late.status, 400, 'the right code no longer works after 5 wrong tries');
  const tok = await t.signIn();
  assert.match(tok, /^[0-9a-f]{64}$/);
  const me = await (await t.call('GET', '/_/api/me', { cookie: tok })).json();
  assert.equal(me.owner, true);
});

test('writes need the header and a session', async () => {
  const t = await setup();
  assert.equal((await t.call('PUT', '/_/api/page/abc12345', { body: { lines: ['x'] }, cookie: t.tok })).status, 403);
  assert.equal((await t.call('PUT', '/_/api/page/abc12345', { body: { lines: ['x'] }, headers: t.W })).status, 401);
  assert.equal((await t.call('DELETE', '/_/api/page/pub00001', { headers: t.W })).status, 401);
  assert.equal((await t.call('POST', '/_/api/files', { body: Buffer.from('x'), raw: true, headers: { ...t.W, 'Content-Type': 'image/png' } })).status, 401);
});

test('a private page never reaches a visitor', async () => {
  const t = await setup();
  const idx = await (await t.call('GET', '/_/api/index')).json();
  assert.deepEqual(idx.pages.map((p) => p.title).sort(), ['リゾーム', '誤配']);
  assert.ok(!JSON.stringify(idx).includes('priv.png'));
  assert.equal((await t.call('GET', '/_/api/page/priv0001')).status, 404);
  assert.deepEqual((await (await t.call('GET', '/_/api/search?q=secret.example')).json()).ids, [], 'words only on the private page are not found');
  assert.deepEqual((await (await t.call('GET', '/_/api/search?q=secret.example', { cookie: t.tok })).json()).ids, ['priv0001']);
  assert.equal((await t.call('GET', '/' + encodeURIComponent(PRIV).replace(/%20/g, '_'))).status, 404);
  const home = await (await t.call('GET', '/')).text();
  assert.ok(!home.includes('secret plan'));
  const page = await (await t.call('GET', '/' + encodeURIComponent('リゾーム'))).text();
  assert.ok(page.includes('secret plan 2027'), 'the words written on a public page are public');
  assert.ok(!page.includes('href="/secret'), 'but they are not a link to the private page');
  assert.ok(!page.includes('priv.png'));
  assert.equal((await t.call('GET', '/files/priv.png')).status, 404);
  assert.equal((await t.call('GET', '/files/pub.png')).status, 200);
  assert.equal((await t.call('GET', '/files/priv.png', { cookie: t.tok })).status, 200);
  const meta = await (await t.call('GET', '/_/api/linkmeta?u=' + encodeURIComponent('https://secret.example.com/x'))).json();
  assert.deepEqual(meta, {});
  assert.equal((await t.call('GET', '/sitemap.xml')).status, 200);
  const sm = await (await t.call('GET', '/sitemap.xml')).text();
  assert.ok(!sm.includes(encodeURIComponent('secret')));
  assert.equal((await t.call('GET', '/_/api/export')).status, 401);
  const ex = await (await t.call('GET', '/_/api/export', { headers: { Authorization: 'Bearer ' + 'k'.repeat(40) } })).json();
  assert.equal(ex.pages.length, 3);
  // the owner sees everything
  const own = await (await t.call('GET', '/_/api/index', { cookie: t.tok })).json();
  assert.equal(own.pages.length, 3);
});

test('turning a page public shows it at once; unpublishing hides it again', async () => {
  const t = await setup();
  const full = await (await t.call('GET', '/_/api/page/priv0001', { cookie: t.tok })).json();
  await t.put('priv0001', full.page.lines, { pub: true, baseRev: full.page.rev });
  assert.equal((await t.call('GET', '/_/api/page/priv0001')).status, 200);
  assert.equal((await t.call('GET', '/files/priv.png')).status, 200);
  const again = await (await t.call('GET', '/_/api/page/priv0001', { cookie: t.tok })).json();
  await t.put('priv0001', again.page.lines, { pub: false, baseRev: again.page.rev });
  assert.equal((await t.call('GET', '/_/api/page/priv0001')).status, 404);
});

test('two devices: an old copy cannot overwrite a newer one', async () => {
  const t = await setup();
  const r = await t.put('pub00002', ['誤配', 'from the Mac'], { baseRev: 1 });
  assert.equal(r.status, 200);
  const stale = await t.put('pub00002', ['誤配', 'from the phone'], { baseRev: 1 });
  assert.equal(stale.status, 409);
  const body = await stale.json();
  assert.equal(body.error, 'conflict');
  assert.deepEqual(body.page.lines, ['誤配', 'from the Mac']);
});

test('titles: unique, and a rename carries the links along', async () => {
  const t = await setup();
  assert.equal((await t.put('dup00001', ['りぞーむ'.replace('りぞーむ', 'リゾーム'), 'x'])).status, 409);
  const cur = await (await t.call('GET', '/_/api/page/pub00002', { cookie: t.tok })).json();
  const r = await t.put('pub00002', ['Gohai', ...cur.page.lines.slice(1)], { baseRev: cur.page.rev });
  assert.equal(r.status, 200);
  const other = await (await t.call('GET', '/_/api/page/pub00001', { cookie: t.tok })).json();
  assert.ok(other.page.lines.join('\n').includes('[Gohai]'));
  assert.ok(!other.page.lines.join('\n').includes('[誤配]'));
  assert.equal((await t.call('GET', '/Gohai')).status, 200);
});

test('versions are kept and can be read back', async () => {
  const t = await setup();
  const cur = await (await t.call('GET', '/_/api/page/pub00002', { cookie: t.tok })).json();
  await t.put('pub00002', ['誤配', 'second'], { baseRev: cur.page.rev });
  const v = await (await t.call('GET', '/_/api/versions/pub00002', { cookie: t.tok })).json();
  assert.equal(v.versions.length, 1);
  const old = await (await t.call('GET', `/_/api/versions/pub00002/${v.versions[0].rev}`, { cookie: t.tok })).json();
  assert.deepEqual(old.lines, ['誤配', 'back to [リゾーム]']);
  assert.equal((await t.call('GET', '/_/api/versions/pub00002')).status, 401);
});

test('server page: share card, related pages, stage is hidden from search engines', async () => {
  const t = await setup();
  const h = await (await t.call('GET', '/' + encodeURIComponent('リゾーム'))).text();
  assert.match(h, /<meta property="og:title" content="リゾーム">/);
  assert.match(h, /hashtags=TatsukiMitsuhashi%2CNotes%2CTANYLLC/);
  assert.match(h, /href="\/%E8%AA%A4%E9%85%8D"/, 'related page is a real link');
  assert.match(h, /buymeacoffee\.com\/tatsuki3284/);
  const t2 = makeEnv({ STAGE: '1' });
  assert.match(await (await t2.call('GET', '/robots.txt')).text(), /Disallow: \//);
  assert.match(await (await t2.call('GET', '/')).text(), /noindex/);
});

test('images: only pictures, size limit, stored under a random name', async () => {
  const t = await setup();
  const bad = await t.call('POST', '/_/api/files', { body: Buffer.from('<svg/>'), raw: true, cookie: t.tok, headers: { ...t.W, 'Content-Type': 'image/svg+xml' } });
  assert.equal(bad.status, 415, 'SVG can carry scripts, so it is refused');
  const ok = await t.call('POST', '/_/api/files', { body: Buffer.from([1, 2, 3]), raw: true, cookie: t.tok, headers: { ...t.W, 'Content-Type': 'image/png' } });
  const j = await ok.json();
  assert.match(j.url, /^\/files\/[a-z0-9]+\.png$/);
});

test('link previews: never fetch private or local addresses; read titles and images', () => {
  for (const u of ['http://localhost/x', 'http://127.0.0.1/', 'http://10.0.0.1/', 'http://[::1]/', 'http://router/', 'https://a.local/x', 'ftp://example.com/', 'https://user:pw@example.com/', 'https://example.com:8080/']) assert.equal(safeTarget(u), false, u);
  assert.equal(safeTarget('https://en.wikipedia.org/wiki/Rhizome'), true);
  const m = readMeta('<html><head><title>Plain &amp; title</title><meta property="og:image" content="/img/a.jpg"><meta property="og:site_name" content="Site"></head>', 'https://example.com/p');
  assert.deepEqual(m, { title: 'Plain & title', image: 'https://example.com/img/a.jpg', site: 'Site' });
});
