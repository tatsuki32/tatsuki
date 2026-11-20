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
