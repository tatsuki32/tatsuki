// Rebuilds a picture from only the parts needed to draw it, before it leaves the device. Everything else (EXIF, XMP, IPTC,
// maker notes, comments, thumbnails, whatever follows the end of the picture) is left out, so the place it was taken cannot
// travel with it. Pure functions, tested in Node. They throw when the picture cannot be read: the caller must then ask.

const ascii = (b, at, n) => String.fromCharCode(...b.subarray(at, at + n));
const view = (b) => new DataView(b.buffer, b.byteOffset, b.length);
function join(parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

export function pictureKind(b) {
  if (b[0] === 0xff && b[1] === 0xd8) return 'jpeg';
  if (b[0] === 0x89 && ascii(b, 1, 3) === 'PNG') return 'png';
  if (/^GIF8[79]a$/.test(ascii(b, 0, 6))) return 'gif';
  if (ascii(b, 0, 4) === 'RIFF' && ascii(b, 8, 4) === 'WEBP') return 'webp';
  if (ascii(b, 4, 4) === 'ftyp') { const brand = ascii(b, 8, 4); if (/^avi[fs]$/.test(brand)) return 'avif'; if (/^(hei[cxms]|hev[cxms]|mif1|msf1)$/.test(brand)) return 'heic'; }
  return '';
}
/* Is this meant to be a picture, whatever it is? (TIFF, camera raw, JPEG XL … : kinds this file cannot rebuild) */
export function looksLikePicture(name, type, b) {
  if (pictureKind(b) || /^(II\*\0|MM\0\*|II\x2b\0|MM\0\x2b|IIU\0|IIRO|IIRS|FUJIFILM|FOVb)/.test(ascii(b, 0, 8))) return true;
  if (/\.(jpe?g|jpe|jfif|png|apng|gif|webp|avif|hei[cfs]|hif|tiff?|dng|cr[23w]|nef|nrw|arw|sr[2fw]|raf|orf|rw2|pef|x3f|3fr|iiq|erf|mrw|jxl|jp2|j2k|jpx|psd|psb|insp|gpr)$/i.test(name || '')) return true;
  return /^image\//.test(type || '') && !/^image\/(svg\+xml|bmp|x-ms-bmp|x-icon|vnd\.microsoft\.icon)$/.test(type);
}

/* Which way is up (1–8) from an EXIF block; 1 when it cannot be told. */
function orientation(b, t, end) {
  try {
    const v = new DataView(b.buffer, b.byteOffset + t, end - t), le = v.getUint16(0) === 0x4949;
    if (!le && v.getUint16(0) !== 0x4d4d) return 1;
    const ifd = v.getUint32(4, le);
    for (let k = 0, n = v.getUint16(ifd, le); k < n; k++) if (v.getUint16(ifd + 2 + k * 12, le) === 0x0112) { const o = v.getUint16(ifd + 10 + k * 12, le); return o >= 1 && o <= 8 ? o : 1; }
  } catch (e) {}
  return 1;
}
const upright = (o) => new Uint8Array([0xff, 0xe1, 0, 34, 0x45, 0x78, 0x69, 0x66, 0, 0, 0x4d, 0x4d, 0, 0x2a, 0, 0, 0, 8, 0, 1, 0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, o, 0, 0, 0, 0, 0, 0]);

export function cleanJpeg(b) {
  const keep = []; let i = 2, way = 1, scans = 0;
  for (;;) {
    if (i + 1 >= b.length || b[i] !== 0xff) throw new Error('jpeg');
    const mark = b[i + 1];
    if (mark === 0xff) { i++; continue; }
    if (mark === 0xd9) break; // the end: what follows (a second picture, a film) is left out
    if (i + 4 > b.length) throw new Error('jpeg');
    const len = b[i + 2] << 8 | b[i + 3]; let end = i + 2 + len;
    if (len < 2 || end > b.length || mark < 0xc0) throw new Error('jpeg');
    const id = ascii(b, i + 4, Math.min(12, len - 2));
    let ok = (mark >= 0xc0 && mark <= 0xcf) || (mark >= 0xda && mark <= 0xdf); // frames, tables, scans
    if (mark === 0xe0) ok = id.startsWith('JFIF\0') && len === 16 && b[i + 16] === 0 && b[i + 17] === 0; // without its thumbnail
    if (mark === 0xe1 && id.startsWith('Exif\0\0')) way = orientation(b, i + 10, end);
    if (mark === 0xe2) ok = id.startsWith('ICC_PROFILE\0');
    if (mark === 0xee) ok = id.startsWith('Adobe') && len === 14;
    if (mark === 0xda) { // a scan: its data runs to the next marker
      scans++;
      while (end + 1 < b.length && !(b[end] === 0xff && b[end + 1] !== 0 && b[end + 1] !== 0xff && !(b[end + 1] >= 0xd0 && b[end + 1] <= 0xd7))) end++;
      if (end + 1 >= b.length) throw new Error('jpeg');
    }
    if (ok) keep.push(b.subarray(i, end));
    i = end;
  }
  if (!scans) throw new Error('jpeg');
  const head = [new Uint8Array([0xff, 0xd8])];
  if (keep[0] && keep[0][1] === 0xe0) head.push(keep.shift());
  if (way > 1) head.push(upright(way));
  return join([...head, ...keep, new Uint8Array([0xff, 0xd9])]);
}

const PNG_KEEP = ['IHDR', 'PLTE', 'IDAT', 'IEND', 'tRNS', 'gAMA', 'cHRM', 'sRGB', 'iCCP', 'cICP', 'sBIT', 'bKGD', 'hIST', 'pHYs', 'acTL', 'fcTL', 'fdAT'];
export function cleanPng(b) {
  const v = view(b), keep = [b.subarray(0, 8)];
  for (let i = 8; ;) {
    if (i + 12 > b.length) throw new Error('png');
    const n = v.getUint32(i), id = ascii(b, i + 4, 4);
    if (i + 12 + n > b.length) throw new Error('png');
    if (PNG_KEEP.includes(id)) keep.push(b.subarray(i, i + 12 + n));
    if (id === 'IEND') return join(keep);
    i += 12 + n;
  }
}

const WEBP_KEEP = ['VP8X', 'VP8 ', 'VP8L', 'ALPH', 'ANIM', 'ANMF', 'ICCP'];
export function cleanWebp(b) {
  const v = view(b), end = v.getUint32(4, true) + 8, keep = [];
  if (end > b.length) throw new Error('webp');
  const walk = (from, to, fn) => { for (let i = from; i < to;) { if (i + 8 > to) throw new Error('webp'); const n = v.getUint32(i + 4, true); if (i + 8 + n > to) throw new Error('webp'); fn(ascii(b, i, 4), i, n); i += 8 + n + (n & 1); } };
  walk(12, end, (id, i, n) => {
    if (!WEBP_KEEP.includes(id)) return;
    const c = new Uint8Array(8 + n + (n & 1)); c.set(b.subarray(i, i + 8 + n));
    if (id === 'VP8X') c[8] &= ~0x0c; // no EXIF, no XMP any more
    if (id === 'ANMF') walk(i + 24, i + 8 + n, (sub) => { if (!['VP8 ', 'VP8L', 'ALPH'].includes(sub)) throw new Error('webp'); });
    keep.push(c);
  });
  if (!keep.length) throw new Error('webp');
  const out = join([b.slice(0, 12), ...keep]);
  view(out).setUint32(4, out.length - 8, true);
  return out;
}

export function cleanGif(b) {
  const table = (f) => (f & 0x80 ? 3 * (2 << (f & 7)) : 0);
  let i = 13 + table(b[10]); const keep = [b.subarray(0, i)];
  const blocks = (at) => { while (b[at]) at += b[at] + 1; if (at >= b.length) throw new Error('gif'); return at + 1; };
  for (;;) {
    if (i >= b.length) throw new Error('gif');
    if (b[i] === 0x3b) return join([...keep, new Uint8Array([0x3b])]);
    let end, ok;
    if (b[i] === 0x2c) { end = blocks(i + 10 + table(b[i + 9]) + 1); ok = true; }
    else if (b[i] === 0x21) { end = blocks(i + 2); ok = b[i + 1] === 0xf9 || (b[i + 1] === 0xff && b[i + 2] === 11 && /^(NETSCAPE2\.0|ANIMEXTS1\.0)$/.test(ascii(b, i + 3, 11)) && end - i === 19); }
    else throw new Error('gif');
    if (ok) keep.push(b.subarray(i, end));
    i = end;
  }
}
/* The clean bytes, or null = draw it again on a canvas. */
export function cleanPicture(b, kind) {
  if (kind === 'jpeg') return cleanJpeg(b);
  if (kind === 'png') return cleanPng(b);
  if (kind === 'webp') return cleanWebp(b);
  if (kind === 'gif') return cleanGif(b);
  if (kind === 'avif' || kind === 'heic') return null;
  throw new Error('unknown');
}
