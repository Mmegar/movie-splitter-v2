// Same repair as mp4fix.py: parts made before the chapter fix carried the movie's chapter list as an
// extra track as long as the whole movie, so the phone showed "1 hr" and stopped after the real part.
// Removes that track before the video is saved. Returns the bytes unchanged if nothing needs fixing.

const td = new TextDecoder('latin1');
const CONT = new Set(['moov', 'trak', 'mdia', 'minf', 'stbl', 'edts']);

function boxes(v, start, end) {
  const out = [];
  let i = start;
  while (i + 8 <= end) {
    let size = v.getUint32(i), hdr = 8;
    const type = td.decode(new Uint8Array(v.buffer, v.byteOffset + i + 4, 4));
    if (size === 1) { size = Number(v.getBigUint64(i + 8)); hdr = 16; } else if (size === 0) size = end - i;
    if (size < hdr || i + size > end) throw new Error('broken mp4');
    out.push({ pos: i, size, hdr, type });
    i += size;
  }
  return out;
}
function find(v, start, end, path) {
  for (const b of boxes(v, start, end)) {
    if (b.type === path[0]) return path.length === 1 ? b : find(v, b.pos + b.hdr, b.pos + b.size, path.slice(1));
  }
  return null;
}
function handler(v, t) {
  const h = find(v, t.pos + t.hdr, t.pos + t.size, ['mdia', 'hdlr']);
  return h ? td.decode(new Uint8Array(v.buffer, v.byteOffset + h.pos + h.hdr + 8, 4)) : '';
}
function seconds(v, t) {
  const m = find(v, t.pos + t.hdr, t.pos + t.size, ['mdia', 'mdhd']);
  const p = m.pos + m.hdr;
  if (v.getUint8(p) === 1) return Number(v.getBigUint64(p + 24)) / v.getUint32(p + 20);
  return v.getUint32(p + 16) / v.getUint32(p + 12);
}

export function fixMp4(buf) {
  try {
    const bytes = new Uint8Array(buf);
    const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const top = boxes(v, 0, bytes.length);
    const moov = top.find((b) => b.type === 'moov');
    const mdat = top.find((b) => b.type === 'mdat');
    if (!moov || !mdat || moov.pos > mdat.pos) return buf;
    const traks = boxes(v, moov.pos + moov.hdr, moov.pos + moov.size).filter((b) => b.type === 'trak');
    const av = traks.filter((t) => ['vide', 'soun'].includes(handler(v, t)));
    const extra = traks.filter((t) => !['vide', 'soun'].includes(handler(v, t)));
    if (!av.length || !extra.length) return buf;
    const keep = Math.max(...av.map((t) => seconds(v, t)));
    if (Math.max(...extra.map((t) => seconds(v, t))) <= keep + 1) return buf;
    // build the new moov without the extra tracks and without "tref" links to them
    const parts = [];
    for (const b of boxes(v, moov.pos + moov.hdr, moov.pos + moov.size)) {
      if (extra.some((t) => t.pos === b.pos)) continue;
      if (b.type === 'trak') {
        const inner = boxes(v, b.pos + b.hdr, b.pos + b.size).filter((c) => c.type !== 'tref');
        const len = 8 + inner.reduce((n, c) => n + c.size, 0);
        const head = new Uint8Array(8); new DataView(head.buffer).setUint32(0, len); head.set([0x74, 0x72, 0x61, 0x6b], 4);
        parts.push(head, ...inner.map((c) => bytes.subarray(c.pos, c.pos + c.size)));
      } else parts.push(bytes.subarray(b.pos, b.pos + b.size));
    }
    const bodyLen = parts.reduce((n, p) => n + p.length, 0);
    const nm = new Uint8Array(8 + bodyLen);
    const nv = new DataView(nm.buffer);
    nv.setUint32(0, nm.length); nm.set([0x6d, 0x6f, 0x6f, 0x76], 4);
    let at = 8;
    for (const p of parts) { nm.set(p, at); at += p.length; }
    const shift = moov.size - nm.length;
    const mv = find(nv, 8, nm.length, ['mvhd']);
    const p0 = mv.pos + mv.hdr;
    if (nv.getUint8(p0) === 1) nv.setBigUint64(p0 + 24, BigInt(Math.round(keep * nv.getUint32(p0 + 20))));
    else nv.setUint32(p0 + 16, Math.round(keep * nv.getUint32(p0 + 12)));
    for (const t of boxes(nv, 8, nm.length).filter((b) => b.type === 'trak')) {
      const st = find(nv, t.pos + t.hdr, t.pos + t.size, ['mdia', 'minf', 'stbl']);
      for (const c of boxes(nv, st.pos + st.hdr, st.pos + st.size)) {
        if (c.type !== 'stco' && c.type !== 'co64') continue;
        const n = nv.getUint32(c.pos + c.hdr + 4);
        for (let k = 0; k < n; k++) {
          if (c.type === 'stco') { const a = c.pos + c.hdr + 8 + k * 4; nv.setUint32(a, nv.getUint32(a) - shift); }
          else { const a = c.pos + c.hdr + 8 + k * 8; nv.setBigUint64(a, nv.getBigUint64(a) - BigInt(shift)); }
        }
      }
    }
    const out = new Uint8Array(bytes.length - shift);
    out.set(bytes.subarray(0, moov.pos), 0);
    out.set(nm, moov.pos);
    out.set(bytes.subarray(moov.pos + moov.size), moov.pos + nm.length);
    return out.buffer;
  } catch {
    return buf;               // anything unexpected: save it as it is
  }
}
