"""
Moving files through GitHub draft releases (Python standard library only).

    send()     uploads a local file as release files "<prefix>.000", "<prefix>.001", ...
               (each under 1.9 GB: GitHub's limit is 2 GB per file). Pieces already uploaded are
               skipped, so a send that was interrupted carries on where it stopped.
    receive()  downloads those pieces in order and joins them back into one local file.

Draft releases are only visible to people with write access to the repo, so they're not public.
The release text ("body") is JSON, so both sides can read what a release holds.
"""
import json
import os
from pathlib import Path

INCOMING = "incoming-"        # draft release holding a movie on its way to the cutter
PARTS = "parts-"              # draft release holding finished parts on their way back
CHUNK_SIZE = 1_900_000_000    # GitHub release files must stay under 2 GB
BLOCK = 1 << 20


def body_of(release):
    try:
        data = json.loads(release.get("body") or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def chunk_name(prefix, i):
    return f"{prefix}.{i:03}"


def assets_for(assets, prefix):
    """The pieces of one file, in order."""
    out = [a for a in assets if a["name"].rsplit(".", 1)[0] == prefix and a["name"].rsplit(".", 1)[-1].isdigit()]
    return sorted(out, key=lambda a: a["name"])


def ranges(size, chunk_size=CHUNK_SIZE):
    """[(start, end)] byte ranges of the pieces (an empty file is one empty piece)."""
    return [(a, min(a + chunk_size, size)) for a in range(0, size, chunk_size)] or [(0, 0)]


def read_range(path, start, end):
    """Yields the file's bytes from start to end, a block at a time (low memory)."""
    with open(path, "rb") as f:
        f.seek(start)
        left = end - start
        while left > 0:
            block = f.read(min(BLOCK, left))
            if not block:
                raise IOError(f"{path} got shorter while it was being sent")
            left -= len(block)
            yield block


def send(gh, release, path, prefix, existing=(), chunk_size=CHUNK_SIZE, progress=None):
    """Uploads `path` as release files prefix.000, prefix.001... progress(percent). Returns the names."""
    size = os.path.getsize(path)
    have = {a["name"]: a for a in existing}
    names, done = [], 0
    for i, (a, b) in enumerate(ranges(size, chunk_size)):
        name = chunk_name(prefix, i)
        names.append(name)
        old = have.get(name)
        if old and old.get("state", "uploaded") == "uploaded" and int(old.get("size", -1)) == b - a:
            done += b - a                       # already there from an earlier try
            continue
        if old:
            gh.delete_asset(old["id"])          # half-finished: send this piece again

        def tick(sent, base=done):
            if progress and size:
                progress(int((base + sent) * 100 / size))
        gh.upload_asset(release, name, b - a, read_range(path, a, b), tick)
        done += b - a
    if progress:
        progress(100)
    return names


def receive(gh, chunks, dest, progress=None):
    """Downloads the pieces (in order) and joins them into dest (written as dest.part first)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    total = sum(int(a.get("size", 0)) for a in chunks) or 1
    got = [0]
    try:
        with open(tmp, "wb") as out:
            for a in chunks:
                start = out.tell()

                def tick(n, base=got[0]):
                    if progress:
                        progress(min(100, int((base + n) * 100 / total)))
                gh.download_asset(a["id"], out.write, tick)
                if out.tell() - start != int(a.get("size", out.tell() - start)):
                    raise IOError(f"{a['name']} arrived incomplete; it will be tried again")
                got[0] += int(a.get("size", 0))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return dest
