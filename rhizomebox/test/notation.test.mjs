import { test } from 'node:test';
import assert from 'node:assert/strict';
import { linksOf, webLinksOf, imagesOf, derive, renderStatic, makeInline, ytLine, codeBlocks, textLines } from '../public/app/notation.js';

const lines = [
  'リゾーム',
  '[/files/abc.png]',
  'ドゥルーズの[千のプラトー]と #思想 と [tatsuki.icon*3]',
  '[* 太字] [[強調]] `[コード]` [$ x^2] [/other/page]',
  '[Rhizome - Wikipedia https://en.wikipedia.org/wiki/Rhizome_(philosophy)]',
  'https://www.youtube.com/watch?v=abc',
  'code:style.css',
  ' .x { color: red } [not a link]',
  'table:t',
  ' a\t[b]',
  '[リンク]',
];

test('page links, tags and icons; code, formulas, URLs and [[bold]] are not links', () => {
  assert.deepEqual(linksOf(lines), ['千のプラトー', 'tatsuki', '思想', 'リンク']);
});

test('web links keep their labels; images are not web links', () => {
  assert.deepEqual(webLinksOf(lines), [
    { url: 'https://en.wikipedia.org/wiki/Rhizome_(philosophy)', label: 'Rhizome - Wikipedia' },
    { url: 'https://www.youtube.com/watch?v=abc', label: '' },
  ]);
  assert.deepEqual(imagesOf(lines), ['/files/abc.png']);
});

test('derive: title, picture, readable text without code, page CSS', () => {
  const d = derive(lines);
  assert.equal(d.title, 'リゾーム');
  assert.equal(d.image, '/files/abc.png');
  assert.match(d.descr, /千のプラトー/);
  assert.doesNotMatch(d.descr, /color: red/);
  assert.equal(d.css, '.x { color: red } [not a link]');
  assert.deepEqual(codeBlocks(['a', 'code:style.css', ' a{}', '', ' b{}', 'after'], 'style.css'), ['a{}\n\nb{}']);
  assert.deepEqual(textLines(['t', '[* 見出し]', 'code:x', ' y']), ['見出し']);
});

test('YouTube-only lines are recognised', () => {
  assert.equal(ytLine('https://youtu.be/xyz'), 'https://youtu.be/xyz');
  assert.equal(ytLine('[解説 https://www.youtube.com/results?search_query=a]'), 'https://www.youtube.com/results?search_query=a');
  assert.equal(ytLine('see https://youtu.be/xyz'), '');
});

test('rendering escapes everything a page author types', () => {
  const ctx = { link: (t, l) => `<a>${l}</a>`, icon: (n) => n, img: (u) => `<img src="${u}">` };
  const inl = makeInline(ctx);
  assert.equal(inl('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;');
  assert.doesNotMatch(inl('[javascript:alert(1) x]'), /href="javascript/);
  const h = renderStatic(['t', '"><img src=x onerror=alert(1)>', 'code:a.html', ' <script>x</script>'], ctx);
  assert.doesNotMatch(h, /<img src=x/);
  assert.match(h, /sandbox=""/);
  assert.doesNotMatch(h, /<script>x/);
});

test('stored files: title kept, not an image, not a page link, not a web link', async () => {
  const { filesOf, fileRef, kindOfFile, isImg } = await import('../public/app/notation.js');
  const L = ['t', '[/files/a1.pdf Yearly report.icon]', '[https://rhizomebox.tany.im/files/b2.zip]', '[/files/c3.png]', '`[/files/no.pdf x]`', 'table:x', ' [/files/d4.mp4 v]\tb', 'code:z', ' [/files/no2.pdf x]'];
  assert.deepEqual(filesOf(L), ['a1.pdf', 'b2.zip', 'c3.png', 'd4.mp4']);
  assert.deepEqual(imagesOf(L), ['/files/c3.png']);
  assert.deepEqual(linksOf(L), []);
  assert.deepEqual(webLinksOf(L), []);
  assert.equal(textLines(L)[0], 'Yearly report.icon');
  assert.equal(fileRef('/files/c3.png a'), null);
  assert.equal(fileRef('https://other.example/files/x.pdf a'), null, 'other sites are links, as before');
  assert.equal(kindOfFile('x.SVG'), 'other'); assert.ok(!isImg('/files/a1.pdf'));
  const h = makeInline({ link: (a, b) => b, icon: (n) => n, img: (u) => u })('[/files/x.zip "><script>]');
  assert.ok(h.includes('download') && !h.includes('<script>'));
});

test('a visitor may open exactly the files the page draws', async () => {
  const { filesOf, isImg, resolveFile } = await import('../public/app/notation.js');
  const hidden = ['[/files/t.pdf title line]', '$ [/files/a.pdf x]', '% [/files/b.png]', '[$ /files/c.pdf]', '[note /files/d.pdf]', '[https://other.example/files/e.png]', '[https://other.example/files/f.pdf x]', '`[/files/g.pdf]`', '/files/h.pdf', 'code:z', ' [/files/i.pdf x]', '', '[* /files/j.pdf]'];
  assert.deepEqual(filesOf(hidden), []);
  assert.deepEqual(filesOf(['t', '> [/files/k.pdf q]', '[[/files/l.png]]', '[https://rhizomebox.tany.im/files/m.png]']), ['k.pdf', 'l.png', 'm.png']);
  assert.equal(resolveFile('https://other.example/files/e.png'), 'https://other.example/files/e.png');
  assert.equal(resolveFile('https://rhizomebox.tany.im/files/m.png'), '/files/m.png');
  assert.ok(isImg('https://other.example/files/e.png'));
});

test('plain text: what the eye reads, title first, no notation', async () => {
  const { plainText } = await import('../public/app/notation.js');
  const L = ['誤配 & <題>', '[** 見出し]', 'これは[リゾーム]と[* 大事]な[/ 話]、[[太字]]と`code`。 #哲学', '[tatsuki.icon]から [Wiki https://en.wikipedia.org/wiki/X] と [https://example.com/a 例] と https://example.com/b', '[/files/a.png]', '', '', '', ' 点1 [$ x^2]', '  点2', '> 引用[言葉]', '[/files/b.pdf 報告書]', 'code:a.js', ' const a = [1];', '  b()', 'table:表', ' [左]\t右', '$ ls', 'a < b && "c"'];
  assert.equal(plainText(L), ['誤配 & <題>', '', '見出し', 'これはリゾームと大事な話、太字とcode。 #哲学', 'から Wiki と 例 と https://example.com/b', '', '• 点1 x^2', '  • 点2', '引用言葉', '報告書', 'const a = [1];', ' b()', '表', '左\t右', 'ls', 'a < b && "c"', ''].join('\n'));
  assert.equal(plainText(['only']), 'only\n');
});
