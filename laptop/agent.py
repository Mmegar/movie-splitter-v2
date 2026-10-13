"""
Movie Splitter laptop helper (Windows and Mac). Needs only Python 3.8+ and rclone.

What it does, all by itself, while your laptop is on:
  1. Watches your "To upload" folder. When a movie file appears and has stopped growing for
     60 seconds (so it's finished copying), it moves it to Google Drive "To edit", where the
     cutter picks it up. The file is removed from the laptop after a successful upload.
  2. Reads the wish list you edit in the iPhone app (config/wishlist.txt). For each title it
     looks on archive.org for a PUBLIC-DOMAIN copy of that film. If there is one, it downloads
     it and uploads it like step 1. If not, the app shows "not available": add the file to the
     laptop folder yourself instead. It never uses torrents or any other source.
  3. Every 5 minutes (and whenever something changes) it tells the app what it's doing
     (laptop.json on the "status" branch of your GitHub repo).

Settings: config.json next to this file (copy config.example.json). Log: agent.log next to it.
Run:  python agent.py            (keeps running; Ctrl+C to stop)
      python agent.py --once     (one check, then stop: handy for testing)
"""
import argparse
import base64
import json
import logging
import logging.handlers
import math
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".webm", ".mpg", ".mpeg", ".ts", ".ogv"}
UPLOAD_EXT = VIDEO_EXT | {".srt"}          # a .srt with the same name as the movie is used for subtitles
PARTIAL_EXT = {".part", ".partial", ".crdownload", ".download", ".tmp", ".!ut", ".opdownload"}
NOT_AVAILABLE = "Not public domain - add the file to your laptop folder instead"
ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_META = "https://archive.org/metadata/"
GITHUB_API = "https://api.github.com"
DEFAULT_PD_COLLECTIONS = ["feature_films", "film_noir", "scifi_horror", "silent_films", "comedy_films",
                          "classic_cartoons", "prelinger", "publicmovies212", "moviesandfilms_pd"]
DEFAULTS = {
    "watch_folder": str(Path.home() / "Movies" / "To upload"),
    "download_folder": str(Path.home() / "Movies" / "From archive.org"),
    "rclone_path": "rclone",
    "rclone_remote": "gdrive",
    "drive_folder": "TikTok automation v2",
    "github_owner": "",
    "github_repo": "movie-splitter-v2",
    "github_token": "",
    "poll_seconds": 15,
    "stable_seconds": 60,
    "wishlist_minutes": 10,
    "heartbeat_minutes": 5,
    "max_download_mb": 4000,
    "public_domain_collections": DEFAULT_PD_COLLECTIONS,
}
log = logging.getLogger("agent")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- HTTP (tests replace http_request)
def http_request(method, url, headers=None, body=None, timeout=60):
    """Returns (status code, response bytes). Never raises for HTTP error codes."""
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=dict({"User-Agent": "movie-splitter-laptop"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_json(url):
    code, data = http_request("GET", url)
    if code != 200:
        raise RuntimeError(f"{url} answered {code}")
    return json.loads(data.decode("utf-8"))


# ---------------------------------------------------------------- GitHub (laptop.json + wish list)
class GitHub:
    def __init__(self, owner, repo, token):
        self.base = f"{GITHUB_API}/repos/{owner}/{repo}"
        self.token = token

    def call(self, method, path, body=None, accept="application/vnd.github+json"):
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode() if body is not None else None
        if data:
            headers["Content-Type"] = "application/json"
        return http_request(method, self.base + path, headers, data)

    def read_text(self, path, ref=None):
        """A file's text from the repo (default branch unless ref), or None if it isn't there."""
        q = f"?ref={urllib.parse.quote(ref)}" if ref else ""
        code, data = self.call("GET", f"/contents/{path}{q}", accept="application/vnd.github.raw")
        if code == 404:
            return None
        if code != 200:
            raise RuntimeError(f"GitHub answered {code} for {path}: {data[:200]!r}")
        return data.decode("utf-8")

    def _sha(self, path, branch):
        code, data = self.call("GET", f"/contents/{path}?ref={urllib.parse.quote(branch)}")
        return json.loads(data).get("sha") if code == 200 else None

    def _make_orphan_branch(self, branch, path, text, message):
        """Makes a brand-new branch holding just this one file (like 'git checkout --orphan')."""
        code, data = self.call("POST", "/git/trees", {"tree": [
            {"path": path, "mode": "100644", "type": "blob", "content": text}]})
        if code != 201:
            raise RuntimeError(f"couldn't make the {branch} branch ({code}): {data[:200]!r}")
        code, data = self.call("POST", "/git/commits",
                               {"message": message, "tree": json.loads(data)["sha"], "parents": []})
        if code != 201:
            raise RuntimeError(f"couldn't make the {branch} branch ({code}): {data[:200]!r}")
        code, data = self.call("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": json.loads(data)["sha"]})
        return code in (201, 422)    # 422: someone else made it a moment ago

    def write_text(self, path, text, branch, message):
        """Saves a file on a branch, coping with someone else saving in between (409/422)."""
        for attempt in range(5):
            sha = self._sha(path, branch)
            body = {"message": message, "branch": branch,
                    "content": base64.b64encode(text.encode("utf-8")).decode()}
            if sha:
                body["sha"] = sha
            code, data = self.call("PUT", f"/contents/{path}", body)
            if code in (200, 201):
                return True
            msg = data.decode("utf-8", "replace").lower()
            if code == 404 or (code == 422 and "branch" in msg and "not found" in msg):
                code_b, _ = self.call("GET", f"/branches/{urllib.parse.quote(branch)}")
                if code_b == 404:
                    if self._make_orphan_branch(branch, path, text, message):
                        return True
                    continue
            if code in (409, 422):
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"GitHub answered {code}: {msg[:200]}")
        raise RuntimeError("GitHub kept refusing the save (someone else saving at the same time?)")


# ---------------------------------------------------------------- wish list + archive.org
def parse_wishlist(text):
    """'Nosferatu (1922)' -> ('Nosferatu (1922)', 'Nosferatu', 1922). Skips blanks and # comments."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.*?)\s*[\(\[]\s*(\d{4})\s*[\)\]]\s*$", line)
        title, year = (m.group(1).strip(), int(m.group(2))) if m else (line, None)
        if title:
            out.append((line, title, year))
    return out


def norm(s):
    s = str(s or "").lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^(the|a|an) ", "", s)


def _first(v):
    return (v[0] if v else "") if isinstance(v, list) else (v or "")


def _as_list(v):
    return [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])


def public_domain(doc, pd_collections):
    """True only if archive.org says this item is public domain (licence, rights, or a public-domain film collection)."""
    lic = " ".join(_as_list(doc.get("licenseurl"))).lower()
    rights = " ".join(_as_list(doc.get("rights"))).lower()
    if "publicdomain" in lic or "/zero/" in lic:
        return True
    if lic:
        return False          # some other licence (e.g. Creative Commons with conditions): not public domain
    if "public domain" in rights:
        return True
    if re.search(r"copyright|all rights reserved|©", rights):
        return False
    cols = {c.lower() for c in _as_list(doc.get("collection"))}
    return bool(cols & {c.lower() for c in pd_collections})


def doc_year(doc):
    for key in ("year", "date"):
        m = re.search(r"(1[89]\d{2}|20\d{2})", str(_first(doc.get(key))))
        if m:
            return int(m.group(1))
    return None


def title_matches(want, have):
    have = str(_first(have))
    base = re.split(r"\s*[:(\[]\s*|\s+[-–]\s+", have)[0]
    return norm(want) in (norm(have), norm(base))


def search_archive(title, year, pd_collections):
    """The best public-domain archive.org film matching this title (and year), or None."""
    q = f'title:("{title.replace(chr(34), " ")}") AND mediatype:(movies)'
    params = [("q", q)] + [("fl[]", f) for f in ("identifier", "title", "year", "date", "licenseurl", "rights",
                                                 "collection", "downloads")]
    params += [("rows", "50"), ("sort[]", "downloads desc"), ("output", "json")]
    docs = http_json(ARCHIVE_SEARCH + "?" + urllib.parse.urlencode(params)).get("response", {}).get("docs", [])
    best, best_score = None, -1e9
    for d in docs:
        if not title_matches(title, d.get("title")) or not public_domain(d, pd_collections):
            continue
        y = doc_year(d)
        if year and y and abs(y - year) > 1:
            continue
        score = math.log10(1 + float(d.get("downloads") or 0))
        score += 3 if {c.lower() for c in _as_list(d.get("collection"))} & {c.lower() for c in pd_collections} else 0
        score += 2 if (year and y == year) else (1 if year and y else 0)
        if score > best_score:
            best, best_score = d, score
    return best


def best_video_file(identifier, max_mb, pd_collections):
    """(download url, file name, size in bytes) of the best video in an archive.org item; prefers mp4."""
    meta = http_json(ARCHIVE_META + urllib.parse.quote(identifier))
    md = meta.get("metadata", {}) or {}
    if (md.get("licenseurl") or md.get("rights")) and not public_domain(md, pd_collections):
        raise LookupError("item is not public domain")
    vids = []
    for f in meta.get("files", []):
        name = f.get("name", "")
        size = int(f.get("size", 0) or 0)
        if Path(name).suffix.lower() in VIDEO_EXT and (not size or size <= max_mb * 1e6):
            vids.append((name.lower().endswith(".mp4"), size, name))
    if not vids:
        raise LookupError("no video file small enough")
    is_mp4, size, name = max(vids)          # mp4 first, then the biggest (best quality)
    return f"https://archive.org/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(name)}", name, size


def download(url, dest, size=0, progress=None, chunk=1 << 20):
    """Downloads to dest, resuming a half-finished '<dest>.part' if there is one."""
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    if dest.exists() and (not size or dest.stat().st_size == size):
        return dest            # already downloaded (the upload must have failed last time)
    have = part.stat().st_size if part.exists() else 0
    if size and have >= size:
        os.replace(part, dest)
        return dest
    headers = {"User-Agent": "movie-splitter-laptop"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        mode = "ab" if (have and r.status == 206) else "wb"
        have = have if mode == "ab" else 0
        total = size or (have + int(r.headers.get("Content-Length") or 0))
        with open(part, mode) as f:
            while True:
                block = r.read(chunk)
                if not block:
                    break
                f.write(block)
                have += len(block)
                if progress and total:
                    progress(min(100, int(have * 100 / total)))
    if size and have != size:
        raise IOError(f"download incomplete ({have} of {size} bytes); it will carry on next time")
    os.replace(part, dest)
    return dest


def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|%]', "", name).strip() or "movie"


# ---------------------------------------------------------------- watch folder
class Watcher:
    """Finds files in the folder that have stopped changing size for `stable` seconds."""

    def __init__(self, folder, stable=60):
        self.folder, self.stable = Path(folder), stable
        self.seen = {}         # path -> (size, mtime, unchanged since)

    def candidates(self):
        if not self.folder.is_dir():
            return []
        out = []
        for p in sorted(self.folder.iterdir()):
            if (p.is_file() and not p.name.startswith((".", "~"))
                    and p.suffix.lower() in UPLOAD_EXT and p.suffix.lower() not in PARTIAL_EXT):
                out.append(p)
        return out

    def ready(self, now=None):
        now = time.time() if now is None else now
        found, current = [], set()
        for p in self.candidates():
            try:
                st = p.stat()
            except OSError:
                continue
            current.add(p)
            sig = (st.st_size, st.st_mtime)
            old = self.seen.get(p)
            if not old or old[:2] != sig:
                self.seen[p] = sig + (now,)
            elif st.st_size > 0 and now - old[2] >= self.stable and self._not_locked(p):
                found.append(p)
        for p in list(self.seen):
            if p not in current:
                del self.seen[p]
        # movies before subtitles, so a .srt never arrives before its movie
        return sorted(found, key=lambda p: (p.suffix.lower() == ".srt", p.name))

    def forget(self, p):
        self.seen.pop(p, None)

    @staticmethod
    def _not_locked(p):
        """On Windows a file that's still being copied can't be opened for writing."""
        try:
            with open(p, "ab"):
                return True
        except OSError:
            return False


# ---------------------------------------------------------------- the agent
class Agent:
    def __init__(self, cfg, config_dir=HERE):
        self.cfg = dict(DEFAULTS, **cfg)
        self.dir = Path(config_dir)
        self.lock = threading.RLock()
        self.watch = Path(self.cfg["watch_folder"]).expanduser()
        self.downloads = Path(self.cfg["download_folder"]).expanduser()
        self.watcher = Watcher(self.watch, float(self.cfg["stable_seconds"]))
        self.github = GitHub(self.cfg["github_owner"], self.cfg["github_repo"], self.cfg["github_token"])
        self.memory_file = self.dir / "agent_memory.json"
        self.memory = self._load_memory()
        self.uploading = None
        self.online = True
        self.last_push, self.last_sent, self.progress_changed = 0.0, None, False
        self.child = None

    # ---- memory (what's been done, kept between restarts)
    def _load_memory(self):
        try:
            m = json.loads(self.memory_file.read_text(encoding="utf-8"))
        except Exception:
            m = {}
        m.setdefault("uploaded", [])
        m.setdefault("wishlist", {})
        m.setdefault("order", [])
        return m

    def _save_memory(self):
        tmp = self.memory_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.memory, indent=1), encoding="utf-8")
        os.replace(tmp, self.memory_file)

    # ---- rclone
    def remote_to_edit(self):
        folder = self.cfg["drive_folder"].strip("/")
        return f"{self.cfg['rclone_remote']}:{folder + '/' if folder else ''}To edit"

    def rclone_move(self, path):
        """Moves one file to Drive 'To edit', updating the upload percentage as it goes."""
        cmd = [self.cfg["rclone_path"], "moveto", str(path), f"{self.remote_to_edit()}/{path.name}",
               "--use-json-log", "--stats", "2s", "--stats-log-level", "NOTICE"]
        flags = 0x08000000 if os.name == "nt" else 0          # no black window on Windows
        log.info("Uploading %s", path.name)
        self.child = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                      errors="replace", creationflags=flags)
        tail = []
        for line in self.child.stderr:
            tail = (tail + [line.strip()])[-5:]
            try:
                st = json.loads(line).get("stats") or {}
            except ValueError:
                continue
            if st.get("totalBytes"):
                self.set_uploading(path.name, int(st.get("bytes", 0) * 100 / st["totalBytes"]))
        code = self.child.wait()
        self.child.stderr.close()
        self.child = None
        if code != 0:
            raise RuntimeError(f"rclone failed ({code}): {' | '.join(tail)[-300:]}")

    def set_uploading(self, name, percent):
        with self.lock:
            new = {"file": name, "percent": max(0, min(100, percent))} if name else None
            if new != self.uploading:
                self.uploading = new
                self.progress_changed = True
        self.heartbeat()

    def upload(self, path, source="laptop"):
        size_mb = round(path.stat().st_size / 1e6)
        self.set_uploading(path.name, 0)
        try:
            self.rclone_move(path)
        finally:
            self.set_uploading(None, 0)
        with self.lock:
            self.memory["uploaded"] = ([{"file": path.name, "at": now_iso(), "size_mb": size_mb, "source": source}]
                                       + [u for u in self.memory["uploaded"] if u.get("file") != path.name])[:50]
            self._save_memory()
        log.info("Uploaded %s (%s MB)", path.name, size_mb)
        self.heartbeat(force=True)

    def check_folder(self):
        for p in self.watcher.ready():
            try:
                self.upload(p, "laptop")
            except Exception as e:
                log.error("Upload of %s failed, will try again: %s", p.name, e)
            self.watcher.forget(p)

    # ---- wish list
    def check_wishlist(self):
        try:
            text = self.github.read_text("config/wishlist.txt")
        except Exception as e:
            log.warning("Couldn't read the wish list from GitHub: %s", e)
            return
        items = parse_wishlist(text)
        with self.lock:
            self.memory["order"] = [line for line, _, _ in items]
            self._save_memory()
        self.heartbeat()
        for line, title, year in items:
            self.handle_wish(line, title, year)

    def _wish(self, line, **changes):
        with self.lock:
            entry = self.memory["wishlist"].setdefault(line, {"title": line, "status": "searching",
                                                              "source_url": None, "note": None})
            entry.update(changes)
            self._save_memory()
            only_progress = set(changes) == {"percent"}
            if only_progress:
                self.progress_changed = True
        self.heartbeat(force=not only_progress)

    def handle_wish(self, line, title, year):
        entry = self.memory["wishlist"].get(line) or {}
        st, checked = entry.get("status"), entry.get("checked", 0)
        if st == "uploaded":
            return             # done (a half-finished download carries on where it stopped)
        if st == "not_available" and time.time() - checked < 24 * 3600:
            return             # look again once a day
        if st == "failed" and time.time() - checked < 3600:
            return             # try again after an hour
        pd = self.cfg["public_domain_collections"]
        try:
            self._wish(line, status="searching", note=None, checked=time.time())
            doc = search_archive(title, year, pd)
            if not doc:
                log.info("Wish list: %s is not available as public domain", line)
                self._wish(line, status="not_available", source_url=None, note=NOT_AVAILABLE)
                return
            ident = doc["identifier"]
            page = f"https://archive.org/details/{ident}"
            try:
                url, ia_name, size = best_video_file(ident, float(self.cfg["max_download_mb"]), pd)
            except LookupError as e:
                self._wish(line, status="not_available", source_url=page, note=f"{NOT_AVAILABLE} ({e})")
                return
            self._wish(line, status="found", source_url=page)
            y = year or doc_year(doc)
            name = safe_filename(f"{title} ({y})" if y else title) + Path(ia_name).suffix.lower()
            self.downloads.mkdir(parents=True, exist_ok=True)
            log.info("Wish list: downloading %s from %s", line, url)
            self._wish(line, status="downloading", percent=0)
            last = [0.0]

            def progress(pc):
                if time.time() - last[0] > 5:
                    last[0] = time.time()
                    self._wish(line, percent=pc)
            local = download(url, self.downloads / name, size, progress)
            self.upload(local, "wishlist")
            self._wish(line, status="uploaded", percent=None, note=None)
        except Exception as e:
            log.error("Wish list: %s failed: %s", line, e)
            self._wish(line, status="failed", percent=None, note=str(e)[:200])

    # ---- laptop.json
    def snapshot(self):
        with self.lock:
            wl = []
            for line in self.memory["order"]:
                e = dict(self.memory["wishlist"].get(line) or {"title": line, "status": "searching",
                                                                "source_url": None, "note": None})
                e.pop("checked", None)
                if e.get("percent") is None:
                    e.pop("percent", None)
                wl.append(e)
            return {"version": 1, "updated": now_iso(), "online": self.online, "host": socket.gethostname(),
                    "watch_folder": str(self.watch).replace("\\", "/"), "uploading": self.uploading,
                    "uploaded": self.memory["uploaded"][:20], "wishlist": wl}

    def heartbeat(self, force=False):
        """Saves laptop.json to GitHub when something changed (progress at most every 30 s) or every 5 minutes."""
        with self.lock:
            snap = self.snapshot()
            body = {k: v for k, v in snap.items() if k != "updated"}
            since = time.time() - self.last_push
            changed = body != self.last_sent
            due = since >= float(self.cfg["heartbeat_minutes"]) * 60
            if not (force or due or (changed and (not self.progress_changed or since >= 30))):
                return
            try:
                self.github.write_text("laptop.json", json.dumps(snap, indent=1, ensure_ascii=False) + "\n",
                                       "status", "laptop heartbeat")
                self.last_push, self.last_sent, self.progress_changed = time.time(), body, False
            except Exception as e:
                log.warning("Couldn't update the app (laptop.json): %s", e)
                self.last_push = time.time() - float(self.cfg["heartbeat_minutes"]) * 60 + 60   # retry in 1 min

    def stop(self):
        self.online = False
        if self.child:
            self.child.terminate()
        self.uploading = None
        self.heartbeat(force=True)


def load_config(path):
    path = Path(path)
    if not path.exists():
        sys.exit(f"No settings file at {path}. Copy config.example.json to config.json and fill it in.")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def setup_logging(folder):
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(Path(folder) / "agent.log", maxBytes=1_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr:                       # (no console when started hidden on Windows)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Movie Splitter laptop helper")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--once", action="store_true", help="check once (folder + wish list), then stop")
    args = ap.parse_args(argv)
    cfg_path = Path(args.config)
    setup_logging(cfg_path.parent)
    agent = Agent(load_config(cfg_path), cfg_path.parent)
    agent.watch.mkdir(parents=True, exist_ok=True)
    log.info("Started. Watching %s", agent.watch)

    def stop_signal(*_):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, stop_signal)   # Mac: launchd stopping the helper
    except (ValueError, AttributeError):
        pass
    try:
        agent.heartbeat(force=True)
        if args.once:
            agent.check_wishlist()
            agent.watcher.ready()
            if agent.watcher.seen:                   # look again once they've had time to settle
                time.sleep(float(agent.cfg["stable_seconds"]) + 0.5)
            agent.check_folder()
            agent.stop()
            return 0

        def every(minutes, fn):
            while True:
                time.sleep(float(minutes) * 60)
                try:
                    fn()
                except Exception as e:
                    log.error("%s: %s", fn.__name__, e)
        threading.Thread(target=every, args=(agent.cfg["wishlist_minutes"], agent.check_wishlist),
                         daemon=True).start()
        threading.Thread(target=every, args=(agent.cfg["heartbeat_minutes"], lambda: agent.heartbeat(force=True)),
                         daemon=True).start()
        threading.Thread(target=agent.check_wishlist, daemon=True).start()   # first look straight away
        while True:
            try:
                agent.check_folder()
            except Exception as e:
                log.error("Checking the folder failed: %s", e)
            time.sleep(float(agent.cfg["poll_seconds"]))
    except KeyboardInterrupt:
        log.info("Stopping.")
        agent.stop()
        return 0


if __name__ == "__main__":
    sys.exit(main())
