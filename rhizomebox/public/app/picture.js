// Takes the place a picture was taken out of its bytes before it leaves the device. Pure functions, tested in Node.
// Every function throws when it cannot read the picture: the caller must then ask, never send it as it is.

const ascii = (b, at, n) => String.fromCharCode(...b.subarray(at, at + n));
const has = (b, word) => { const w = [...word].map((c) => c.charCodeAt(0)); outer: for (let i = 0; i + w.length <= b.length; i++) { for (let k = 0; k < w.length; k++) if (b[i + k] !== w[k]) continue outer; return true; } return false; };

export function pictureKind(b) {
  if (b[0] === 0xff && b[1] === 0xd8) return 'jpeg';
  if (b[0] === 0x89 && ascii(b, 1, 3) === 'PNG') return 'png';
  if (ascii(b, 0, 4) === 'RIFF' && ascii(b, 8, 4) === 'WEBP') return 'webp';
  if (ascii(b, 4, 4) === 'ftyp') { const brand = ascii(b, 8, 4); if (/^avi[fs]$/.test(brand)) return 'avif'; if (/^(hei[cxms]|hev[cxms]|mif1|msf1)$/.test(brand)) return 'heic'; }
  return '';
}

/* EXIF: only the GPS block is emptied; the rest (which way is up) stays. */
function wipeExif(b, t, end) {
  const v = new DataView(b.buffer, b.byteOffset + t, end - t);
  const order = v.getUint16(0);
  if (order !== 0x4949 && order !== 0x4d4d) throw new Error('exif');
  const le = order === 0x4949, ifd = v.getUint32(4, le), count = v.getUint16(ifd, le);
  const size = [0, 1, 1, 2, 4, 8, 1, 1, 2, 4, 8, 4, 8];
  for (let k = 0; k < count; k++) {
    const e = ifd + 2 + k * 12;
    if (v.getUint16(e, le) !== 0x8825) continue;
    const g = v.getUint32(e + 8, le), m = v.getUint16(g, le);
    for (let q = 0; q < m; q++) {
      const x = g + 2 + q * 12, n = v.getUint32(x + 4, le);
      if (n > 65535) throw new Error('exif');
      const bytes = (size[v.getUint16(x + 2, le)] || 1) * n;
      if (bytes > 4) { const at = v.getUint32(x + 8, le); for (let z = 0; z < bytes; z++) v.setUint8(at + z, 0); }
      for (let z = 0; z < 12; z++) v.setUint8(x + z, 0);
    }
    v.setUint16(g, 0, le);
  }
}

/* Changes b in place. Returns 'redraw' when the picture must be drawn again (a second picture or a film is inside). */
export function cleanJpeg(b) {
  let i = 2, redraw = false;
  for (;;) {
    if (i + 4 > b.length || b[i] !== 0xff) throw new Error('jpeg');
    const mark = b[i + 1];
    if (mark === 0xff) { i++; continue; }
    if (mark === 0xda || mark === 0xd9) break;
    const len = b[i + 2] << 8 | b[i + 3], end = i + 2 + len;
    if (len < 2 || end > b.length) throw new Error('jpeg');
    if (mark === 0xe1 && ascii(b, i + 4, 6) === 'Exif\0\0') wipeExif(b, i + 10, end);
    else if (mark === 0xe1) { const s = b.subarray(i + 4, end); if (has(s, 'MotionPhoto') || has(s, 'MicroVideo') || has(s, 'Container:')) redraw = true; s.fill(0); } // XMP
    else if (mark === 0xe2 && ascii(b, i + 4, 4) === 'MPF\0') redraw = true; // more pictures after this one, each with its own EXIF
    else if (mark === 0xed) b.fill(0, i + 4, end); // IPTC (place names)
    i = end;
  }
  return redraw ? 'redraw' : '';
}
export function cleanWebp(b) {
  const v = new DataView(b.buffer, b.byteOffset, b.length);
  for (let i = 12; i + 8 <= b.length;) {
    const n = v.getUint32(i + 4, true), id = ascii(b, i, 4);
    if (i + 8 + n > b.length) throw new Error('webp');
    if (id === 'EXIF' || id === 'XMP ') b.fill(0, i + 8, i + 8 + n);
    i += 8 + n + (n & 1);
  }
  return '';
}
export function cleanPng(b) {
  const v = new DataView(b.buffer, b.byteOffset, b.length);
  for (let i = 8; ;) {
    if (i + 12 > b.length) throw new Error('png');
    const n = v.getUint32(i), id = ascii(b, i + 4, 4);
    if (i + 12 + n > b.length) throw new Error('png');
    if (id === 'IEND') return '';
    if (['eXIf', 'iTXt', 'tEXt', 'zTXt'].includes(id)) return 'redraw'; // drawn again without any words inside
    i += 12 + n;
  }
}
/* '' = clean now (b was changed in place), 'redraw' = draw it again on a canvas. */
export function cleanPicture(b, kind) {
  if (kind === 'jpeg') return cleanJpeg(b);
  if (kind === 'webp') return cleanWebp(b);
  if (kind === 'png') return cleanPng(b);
  if (kind === 'avif') return has(b, 'Exif') || has(b, 'rdf+xml') ? 'redraw' : '';
  if (kind === 'heic') return 'redraw';
  return '';
}
