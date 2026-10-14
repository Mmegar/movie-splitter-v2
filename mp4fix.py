"""
Repairs parts made before the chapter fix: ffmpeg copied the movie's chapter list into each part as an
extra track as long as the WHOLE movie, so phones showed "1 hr" and the video stopped after the real
part. This removes that chapter track (pure Python, no ffmpeg needed) and sets the right length.

Only touches .mp4 files whose moov box comes before mdat (how the cutter writes them, "faststart").
"""
import struct
from pathlib import Path

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"}


def _boxes(b, start, end):
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", b[i:i + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", b[i + 8:i + 16])[0], 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            raise ValueError("broken mp4 box")
        yield i, size, hdr, typ
        i += size


def _find(b, start, end, path):
    """First box at path (list of types) inside [start, end): (pos, size, hdr) or None."""
    for pos, size, hdr, typ in _boxes(b, start, end):
        if typ == path[0]:
            return (pos, size, hdr) if len(path) == 1 else _find(b, pos + hdr, pos + size, path[1:])
    return None


def _handler(b, trak):
    pos, size, hdr = trak
    h = _find(b, pos + hdr, pos + size, [b"mdia", b"hdlr"])
    return b[h[0] + h[2] + 8:h[0] + h[2] + 12] if h else b""


def _track_seconds(b, trak):
    pos, size, hdr = trak
    m = _find(b, pos + hdr, pos + size, [b"mdia", b"mdhd"])
    v = b[m[0] + m[2]]
    if v == 1:
        ts, du = struct.unpack(">IQ", b[m[0] + m[2] + 20:m[0] + m[2] + 32])
    else:
        ts, du = struct.unpack(">II", b[m[0] + m[2] + 12:m[0] + m[2] + 20])
    return du / ts if ts else 0


def fix_bytes(data):
    """Returns repaired bytes, or None if nothing needed fixing."""
    b = bytes(data)
    moov = _find(b, 0, len(b), [b"moov"])
    mdat = _find(b, 0, len(b), [b"mdat"])
    if not moov or not mdat or moov[0] > mdat[0]:
        return None
    mpos, msize, mhdr = moov
    traks = [(p, s, h) for p, s, h, t in _boxes(b, mpos + mhdr, mpos + msize) if t == b"trak"]
    av = [t for t in traks if _handler(b, t) in (b"vide", b"soun")]
    extra = [t for t in traks if _handler(b, t) not in (b"vide", b"soun")]
    if not av or not extra:
        return None
    keep_secs = max(_track_seconds(b, t) for t in av)
    if max(_track_seconds(b, t) for t in extra) <= keep_secs + 1:
        return None                                      # extra tracks aren't longer: leave the file alone
    # new moov: without the extra (chapter) tracks, the "tref" links to them, and the right length
    out = bytearray()
    for p, s, h, t in _boxes(b, mpos + mhdr, mpos + msize):
        if (p, s, h) in extra:
            continue
        if t == b"trak":
            inner = bytearray()
            for p2, s2, h2, t2 in _boxes(b, p + h, p + s):
                if t2 != b"tref":
                    inner += b[p2:p2 + s2]
            out += struct.pack(">I4s", 8 + len(inner), b"trak") + inner
        else:
            out += b[p:p + s]
    new_moov = bytearray(struct.pack(">I4s", 8 + len(out), b"moov") + out)
    shift = msize - len(new_moov)
    # mvhd length: in the movie's timescale
    mv = _find(new_moov, 8, len(new_moov), [b"mvhd"])
    v = new_moov[mv[0] + mv[2]]
    if v == 1:
        ts = struct.unpack(">I", new_moov[mv[0] + mv[2] + 20:mv[0] + mv[2] + 24])[0]
        struct.pack_into(">Q", new_moov, mv[0] + mv[2] + 24, int(round(keep_secs * ts)))
    else:
        ts = struct.unpack(">I", new_moov[mv[0] + mv[2] + 12:mv[0] + mv[2] + 16])[0]
        struct.pack_into(">I", new_moov, mv[0] + mv[2] + 16, int(round(keep_secs * ts)))
    # the media data moves forward by `shift` bytes: fix every chunk offset
    for p, s, h, t in _boxes(new_moov, 8, len(new_moov)):
        if t != b"trak":
            continue
        st = _find(new_moov, p + h, p + s, [b"mdia", b"minf", b"stbl"])
        for p2, s2, h2, t2 in _boxes(new_moov, st[0] + st[2], st[0] + st[1]):
            if t2 in (b"stco", b"co64"):
                n = struct.unpack(">I", new_moov[p2 + h2 + 4:p2 + h2 + 8])[0]
                fmt, w = (">I", 4) if t2 == b"stco" else (">Q", 8)
                for k in range(n):
                    at = p2 + h2 + 8 + k * w
                    struct.pack_into(fmt, new_moov, at, struct.unpack(fmt, new_moov[at:at + w])[0] - shift)
    return b[:mpos] + bytes(new_moov) + b[mpos + msize:]


def fix_file(path):
    """Repairs the file in place (safely: temp file, then swap). True if it was changed."""
    path = Path(path)
    try:
        fixed = fix_bytes(path.read_bytes())
    except (OSError, ValueError, struct.error):
        return False
    if not fixed:
        return False
    tmp = path.with_name(path.name + ".fixing")
    tmp.write_bytes(fixed)
    tmp.replace(path)
    return True
