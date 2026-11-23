import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeEnv } from './helpers.mjs';
import { safeTarget, readMeta, visitorNet } from '../src/app.js';

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
  const r2 = await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, headers: t.W });
  assert.equal(t.mails.length, 1);
  assert.equal(await r2.text(), await r1.text());
  assert.equal(r2.headers.get('Set-Cookie').replace(/rbq=[0-9a-f]+/, ''), r1.headers.get('Set-Cookie').replace(/rbq=[0-9a-f]+/, ''), 'the cookie looks the same too');
  const code = t.mails[0].subject.match(/(\d{6})/)[1];
  const wrong = code === '000000' ? '111111' : '000000';
  const ask = t.askOf(r2);
  for (let i = 0; i < 5; i++) assert.equal((await t.call('POST', '/_/api/verify', { body: { code: wrong }, ask, headers: t.W })).status, 400);
  const late = await t.call('POST', '/_/api/verify', { body: { code }, ask, headers: t.W });
  assert.equal(late.status, 400, 'the right code no longer works after 5 wrong tries');
  const tok = await t.signIn();
  assert.match(tok, /^[0-9a-f]{64}$/);
  const me = await (await t.call('GET', '/_/api/me', { cookie: tok })).json();
  assert.equal(me.owner, true);
});

test('sign-in: a stranger can neither use, burn nor block the owner\'s code', async () => {
  const t = makeEnv();
  const ip = (n) => ({ ...t.W, 'CF-Connecting-IP': n });
  const a = await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, headers: ip('1.1.1.1') });
  const code = t.mails[0].subject.match(/(\d{6})/)[1];
  const st = await t.call('POST', '/_/api/login', { body: { email: 'x@example.com' }, headers: ip('2.2.2.2') });
  assert.equal((await t.call('POST', '/_/api/verify', { body: { code }, headers: ip('2.2.2.2') })).status, 400, 'no browser mark');
  for (let i = 0; i < 8; i++) assert.equal((await t.call('POST', '/_/api/verify', { body: { code }, ask: t.askOf(st), headers: ip('2.2.2.2') })).status, 400, 'the right code in another browser');
  let last; for (let i = 0; i < 12; i++) last = await t.call('POST', '/_/api/login', { body: { email: `s${i}@example.com` }, headers: ip('2.2.2.2') });
  assert.equal(last.status, 429, 'the stranger is slowed down');
  const ok = await t.call('POST', '/_/api/verify', { body: { code }, ask: t.askOf(a), headers: ip('1.1.1.1') });
  assert.equal(ok.status, 200, 'the owner still gets in');
  assert.equal((await t.call('POST', '/_/api/verify', { body: { code }, ask: t.askOf(a), headers: ip('1.1.1.1') })).status, 400, 'a code works once');
  assert.ok(!JSON.stringify(t.env.DB.raw.prepare('SELECT * FROM limits').all()).includes('2.2.2.2'), 'no address is kept');
});

test('sign-in: an old code runs out; every address meets the same limit', async () => {
  const t = makeEnv();
  const a = await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, headers: t.W });
  const code = t.mails[0].subject.match(/(\d{6})/)[1];
  t.env.DB.raw.prepare('UPDATE codes SET expires = 1').run();
  assert.equal((await t.call('POST', '/_/api/verify', { body: { code }, ask: t.askOf(a), headers: t.W })).status, 400);
  const tries = async (email, ip) => { const o = []; for (let i = 0; i < 6; i++) o.push((await t.call('POST', '/_/api/login', { body: { email }, headers: { ...t.W, 'CF-Connecting-IP': ip } })).status); return o; };
  const want = [200, 200, 200, 200, 200, 429];
  assert.deepEqual(await tries('owner@example.com', '3.3.3.3'), want);
  assert.deepEqual(await tries('nobody@example.com', '4.4.4.4'), want);
});

test('sign-in: tries sent at the same moment all count, and a code opens one session', async () => {
  const t = makeEnv();
  const a = await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, headers: t.W });
  const code = t.mails[0].subject.match(/(\d{6})/)[1], wrong = code === '000000' ? '111111' : '000000', ask = t.askOf(a);
  await Promise.all(Array.from({ length: 40 }, (_, i) => t.call('POST', '/_/api/verify', { body: { code: wrong }, ask, headers: { ...t.W, 'CF-Connecting-IP': '5.5.5.' + i } })));
  assert.equal(t.env.DB.raw.prepare('SELECT tries FROM codes').get().tries, 5);
  assert.equal((await t.call('POST', '/_/api/verify', { body: { code }, ask, headers: t.W })).status, 400);
  const a2 = await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, headers: t.W });
  const c2 = t.mails.at(-1).subject.match(/(\d{6})/)[1];
  const two = await Promise.all([1, 2, 3].map((i) => t.call('POST', '/_/api/verify', { body: { code: c2 }, ask: t.askOf(a2), headers: { ...t.W, 'CF-Connecting-IP': '6.6.6.' + i } })));
  assert.equal(two.filter((r) => r.status === 200).length, 1);
  const many = await Promise.all(Array.from({ length: 20 }, () => t.call('POST', '/_/api/login', { body: { email: 'x@example.com' }, headers: { ...t.W, 'CF-Connecting-IP': '7.7.7.7' } })));
  assert.equal(many.filter((r) => r.status === 200).length, 5, 'the limit holds for requests sent together');
});

test('sign-in: strangers who know the address cannot use up the owner\'s codes', async () => {
  const t = makeEnv();
  const a = await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, headers: t.W });
  const ok = await t.call('POST', '/_/api/verify', { body: { code: t.mails[0].subject.match(/(\d{6})/)[1] }, ask: t.askOf(a), headers: t.W });
  const device = ok.headers.get('Set-Cookie').match(/rbd=([0-9a-f.]+)/)[1];
  for (let i = 0; i < 60; i++) assert.equal((await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, headers: { ...t.W, 'CF-Connecting-IP': '8.8.1.' + i } })).status, 200);
  assert.equal(t.mails.length, 30, 'the mailbox is not flooded');
  const forged = await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, device: 'a'.repeat(32) + '.' + 'b'.repeat(64), headers: { ...t.W, 'CF-Connecting-IP': '8.8.9.1' } });
  assert.equal(forged.status, 200);
  assert.equal(t.mails.length, 30, 'a made-up mark gets no code');
  const mine = await t.call('POST', '/_/api/login', { body: { email: 'owner@example.com' }, device, headers: { ...t.W, 'CF-Connecting-IP': '8.8.9.2' } });
  assert.equal(t.mails.length, 31, 'a browser that signed in before still gets one');
  assert.equal((await t.call('POST', '/_/api/verify', { body: { code: t.mails.at(-1).subject.match(/(\d{6})/)[1] }, ask: t.askOf(mine), device, headers: t.W })).status, 200);
  assert.equal((await (await t.call('GET', '/_/api/me', { device })).json()).owner, false, 'the mark alone opens nothing');
});

test('sign-in: an IPv6 visitor is one /64; addresses typed leave no rows', async () => {
  assert.equal(visitorNet('2001:db8:1:2:aaaa::1'), visitorNet('2001:0db8:1:2:ffff:ffff:ffff:ffff'));
  assert.equal(visitorNet('2001:db8::1'), '2001:db8:0:0::/64');
  assert.notEqual(visitorNet('2001:db8:1:2::1'), visitorNet('2001:db8:1:3::1'));
  assert.equal(visitorNet('::ffff:1.2.3.4'), '1.2.3.4');
  const t = makeEnv();
  for (let i = 0; i < 20; i++) await t.call('POST', '/_/api/login', { body: { email: `p${i}@example.com` }, headers: { ...t.W, 'CF-Connecting-IP': '2001:db8:1:2::' + i } });
  assert.equal(t.env.DB.raw.prepare('SELECT COUNT(*) AS n FROM limits').get().n, 1);
});

test('the way in: a Code link for visitors, not for the owner; the old address moves', async () => {
  const t = makeEnv();
  assert.ok((await (await t.call('GET', '/')).text()).includes('href="/_/code"'));
  const page = await t.call('GET', '/_/code');
  assert.equal(page.status, 200);
  assert.ok((await page.text()).includes('noindex'));
  const old = await t.call('GET', '/_/login');
  assert.equal(old.status, 302);
  assert.ok(old.headers.get('Location').endsWith('/_/code'));
  const tok = await t.signIn();
  assert.ok(!(await (await t.call('GET', '/', { cookie: tok })).text()).includes('href="/_/code"'));
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

test('the Desk word lists: own key, "<Language> words" pages only, the owner\'s lines above the mark stay', async () => {
  const WK = 'w'.repeat(40);
  const t = makeEnv({ WORDS_KEY: WK });
  const tok = await t.signIn();
  const H = { ...t.W, Authorization: 'Bearer ' + WK };
  const send = (body, headers = H) => t.call('PUT', '/_/api/desk/words', { body, headers });
  // the backup key and no key are refused; so is any other title
  assert.equal((await send({ title: 'Russian words', lines: ['x'] }, { ...t.W, Authorization: 'Bearer ' + 'k'.repeat(40) })).status, 401);
  assert.equal((await send({ title: 'Russian words', lines: ['x'] }, t.W)).status, 401);
  assert.equal((await send({ title: 'Diary', lines: ['x'] })).status, 400);
  assert.equal((await send({ title: 'Russian words', lines: ['x'] }, { Authorization: 'Bearer ' + WK })).status, 403);
  // first write makes a public page
  const r = await send({ title: 'Russian words', head: ['Words in [Russian].'], lines: ['2026-11', ' спасибо — ありがとう'], setPub: true });
  assert.equal(r.status, 200);
  const id = (await r.json()).page.id;
  const pg = await (await t.call('GET', '/_/api/page/' + id)).json();
  assert.deepEqual(pg.page.lines, ['Russian words', 'Words in [Russian].', '', 'The Desk adds the words below this line by itself.', '2026-11', ' спасибо — ありがとう']);
  // the owner writes above the mark; the next Desk write keeps it and replaces only what is below
  const own = [...pg.page.lines.slice(0, 2), 'my own note', ...pg.page.lines.slice(2)];
  assert.equal((await t.call('PUT', '/_/api/page/' + id, { body: { lines: own, baseRev: pg.page.rev }, cookie: tok, headers: t.W })).status, 200);
  assert.equal((await send({ title: 'Russian words', lines: ['2026-11', ' ягода — ベリー'] })).status, 200);
  const after = (await (await t.call('GET', '/_/api/page/' + id)).json()).page.lines;
  assert.deepEqual(after, ['Russian words', 'Words in [Russian].', 'my own note', '', 'The Desk adds the words below this line by itself.', '2026-11', ' ягода — ベリー']);
  // turned off on the Desk: the page goes private but keeps its words; turned on again: public
  assert.equal((await send({ title: 'Russian words', lines: ['2026-11', ' ягода — ベリー'], setPub: false })).status, 200);
  assert.equal((await t.call('GET', '/_/api/page/' + id)).status, 404);
  assert.equal((await send({ title: 'Russian words', lines: ['2026-11', ' ягода — ベリー'], setPub: true })).status, 200);
  assert.equal((await t.call('GET', '/_/api/page/' + id)).status, 200);
});

/* ---------- files and storage ---------- */
const PART = 16 * 1024 * 1024;
async function send(t, name, buf) {
  const C = { cookie: t.tok, headers: t.W };
  const s = await t.call('POST', '/_/api/files/start', { ...C, body: { name, size: buf.length } });
  if (s.status !== 200) return { status: s.status, ...(await s.json()) };
  const { name: key, parts } = await s.json();
  const tags = [];
  for (let n = 1; n <= parts; n++) tags.push(await (await t.call('PUT', `/_/api/files/part/${key}/${n}`, { ...C, body: buf.subarray((n - 1) * PART, n * PART), raw: true })).json());
  const d = await t.call('POST', '/_/api/files/done/' + key, { ...C, body: { parts: tags } });
  return { status: d.status, ...(await d.json()) };
}
const storageOf = async (t) => (await t.call('GET', '/_/api/storage', { cookie: t.tok })).json();

test('any file, in pieces: the bytes arrive whole, and the room counts', async () => {
  const t = await setup();
  const before = (await storageOf(t)).used;
  const big = Buffer.alloc(PART * 2 + 7, 1); big[PART] = 9; big[big.length - 1] = 5;
  const r = await send(t, 'clip.MP4', big);
  assert.equal(r.status, 200); assert.equal(r.kind, 'video'); assert.match(r.url, /^\/files\/[a-z0-9]+\.mp4$/);
  assert.ok(Buffer.compare(t.env.FILES.m.get(r.name).body, big) === 0);
  const st = await storageOf(t);
  assert.equal(st.used, before + big.length); assert.equal(st.uploading, 0); assert.equal(st.kinds.video.n, 1);
  assert.equal(st.files[0].orig, 'clip.MP4'); assert.deepEqual(st.files[0].pages, []);
});

test('a piece of the wrong size, a missing piece and a stranger are refused', async () => {
  const t = await setup(); const C = { cookie: t.tok, headers: t.W };
  const { name } = await (await t.call('POST', '/_/api/files/start', { ...C, body: { name: 'a.pdf', size: 10 } })).json();
  assert.equal((await t.call('PUT', `/_/api/files/part/${name}/1`, { ...C, raw: true, body: Buffer.alloc(11) })).status, 400);
  assert.equal((await t.call('PUT', `/_/api/files/part/${name}/2`, { ...C, raw: true, body: Buffer.alloc(10) })).status, 400);
  assert.equal((await t.call('POST', `/_/api/files/done/${name}`, { ...C, body: { parts: [] } })).status, 400);
  assert.equal((await storageOf(t)).uploading, 10, 'its room is held');
  assert.equal((await t.call('DELETE', `/_/api/files/upload/${name}`, C)).status, 200);
  assert.equal((await storageOf(t)).uploading, 0); assert.equal(t.env.FILES.ups.size, 0);
  for (const [m, p] of [['POST', 'files/start'], ['PUT', 'files/part/a.pdf/1'], ['POST', 'files/done/a.pdf'], ['DELETE', 'files/a.pdf'], ['DELETE', 'files/upload/a.pdf'], ['POST', 'storage/recount'], ['GET', 'storage']]) {
    assert.equal((await t.call(m, '/_/api/' + p, { headers: t.W, ...(m === 'GET' ? {} : { body: {} }) })).status, 401, m + ' ' + p);
  }
  assert.equal((await t.call('GET', '/_/storage')).status, 404);
  assert.equal((await t.call('GET', '/_/storage', { cookie: t.tok })).status, 200);
});

test('500 MB a file and 9 GB together: refused before anything is sent; abandoned uploads free their room', async () => {
  const t = await setup(); const C = { cookie: t.tok, headers: t.W };
  const start = (size) => t.call('POST', '/_/api/files/start', { ...C, body: { name: 'x.bin', size } });
  assert.equal((await start(500e6 + 1)).status, 413);
  assert.equal((await start(0)).status, 400);
  for (let i = 0; i < 18; i++) assert.equal((await start(500e6)).status, 200);
  const full = await start(1);
  assert.equal(full.status, 507); assert.equal((await full.json()).error, 'full');
  assert.equal((await t.call('POST', '/_/api/files', { ...C, raw: true, body: Buffer.from('x'), headers: { ...t.W, 'Content-Type': 'image/png' } })).status, 507, 'the older picture upload too');
  t.env.DB.raw.prepare('UPDATE uploads SET created = created - ?').run(7 * 36e5);
  assert.equal((await start(500e6)).status, 200);
  assert.equal((await storageOf(t)).uploading, 500e6); assert.equal(t.env.FILES.ups.size, 1);
});

test('files follow their page: private 404, public open; code blocks do not publish a file', async () => {
  const t = await setup();
  const { name } = await send(t, '報告書 (1).pdf', Buffer.from('%PDF'));
  const get = () => t.call('GET', '/files/' + name);
  assert.equal((await get()).status, 404);
  await t.put('priv0002', ['notes', `[/files/${name} Report]`]);
  assert.equal((await get()).status, 404);
  await t.put('pub00003', ['shown as code', 'code:a.txt', ` [/files/${name} Report]`], { pub: true });
  assert.equal((await get()).status, 404);
  await t.put('priv0002', ['notes', `[/files/${name} Report]`], { pub: true });
  const r = await get();
  assert.equal(r.status, 200); assert.equal(r.headers.get('Content-Type'), 'application/pdf');
  assert.match(r.headers.get('Content-Disposition'), /^inline; filename\*=UTF-8''%E5%A0%B1%E5%91%8A%E6%9B%B8%20%281%29\.pdf$/);
  const page = await (await t.call('GET', '/notes')).text();
  assert.ok(page.includes(`<iframe src="/files/${name}#view=FitH&amp;navpanes=0"`));
  assert.ok(!page.includes('class="image'));
  const f = (await storageOf(t)).files.find((x) => x.name === name);
  assert.deepEqual(f.pages.map((p) => p.title), ['notes']);
});

test('web pages and SVGs never run here: always a download', async () => {
  const t = await setup();
  for (const n of ['evil.html', 'evil.svg', 'a.js', 'noending', 'x.png.htm']) {
    const { name } = await send(t, n, Buffer.from('<script>1</script>'));
    const r = await t.call('GET', '/files/' + name, { cookie: t.tok });
    assert.equal(r.headers.get('Content-Type'), 'application/octet-stream', n);
    assert.match(r.headers.get('Content-Disposition'), /^attachment;/);
    assert.match(r.headers.get('Content-Security-Policy'), /sandbox/);
    assert.equal(r.headers.get('X-Content-Type-Options'), 'nosniff');
  }
});

test('parts of a file can be asked for (videos on iPhone)', async () => {
  const t = await setup();
  const { name } = await send(t, 'a.mp4', Buffer.from('0123456789'));
  const get = (Range, m = 'GET') => t.call(m, '/files/' + name, { cookie: t.tok, headers: Range ? { Range } : {} });
  for (const [h, want, cr] of [['bytes=0-1', '01', 'bytes 0-1/10'], ['bytes=7-', '789', 'bytes 7-9/10'], ['bytes=-2', '89', 'bytes 8-9/10'], ['bytes=5-99', '56789', 'bytes 5-9/10']]) {
    const r = await get(h); assert.equal(r.status, 206); assert.equal(r.headers.get('Content-Range'), cr); assert.equal(await r.text(), want);
  }
  assert.equal((await get('bytes=10-')).status, 416);
  assert.equal((await get()).status, 200);
  const h = await get('', 'HEAD'); assert.equal(h.headers.get('Content-Length'), '10'); assert.equal(await h.text(), '');
});

test('deleting frees the room; the site picture stays; recount matches the real files; the Desk sees numbers only', async () => {
  const t = await setup(); const C = { cookie: t.tok, headers: t.W };
  const { name } = await send(t, 'a.zip', Buffer.alloc(100));
  assert.equal((await (await t.call('DELETE', '/_/api/files/' + name, C)).json()).used, 0);
  assert.ok(!t.env.FILES.m.has(name));
  assert.equal((await t.call('DELETE', '/_/api/files/avatar.jpg', C)).status, 409);
  t.env.DB.raw.prepare("INSERT INTO files VALUES ('gone.png', 'image/png', 50, 1)").run();
  const rc = await (await t.call('POST', '/_/api/storage/recount', { ...C, body: {} })).json();
  assert.deepEqual([rc.added, rc.removed, rc.used], [2, 1, 2], 'priv.png and pub.png were only in R2');
  const d = await (await t.call('GET', '/_/api/storage', { headers: { Authorization: 'Bearer ' + 'k'.repeat(40) } })).json();
  assert.equal(d.used, 2); assert.equal(d.cap, 9e9); assert.equal(d.files, undefined);
});
