"""
Small previews of every part (and a strip of thumbnails + the subtitles of the whole movie), so
the iPhone app can play a part and pick a scene for a custom clip.

The repo is public, so everything is ENCRYPTED (AES-256-GCM) before it's stored on the
"previews" branch. The key is made from your ntfy name (the NTFY_TOPIC secret on GitHub,
which the app also knows), so nobody else can watch them. No NTFY_TOPIC = no previews.
Only the newest `preview_keep_movies` movies keep their previews; the branch is rewritten
each time with no history, so it doesn't grow.

File format: b"MSP1" + 12-byte nonce + ciphertext (with the 16-byte GCM tag at the end).
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

BRANCH = "previews"
MAGIC = b"MSP1"


def key_for(topic):
    return hashlib.sha256(("movie-splitter-preview:" + (topic or "")).encode("utf-8")).digest()


def encrypt(data, topic):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key_for(topic)).encrypt(nonce, data, None)


def decrypt(blob, topic):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(MAGIC):
        raise ValueError("not a preview file")
    return AESGCM(key_for(topic)).decrypt(blob[4:16], blob[16:], None)


def part_path(movie_id, mode, n):
    return f"{movie_id}/{mode}-{int(n):03}.bin"


class Previews:
    """Collects previews during a run and keeps the 'previews' branch up to date."""

    def __init__(self, gh, topic, keep=3, enabled=True, log=print):
        self.gh, self.topic, self.keep, self.log = gh, (topic or "").strip(), max(1, int(keep or 1)), log
        self.enabled = bool(enabled and self.topic)
        self.files = None            # {path: sha} currently on the branch (loaded on first use)
        self.order = []              # movie ids, newest first

    def _load(self):
        if self.files is None:
            self.files = self.gh.branch_files(BRANCH)
            try:
                meta = json.loads(self._read_plain("index.json") or "{}")
                self.order = [m for m in meta.get("movies", []) if isinstance(m, str)]
            except Exception:
                self.order = []

    def _read_plain(self, path):
        text, _ = self.gh.read_file(path, BRANCH)
        return text

    def _publish(self, message):
        index = json.dumps({"movies": self.order, "format": "MSP1 AES-256-GCM, key = sha256('movie-splitter-preview:'"
                                                           " + ntfy topic)"}, indent=1).encode()
        self.files["index.json"] = self.gh.create_blob(index)
        self.gh.replace_branch(BRANCH, self.files, message)

    def start_movie(self, movie_id):
        """Puts this movie first and drops previews of movies beyond `keep`."""
        if not self.enabled:
            return
        try:
            self._load()
            self.order = [movie_id] + [m for m in self.order if m != movie_id]
            gone = set(self.order[self.keep:])
            self.order = self.order[:self.keep]
            self.files = {p: s for p, s in self.files.items() if p.split("/", 1)[0] not in gone}
        except Exception as e:
            self.log(f"(previews: couldn't read the previews branch: {e})")

    def add(self, path, data, message="preview"):
        """Encrypts and stores one file. Returns its path, or None if previews are off / it failed."""
        if not self.enabled:
            return None
        try:
            self._load()
            self.files[path] = self.gh.create_blob(encrypt(data, self.topic))
            self._publish(message)
            return path
        except Exception as e:
            self.log(f"(preview not saved: {e})")
            return None


# ---------------------------------------------------------------- making the files (ffmpeg)
def make_part_preview(src, dest):
    """A small copy of a finished 1080x1920 part: 360x640, low bitrate, mono (about 2-3 MB a minute)."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-vf", "scale=360:640", "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                    "-maxrate", "450k", "-bufsize", "900k", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-movflags", "+faststart", str(dest)], check=True)
    return Path(dest)


def make_thumbs(src, total, workdir, cols=10, width=160):
    """One JPEG with a small frame every few seconds (at most 360 frames). Returns (jpeg bytes, info)."""
    every = max(5.0, round(total / 360.0, 1))
    count = max(1, int(total // every))
    rows = (count + cols - 1) // cols
    out = Path(workdir) / "thumbs.jpg"
    height = int(width * 9 / 16)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-skip_frame", "nokey", "-i", str(src),
                    "-vf", f"fps=1/{every},scale={width}:{height}:force_original_aspect_ratio=decrease,"
                           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,tile={cols}x{rows}",
                    "-frames:v", "1", "-q:v", "6", str(out)], check=True)
    return out.read_bytes(), {"every": every, "count": count, "cols": cols, "w": width, "h": height}
