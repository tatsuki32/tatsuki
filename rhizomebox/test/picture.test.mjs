import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pictureKind, cleanPicture } from '../public/app/picture.js';

const u8 = (...parts) => new Uint8Array(Buffer.concat(parts.map((p) => Buffer.from(p))));
const seg = (mark, body) => Buffer.concat([Buffer.from([0xff, mark, (body.length + 2) >> 8, (body.length + 2) & 255]), body]);
const TAIL = Buffer.from([0xff, 0xda, 0, 2, 1, 2, 3, 0xff, 0xd9]);
const GPS = Buffer.from('/9j/4QBkRXhpZgAATU0AKgAAAAgAAgESAAMAAAABAAYAAIglAAQAAAABAAAAJgAAAAAAAgABAAIAAAACTgAAAAACAAUAAAADAAAARAAAAAAAAAAjAAAAAQAAACkAAAABAAAeYQAAAGT/2gACREFUQURBVEFEQVRBREFUQURBVEH/2Q==', 'base64');

test('JPEG: GPS numbers are gone, the way up and the picture stay', () => {
  const b = new Uint8Array(GPS); assert.equal(pictureKind(b), 'jpeg');
  assert.equal(cleanPicture(b, 'jpeg'), '');
  assert.equal(b[31], 6); assert.ok(b.slice(50, 104).every((x) => x === 0));
  assert.deepEqual([...b.slice(104)], [...GPS.subarray(104)]);
});
test('JPEG: XMP and IPTC are emptied; a hidden second picture or film means drawing it again', () => {
  const xmp = u8([0xff, 0xd8], seg(0xe1, Buffer.from('http://ns.adobe.com/xap/1.0/\0<x exif:Place="sample-place"/>')), seg(0xed, Buffer.from('Photoshop Tokyo')), TAIL);
  assert.equal(cleanPicture(xmp, 'jpeg'), '');
  assert.ok(!/sample-place|Tokyo/.test(Buffer.from(xmp).toString('latin1')));
  assert.equal(cleanPicture(u8([0xff, 0xd8], seg(0xe2, Buffer.from('MPF\0x')), TAIL), 'jpeg'), 'redraw');
  assert.equal(cleanPicture(u8([0xff, 0xd8], seg(0xe1, Buffer.from('http://ns.adobe.com/xap/1.0/\0MotionPhoto="1"')), TAIL), 'jpeg'), 'redraw');
});
test('a broken picture is an error, never "clean"', () => {
  for (const b of [u8([0xff, 0xd8, 0xff]), u8([0xff, 0xd8], [0xff, 0xe1, 0xff, 0xff, 1]), u8([0xff, 0xd8], seg(0xe1, Buffer.from('Exif\0\0XXxxxxxxxx')), TAIL), new Uint8Array(GPS.subarray(0, 60))]) assert.throws(() => cleanPicture(b, 'jpeg'));
  assert.throws(() => cleanPicture(u8([0x89], 'PNG\r\n\x1a\n', [0, 0, 1, 0], 'IHDR'), 'png'));
  assert.throws(() => cleanPicture(u8('RIFF', [0, 0, 0, 0], 'WEBP', 'EXIF', [255, 0, 0, 0]), 'webp'));
});
test('PNG with words inside is drawn again; WebP is emptied; HEIC is always drawn again', () => {
  const chunk = (id, body) => Buffer.concat([Buffer.from([0, 0, 0, body.length]), Buffer.from(id), Buffer.from(body), Buffer.alloc(4)]);
  const png = (...c) => u8([0x89], 'PNG\r\n\x1a\n', ...c, chunk('IEND', ''));
  assert.equal(cleanPicture(png(chunk('IHDR', 'x')), 'png'), '');
  assert.equal(cleanPicture(png(chunk('eXIf', 'gps')), 'png'), 'redraw');
  const w = u8('RIFF', [0, 0, 0, 0], 'WEBP', 'EXIF', [4, 0, 0, 0], 'gps!', 'VP8 ', [2, 0, 0, 0], 'ok');
  assert.equal(cleanPicture(w, 'webp'), ''); assert.ok(!Buffer.from(w).includes('gps!')); assert.ok(Buffer.from(w).includes('ok'));
  const h = u8([0, 0, 0, 24], 'ftypheic'); assert.equal(pictureKind(h), 'heic'); assert.equal(cleanPicture(h, 'heic'), 'redraw');
});
