import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pictureKind, looksLikePicture, cleanPicture } from '../public/app/picture.js';

const u8 = (...parts) => new Uint8Array(Buffer.concat(parts.map((p) => Buffer.from(p))));
const seg = (mark, body) => Buffer.concat([Buffer.from([0xff, mark, (body.length + 2) >> 8, (body.length + 2) & 255]), Buffer.from(body)]);
const SOI = [0xff, 0xd8], EOI = [0xff, 0xd9];
const SCAN = Buffer.concat([seg(0xda, 'S'), Buffer.from([1, 0xff, 0, 2, 0xff, 0xd3, 4])]);
const text = (b) => Buffer.from(b).toString('latin1');
const EXIF = Buffer.from('/9j/4QBkRXhpZgAATU0AKgAAAAgAAgESAAMAAAABAAYAAIglAAQAAAABAAAAJgAAAAAAAgABAAIAAAACTgAAAAACAAUAAAADAAAARAAAAAAAAAAjAAAAAQAAACkAAAABAAAeYQAAAGQ=', 'base64').subarray(2);

test('JPEG: only what draws it stays, in order; every kind of note, and all after the end, is gone', () => {
  const b = u8(SOI, seg(0xe0, 'JFIF\0' + '\x01\x01\0\0\x01\0\x01\0\0'), EXIF, seg(0xe1, 'http://ns.adobe.com/xap/1.0/\0PLACE1'), seg(0xe1, 'other PLACE2'), seg(0xe2, 'MPF\0PLACE3'), seg(0xe2, 'ICC_PROFILE\0colors'), seg(0xe5, 'PLACE4'),
    seg(0xed, 'Photoshop PLACE5'), seg(0xfe, 'PLACE6'), seg(0xdb, 'quant'), seg(0xc0, 'frame'), seg(0xc4, 'huff'), SCAN, seg(0xe1, 'late PLACE7'), seg(0xc4, 'huff2'), SCAN, EOI, SOI, EXIF, 'PLACE8');
  const out = cleanPicture(b, 'jpeg');
  assert.ok(!text(out).includes('PLACE'));
  assert.deepEqual(out, u8(SOI, seg(0xe0, 'JFIF\0' + '\x01\x01\0\0\x01\0\x01\0\0'), seg(0xe1, Buffer.from('45786966000' + '04d4d002a00000008' + '0001' + '011200030000000100060000' + '00000000', 'hex')),
    seg(0xe2, 'ICC_PROFILE\0colors'), seg(0xdb, 'quant'), seg(0xc0, 'frame'), seg(0xc4, 'huff'), SCAN, seg(0xc4, 'huff2'), SCAN, EOI), 'the way up (6) is the only thing left of the EXIF');
  assert.deepEqual(cleanPicture(out, 'jpeg'), out, 'cleaning twice changes nothing');
  assert.ok(!text(cleanPicture(u8(SOI, seg(0xe0, 'JFXX\0PLACE'), seg(0xee, 'Adobe PLACE'), SCAN, EOI), 'jpeg')).includes('PLACE'));
});
test('a broken picture is an error, never "clean"', () => {
  for (const b of [u8(SOI, [0xff]), u8(SOI, [0xff, 0xe1, 0xff, 0xff, 1]), u8(SOI, SCAN), u8(SOI, EOI), u8(SOI, SCAN, 'x')]) assert.throws(() => cleanPicture(b, 'jpeg'));
  assert.throws(() => cleanPicture(u8([0x89], 'PNG\r\n\x1a\n', [0, 0, 1, 0], 'IHDR'), 'png'));
  assert.throws(() => cleanPicture(u8('RIFF', [20, 0, 0, 0], 'WEBP', 'EXIF', [255, 0, 0, 0], 'abcd'), 'webp'));
  assert.throws(() => cleanPicture(u8('GIF89a', [1, 0, 1, 0, 0, 0, 0], [0x21, 0xfe, 9, 1]), 'gif'));
  assert.throws(() => cleanPicture(u8('II*\0'), ''));
});
test('PNG: unknown and text parts are left out, and all after the end', () => {
  const chunk = (id, body) => Buffer.concat([Buffer.from([0, 0, 0, body.length]), Buffer.from(id), Buffer.from(body), Buffer.alloc(4)]);
  const head = [[0x89], 'PNG\r\n\x1a\n', chunk('IHDR', 'x')];
  const out = cleanPicture(u8(...head, chunk('eXIf', 'PLACE'), chunk('tEXt', 'PLACE'), chunk('zzZz', 'PLACE'), chunk('IDAT', 'pix'), chunk('IEND', ''), 'PLACE'), 'png');
  assert.deepEqual(out, u8(...head, chunk('IDAT', 'pix'), chunk('IEND', '')));
});
test('WebP: EXIF, XMP and unknown parts are left out, the flags and the size follow', () => {
  const c = (id, body) => Buffer.concat([Buffer.from(id), Buffer.from([body.length, 0, 0, 0]), Buffer.from(body), Buffer.alloc(body.length & 1)]);
  const riff = (...p) => { const body = Buffer.concat(p); const n = Buffer.alloc(4); n.writeUInt32LE(body.length + 4); return u8('RIFF', n, 'WEBP', body); };
  const out = cleanPicture(u8(riff(c('VP8X', [0x2c, 0, 0, 0, 0, 0, 0, 0, 0, 0]), c('VP8 ', 'pixel'), c('EXIF', 'PLACE'), c('XMP ', 'PLACE'), c('junk', 'PLACE')), 'PLACE'), 'webp');
  assert.deepEqual(out, riff(c('VP8X', [0x20, 0, 0, 0, 0, 0, 0, 0, 0, 0]), c('VP8 ', 'pixel')));
  assert.throws(() => cleanPicture(riff(c('ANMF', 'sixteen bytes!!!' + 'EXIF\x05\0\0\0PLACE\0')), 'webp'));
});
test('GIF: pictures and the loop stay; comments and other notes go', () => {
  const h = [...Buffer.from('GIF89a'), 1, 0, 1, 0, 0, 0, 0], img = [0x2c, 0, 0, 0, 0, 1, 0, 1, 0, 0, 2, 2, 0x4c, 1, 0], loop = [0x21, 0xff, 11, ...Buffer.from('NETSCAPE2.0'), 3, 1, 0, 0, 0];
  const note = [0x21, 0xfe, 5, ...Buffer.from('PLACE'), 0], xmp = [0x21, 0xff, 11, ...Buffer.from('XMP DataXMP'), 5, ...Buffer.from('PLACE'), 0];
  assert.deepEqual(cleanPicture(u8(h, loop, note, xmp, img, [0x3b], 'PLACE'), 'gif'), u8(h, loop, img, [0x3b]));
});
test('HEIC and AVIF are drawn again; raw, TIFF and unknown pictures count as pictures, so they are asked about', () => {
  const h = u8([0, 0, 0, 24], 'ftypheic'); assert.equal(pictureKind(h), 'heic'); assert.equal(cleanPicture(h, 'heic'), null); assert.equal(cleanPicture(h, 'avif'), null);
  const none = u8('hello world');
  for (const n of ['a.tif', 'a.DNG', 'a.cr3', 'a.nef', 'a.jxl', 'a.jpg', 'a.hif', 'a.gif']) assert.ok(looksLikePicture(n, '', none), n);
  assert.ok(looksLikePicture('a', 'image/whatever', none)); assert.ok(looksLikePicture('a.bin', '', u8('II*\0xxxx')));
  for (const [n, t] of [['a.pdf', 'application/pdf'], ['a.zip', ''], ['a.txt', 'text/plain'], ['a.svg', 'image/svg+xml'], ['a.mp4', 'video/mp4']]) assert.ok(!looksLikePicture(n, t, none), n);
});
