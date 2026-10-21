"""
Movie Splitter laptop helper (Windows and Mac). Needs only Python 3.9+ (nothing to pip install;
the `composio` package is only needed if you switch on automatic TikTok posting).

What it does, all by itself, while your laptop is on:
  1. SEND: watches your "To upload" folder. When a movie has stopped growing for 60 seconds
     (finished copying) it's sent to your GitHub repo (a draft release, which only you can see),
     together with a same-name .srt subtitle file if there is one. Then it starts the cutter.
  2. WISH LIST: titles you add in the iPhone app are looked up on archive.org. Only
     PUBLIC-DOMAIN films are downloaded, then sent like step 1. Anything else shows
     "not available": add the file to the laptop folder yourself instead.
  3. RECEIVE: the finished parts come back the same way; they're saved into
     <output folder>/Ready to post/<movie>/<mode>/, with "_parts list.txt" and "captions.txt".
     Then the copies on GitHub are deleted and the original movie goes to <output folder>/Done
     (setting "original_after": "move", "keep" or "delete").
  4. POST (optional): at your posting times it posts the next part to TikTok through Composio.
  5. Tells the app what it's doing (laptop.json), every 5 minutes and whenever something changes.

Settings: config.json next to this file (copy config.example.json). Log: agent.log next to it.
Run:  python agent.py            (keeps running; Ctrl+C to stop)
      python agent.py --setup    (first time: checks GitHub and shows the one secret to add there)
      python agent.py --once     (one round of everything, then stop: handy for testing)
      python agent.py --post-now [--dry-run]   (post the next part now / just show which)
"""
import argparse
import hashlib
import json
import logging
import logging.handlers
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))     # the shared code lives in the repo folder above this one
sys.path.insert(0, str(HERE))

try:
    import archive          # noqa: E402
    import ghapi            # noqa: E402
    import names            # noqa: E402
    import posting          # noqa: E402
    import settings         # noqa: E402
    import status           # noqa: E402
    import transport        # noqa: E402
except Exception:           # write why it couldn't start into agent.log (no window when started hidden)
    import traceback
    with open(HERE / "agent.log", "a", encoding="utf-8") as _f:
        _f.write(f"{datetime.now()} ERROR The helper couldn't start:\n{traceback.format_exc()}\n")
    raise

VIDEO_EXT = archive.VIDEO_EXT
PARTIAL_EXT = {".part", ".partial", ".crdownload", ".download", ".tmp", ".!ut", ".opdownload"}
WORKFLOW = "make-parts.yml"
LABELS = status.LABELS
DEFAULTS = {
    "watch_folder": str(Path.home() / "Movies" / "To upload"),
    "output_folder": str(Path.home() / "Movies" / "Movie Splitter"),
    "download_folder": str(Path.home() / "Movies" / "From archive.org"),
    "github_owner": "",
    "github_repo": "movie-splitter-v2",
    "github_token": "",
    "github_api_url": ghapi.API,
    "ntfy_topic": "",
    "original_after": "move",          # keep | move (to <output>/Done) | delete
    "poll_seconds": 15,
    "stable_seconds": 60,
    "receive_seconds": 60,
    "wishlist_minutes": 10,
    "heartbeat_minutes": 5,
    "posting_check_minutes": 5,
    "max_download_mb": 4000,
    "chunk_mb": 250,                   # sent in 250 MB pieces: an interrupted send only redoes one piece
    "public_domain_collections": archive.DEFAULT_PD_COLLECTIONS,
    "composio_api_key": "",
    "composio_user_id": "",
    "composio_connected_account_id": "",
    "composio_tiktok_version": "latest",
    "tiktok_username": "",
    "posting_dry_run": False,
    "anthropic_api_key": "",           # for cutting on this laptop: Claude (Haiku) writes the stories
}
PLACEHOLDER = re.compile(r"PASTE|YOUR-|^$")
log = logging.getLogger("agent")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|%]', "", str(name)).strip() or "movie"


def send_to_trash(path):
    """Recycle Bin (Windows) / Bin (Mac). Returns False if that isn't possible."""
    path = Path(path).resolve()
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                            ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16),
                            ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                            ("lpszProgressTitle", wintypes.LPCWSTR)]
            op = SHFILEOPSTRUCTW(None, 3, str(path) + "\0", None, 0x40 | 0x10 | 0x04 | 0x400, False, None, None)
            return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted
        if sys.platform == "darwin":
            bin_dir = Path.home() / ".Trash"
        else:
            bin_dir = Path.home() / ".local" / "share" / "Trash" / "files"
        bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(unique(bin_dir / path.name)))
        return True
    except Exception as e:
        log.warning("Couldn't put %s in the bin: %s", path.name, e)
        return False


def unique(p):
    """p, or 'name (2).ext' etc. if p already exists."""
    p = Path(p)
    n = 2
    while p.exists():
        p = p.with_name(f"{p.stem.rsplit(' (', 1)[0]} ({n}){p.suffix}")
        n += 1
    return p


class Restart(Exception):
    """The helper updated itself and starts again."""


class Paused(Exception):
    """Raised inside a transfer when the app's Pause switch (or 'pause on battery') kicks in."""


def on_battery():
    """True when the laptop is running on battery (Windows / Mac). False if it can't tell."""
    try:
        if os.name == "nt":
            import ctypes

            class SPS(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
            st = SPS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
                return st.ACLineStatus == 0
        elif sys.platform == "darwin":
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
            return "Battery Power" in out
    except Exception:
        pass
    return False


def git_blob_sha(data):
    """The same id GitHub gives a file's content (used to see if a code file changed)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def plain_name(name):
    """A bare file name from the app (no folders, no '..'), or None."""
    name = str(name or "").strip()
    if not name or name != Path(name).name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    return name


def repair_part(path):
    """Removes a movie-length chapter track from a part (see mp4fix.py). True if the file was changed."""
    try:
        import mp4fix
        return mp4fix.fix_file(path)
    except Exception as e:                           # never let a repair stop anything else
        log.warning("Couldn't check %s: %s", Path(path).name, e)
        return False


def file_info(p):
    try:
        st = p.stat()
    except OSError:
        return None
    return {"name": p.name, "size_mb": round(st.st_size / 1e6), "modified":
            datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")}


# files the helper keeps up to date from GitHub by itself (path in the repo -> path here)
CODE_FILES = ["laptop/agent.py", "laptop/archive.py", "ghapi.py", "names.py", "posting.py", "previews.py",
              "settings.py", "status.py", "transport.py", "mp4fix.py", "story.py", "filmcraft.py", "fc_editor.py",
              "process.py", "tracking.py"]   # (process + tracking: for cutting on the laptop)
PACK_SUFFIX = ".editpack.zip"   # an edit plan from GitHub that FilmCraft turns into the video here (story.py)
SHARE_KEEP = 30         # full-quality parts kept on GitHub for the phone to save


def ensure_previews():
    """previews.py (the lock for files sent to the phone) needs the 'cryptography' package: installed the
    first time it's needed (only for "Save to phone")."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        log.info("Installing the 'cryptography' package (needed once for Save to phone)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "cryptography"], timeout=900,
                       capture_output=True)
        import importlib
        importlib.invalidate_caches()
    import previews
    return previews


def find_ffmpeg():
    """ffmpeg on this laptop (PATH, or where winget puts it), or None."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    hits = sorted(base.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe")) if base.is_dir() else []
    return str(hits[-1]) if hits else None


def ensure_cutter_packages():
    """What cutting on this laptop needs (speech-to-text, the narrator voice, Claude): installed once."""
    try:
        import anthropic, edge_tts, faster_whisper, numpy  # noqa: F401,E401
    except ImportError:
        log.info("Installing what cutting needs (once, a few minutes)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "faster-whisper", "edge-tts", "anthropic",
                        "numpy"], timeout=1800, capture_output=True)


# ---------------------------------------------------------------- watch folder
class Watcher:
    """Finds movie files in the folder that have stopped changing size for `stable` seconds."""

    def __init__(self, folder, stable=60):
        self.folder, self.stable = Path(folder), stable
        self.seen = {}         # path -> (size, mtime, unchanged since)

    def candidates(self):
        if not self.folder.is_dir():
            return []
        return [p for p in sorted(self.folder.iterdir())
                if p.is_file() and not p.name.startswith((".", "~"))
                and p.suffix.lower() in VIDEO_EXT and p.suffix.lower() not in PARTIAL_EXT]

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
            elif st.st_size > 0 and now - old[2] >= self.stable and (
                    self._not_locked(p) or (now - old[2] >= max(300, self.stable) and self._readable(p))):
                # A file another program keeps open (a torrent app still seeding, OneDrive syncing, or a
                # read-only file) can't be opened for writing; if it hasn't changed for 5 minutes and can be
                # read, it's finished: send it anyway.
                found.append(p)
        for p in list(self.seen):
            if p not in current:
                del self.seen[p]
        return found

    def forget(self, p):
        self.seen.pop(p, None)

    @staticmethod
    def _readable(p):
        try:
            with open(p, "rb") as f:
                f.read(1)
            return True
        except OSError:
            return False

    @staticmethod
    def _not_locked(p):
        """On Windows a file that's still being copied can't be opened for writing."""
        try:
            with open(p, "ab"):
                return True
        except OSError:
            return False


def matching_srt(movie):
    """'Film.mkv' -> 'Film.srt' next to it (any capitalisation), or None."""
    want = movie.stem.lower() + ".srt"
    return next((p for p in movie.parent.iterdir() if p.name.lower() == want and p.is_file()), None)


# ---------------------------------------------------------------- the agent
class Agent:
    def __init__(self, cfg, config_dir=HERE):
        self.cfg = dict(DEFAULTS, **cfg)
        self.dir = Path(config_dir)
        self.lock = threading.RLock()
        self.watch = Path(self.cfg["watch_folder"]).expanduser()
        self.output = Path(self.cfg["output_folder"]).expanduser()
        self.ready_dir = self.output / "Ready to post"
        self.downloads = Path(self.cfg["download_folder"]).expanduser()
        self.watcher = Watcher(self.watch, float(self.cfg["stable_seconds"]))
        repo = f"{self.cfg['github_owner']}/{self.cfg['github_repo']}"
        self.gh = ghapi.GitHub(repo, self.cfg["github_token"], self.cfg["github_api_url"])
        self.memory_file = self.dir / "agent_memory.json"
        self.memory = self._load_memory()
        self.transfers = {}            # (direction, file) -> {direction, movie, file, percent}
        self.building = False          # FilmCraft is making an edit (build_edits)
        self.cutting = None            # the movie being cut on this laptop right now (cut_movie)
        self.to_send = []              # wish-list downloads waiting to be sent: (path, wish line)
        self.online = True
        self.last_push, self.last_sent, self.progress_changed = 0.0, None, False
        self.last = {"receive": 0.0, "posting": 0.0, "cleanup": 0.0, "settings": 0.0, "commands": 0.0,
                     "update": time.time() - 240, "battery": 0.0}
        self.battery = False
        self.rate = [0.0, 0]                 # upload speed limit: (window start, bytes sent in it)
        self.cmd_status = {}                 # command id -> {status, note}: shown in the app
        self.cmd_lock = threading.Lock()
        self.remote_settings = settings.load(text="{}")
        self.status_ready = False

    # ---- memory (what's been done, kept between restarts)
    def _load_memory(self):
        try:
            m = json.loads(self.memory_file.read_text(encoding="utf-8"))
        except Exception:
            m = {}
        for k, v in (("uploaded", []), ("wishlist", {}), ("order", []), ("sends", {}), ("received", []),
                     ("finished", []), ("originals", {}), ("queue_order", []), ("commands_done", {}),
                     ("styles", {})):
            m.setdefault(k, v)
        return m

    def _save_memory(self):
        with self.lock:
            tmp = self.memory_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.memory, indent=1), encoding="utf-8")
            os.replace(tmp, self.memory_file)

    # ---- phone + app messages
    def notify(self, title, message, kind="info", tags=None, event=True):
        """ntfy notification (same topic as GitHub's), plus an event in the app's list."""
        if event:
            self.state_ready()
            status.add_event(kind, title, message, urgent=True)
        topic = self.cfg["ntfy_topic"]
        if not topic:
            return
        body = json.dumps({"topic": topic, "title": title, "message": message, "tags": tags or []}).encode()
        try:
            archive.http_request("POST", "https://ntfy.sh/", {"Content-Type": "application/json"}, body, timeout=15)
        except Exception as e:
            log.warning("Notification not sent: %s", e)

    def state_ready(self):
        """state.json on GitHub (for events and post results), set up once."""
        if not self.status_ready:
            status.use_github(self.gh.repo, self.gh.token, self.gh.api)
            self.status_ready = True

    # ---- transfers (shown in the app)
    def set_transfer(self, direction, file, movie, percent):
        with self.lock:
            k = (direction, file)
            if percent is None:
                self.transfers.pop(k, None)
            else:
                old = self.transfers.get(k)
                self.transfers[k] = {"direction": direction, "movie": movie, "file": file,
                                     "percent": max(0, min(100, int(percent)))}
                if old and old["percent"] == self.transfers[k]["percent"]:
                    return
            self.progress_changed = True
        self.heartbeat(force=percent is None)

    # ---- pause switch, battery, upload speed limit
    def paused_reason(self):
        """Why the laptop is holding off right now (shown in the app), or None."""
        if self.remote_settings.get("paused"):
            return "Paused in the app"
        if self.remote_settings.get("pause_on_battery"):
            if time.time() - self.last["battery"] > 30:
                self.last["battery"] = time.time()
                self.battery = on_battery()
            if self.battery:
                return "Waiting: the laptop is on battery"
        return None

    def gate(self, n, upload=False):
        """Called for every block sent or received: stops on pause, and keeps to the upload limit."""
        reason = self.paused_reason()        # settings are refreshed every minute by a background thread
        if reason:
            raise Paused(reason)
        limit = float(self.remote_settings.get("upload_limit_mbps") or 0)
        if upload and limit > 0:
            now = time.time()
            if now - self.rate[0] > 5 + self.rate[1] / (limit * 125000):
                self.rate = [now, 0]               # a fresh start after a break
            self.rate[1] += n
            wait = self.rate[1] / (limit * 125000) - (now - self.rate[0])
            if wait > 0:
                time.sleep(min(wait, 30))

    # ---- 1. send
    def send_movie(self, path, source="laptop", wish=None, clips=None, movie=None):
        """Sends one movie (and its .srt) to GitHub, then starts the cutter."""
        path = Path(path)
        size = path.stat().st_size
        srt = matching_srt(path)
        with self.lock:
            rid = next((r for r, s in self.memory["sends"].items()
                        if s["path"] == str(path) and s["size"] == size and s["status"] == "sending"), None)
            if not rid:
                rid = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
                self.memory["sends"][rid] = {"path": str(path), "file": path.name, "size": size,
                                             "srt": str(srt) if srt else None, "source": source, "wish": wish,
                                             "status": "sending", "started": now_iso(),
                                             "clips": clips, "movie": movie, "style": self.style_for(path.name)}
                self._save_memory()
            entry = self.memory["sends"][rid]
        tag = transport.INCOMING + rid
        meta = {"kind": "incoming", "id": rid, "file": path.name, "size": size,
                "srt": Path(entry["srt"]).name if entry.get("srt") else None, "source": source,
                "sent": entry["started"], "laptop": socket.gethostname(), "complete": False,
                "style": entry.get("style") or self.style_for(path.name)}
        if entry.get("clips"):
            meta.update(clips=entry["clips"], movie=entry.get("movie"))
        release = transport.find(self.gh.releases(), "incoming", rid)
        if not release:
            release = self.gh.create_release(tag, f"Incoming: {path.name}", json.dumps(meta))
        existing = self.gh.assets(release["id"])
        log.info("Sending %s to GitHub (%s MB)", path.name, round(size / 1e6))
        self.notify("📤 Sending to GitHub", f"{path.name} ({round(size / 1e6)} MB)", event=False,
                    tags=["outbox_tray"])
        half = [False]

        def progress(pc):
            self.set_transfer("send", path.name, path.name, pc)
            if pc >= 50 and not half[0]:
                half[0] = True
                self.notify(f"Sending {path.name} to GitHub: {pc}%", "Halfway there.", event=False)
        chunk = entry.get("chunk")
        if not chunk:                       # a send started by an older version keeps its piece size
            first = next((a for a in existing if a["name"] == transport.chunk_name("movie", 0)), None)
            chunk = int(first["size"]) if first and int(first.get("size", 0)) < size else int(float(self.cfg["chunk_mb"]) * 1e6)
            with self.lock:
                entry["chunk"] = chunk
                self._save_memory()
        try:
            up = lambda n: self.gate(n, upload=True)   # noqa: E731
            transport.send(self.gh, release, path, "movie", existing, chunk, progress, gate=up)
            if entry.get("srt") and Path(entry["srt"]).exists():
                transport.send(self.gh, release, Path(entry["srt"]), "subs", existing, chunk, gate=up)
            self.gh.update_release(release["id"], body=json.dumps(dict(meta, complete=True)))
        finally:
            self.set_transfer("send", path.name, path.name, None)
        with self.lock:
            entry["status"] = "sent"
            self.memory["uploaded"] = ([{"file": path.name, "at": now_iso(), "size_mb": round(size / 1e6),
                                         "source": source}]
                                       + [u for u in self.memory["uploaded"] if u.get("file") != path.name])[:50]
            if wish and wish in self.memory["wishlist"]:
                self.memory["wishlist"][wish].update(status="uploaded", percent=None, note=None)
            self._save_memory()
        log.info("Sent %s", path.name)
        if entry.get("clips"):
            self.notify("📥 Sent to GitHub for clips", f"{path.name}: {len(entry['clips'])} custom clip(s) start soon.",
                        kind="new_file", tags=["scissors"])
        else:
            self.notify("📥 Sent to GitHub", f"{path.name}: cutting starts soon. The parts come back to "
                        f"{self.ready_dir}.", kind="new_file", tags=["inbox_tray"])
        self.heartbeat(force=True)
        self.start_cutter()

    def start_cutter(self):
        if self.remote_settings.get("paused"):
            return
        try:
            if not self.gh.run_active(WORKFLOW):
                self.gh.dispatch(WORKFLOW)
                log.info("Started the cutter on GitHub")
        except Exception as e:
            log.warning("Couldn't start the cutter (it also runs once a day by itself): %s", e)

    def waiting_paths(self):
        """Originals already sent (or kept after their parts came back): don't send them again."""
        return {s["path"] for s in self.memory["sends"].values() if s["status"] in ("sent", "done", "cutting")}

    def queue_sorted(self, paths):
        """Files in the order chosen in the app (anything not ordered goes last, by name)."""
        order = {n: i for i, n in enumerate(self.memory.get("queue_order") or [])}
        return sorted(paths, key=lambda p: (order.get(p.name, 10 ** 6), p.name.lower()))

    # ---- 1b. or cut it right here (setting cut_on: laptop): no upload at all
    def laptop_cutting(self):
        return str(self.remote_settings.get("cut_on", "laptop")).lower() == "laptop" and bool(find_ffmpeg())

    def cut_movie(self, path):
        """Cuts one movie on this laptop (process.py --here, as its own program), parts straight into Ready to post."""
        path = Path(path)
        srt = matching_srt(path)
        rid = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        with self.lock:
            self.memory["sends"][rid] = {"path": str(path), "file": path.name, "size": path.stat().st_size,
                                         "srt": str(srt) if srt else None, "source": "laptop", "status": "cutting",
                                         "started": now_iso(), "style": self.style_for(path.name)}
            self._save_memory()
        ensure_cutter_packages()
        settings_file = self.dir / "settings-for-cutting.json"
        settings_file.write_text(json.dumps(self.remote_settings), encoding="utf-8")
        env = dict(os.environ, SETTINGS_FILE=str(settings_file), STATUS_BACKEND="api",
                   GITHUB_REPOSITORY=f"{self.cfg['github_owner']}/{self.cfg['github_repo']}",
                   GITHUB_TOKEN=self.cfg["github_token"], NTFY_TOPIC=self.cfg.get("ntfy_topic") or "-",
                   PYTHONIOENCODING="utf-8")
        if self.cfg.get("anthropic_api_key"):
            env["ANTHROPIC_API_KEY"] = self.cfg["anthropic_api_key"]
        ff = find_ffmpeg()
        env["PATH"] = str(Path(ff).parent) + os.pathsep + env.get("PATH", "")
        if os.name == "nt":
            env.setdefault("FONT_FILE", r"'C\:/Windows/Fonts/arialbd.ttf'")   # quoted: the drive's ":" in ffmpeg
        cmd = [sys.executable, str(HERE.parent / "process.py"), "--here", str(path), "--out", str(self.ready_dir)]
        if srt:
            cmd += ["--srt", str(srt)]
        log.info("Cutting %s on this laptop", path.name)
        with open(self.dir / "cutting.log", "a", encoding="utf-8") as logf:
            code = subprocess.run(cmd, cwd=str(HERE.parent), env=env, stdout=logf, stderr=subprocess.STDOUT).returncode
        with self.lock:
            self.memory["sends"][rid]["status"] = "sent"
            self._save_memory()
        self.finish_original(rid, failed=code != 0, error=None if code == 0 else "see laptop/cutting.log",
                             movie=names.clean_name(path.name))

    def _cut_in_background(self, path):
        try:
            self.cut_movie(path)
        except Exception as e:
            log.error("Cutting %s failed: %s", Path(path).name, e)
        finally:
            self.cutting = None

    def check_folder(self):
        busy = self.waiting_paths()
        for p in self.queue_sorted(self.watcher.ready()):
            if str(p) in busy:
                continue
            if self.paused_reason():
                return
            if self.laptop_cutting():
                if not self.cutting:                     # one movie at a time, in the background
                    self.cutting = str(p)
                    threading.Thread(target=self._cut_in_background, args=(p,), daemon=True).start()
                    self.watcher.forget(p)
                return
            try:
                self.send_movie(p, "laptop")
            except Paused as e:
                log.info("Sending %s stopped: %s (it carries on later)", p.name, e)
                return
            except Exception as e:
                log.error("Sending %s failed, will try again: %s", p.name, e)
            self.watcher.forget(p)
        while self.to_send and not self.paused_reason():
            p, wish = self.to_send.pop(0)
            try:
                self.send_movie(p, "wishlist", wish)
            except Paused:
                self.to_send.insert(0, (p, wish))
                return
            except Exception as e:
                log.error("Sending %s failed, will try again: %s", Path(p).name, e)
                self._wish(wish, status="failed", percent=None, note=str(e)[:200], checked=time.time())

    # ---- 2. wish list
    def check_wishlist(self):
        try:
            text, _ = self.gh.read_file("config/wishlist.txt")
        except Exception as e:
            log.warning("Couldn't read the wish list from GitHub: %s", e)
            return
        items = archive.parse_wishlist(text)
        with self.lock:
            self.memory["order"] = [line for line, _, _ in items]
            self._save_memory()
        try:                       # the on/off switch lives in the app's Settings
            s_text, _ = self.gh.read_file("config/settings.json")
            self.remote_settings = settings.load(text=s_text or "{}", env={})
        except Exception as e:
            log.warning("Couldn't read settings from GitHub: %s", e)
        if self.paused_reason():
            return
        if not self.remote_settings.get("wishlist_archive", True):
            for line, _, _ in items:
                st = (self.memory["wishlist"].get(line) or {}).get("status")
                if st not in ("uploaded", "downloading", "found"):
                    self._wish(line, status="not_available", source_url=None, paused=True,
                               note="archive.org search is switched off (app: Settings > Wish list)")
            self.heartbeat()
            return
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
        if st == "uploaded" or any(w == line for _, w in self.to_send):
            return
        if entry.get("paused"):
            self._wish(line, paused=False, checked=0)   # search was switched back on: look now
        elif st == "not_available" and time.time() - checked < 24 * 3600:
            return             # look again once a day
        if st == "failed" and time.time() - checked < 3600:
            return             # try again after an hour
        pd = self.cfg["public_domain_collections"]
        try:
            self._wish(line, status="searching", note=None, checked=time.time())
            doc = archive.search_archive(title, year, pd)
            if not doc:
                log.info("Wish list: %s is not available as public domain", line)
                self._wish(line, status="not_available", source_url=None, note=archive.NOT_AVAILABLE)
                return
            ident = doc["identifier"]
            page = f"https://archive.org/details/{ident}"
            try:
                url, ia_name, size = archive.best_video_file(ident, float(self.cfg["max_download_mb"]), pd)
            except LookupError as e:
                self._wish(line, status="not_available", source_url=page, note=f"{archive.NOT_AVAILABLE} ({e})")
                return
            self._wish(line, status="found", source_url=page)
            y = year or archive.doc_year(doc)
            name = archive.safe_filename(f"{title} ({y})" if y else title) + Path(ia_name).suffix.lower()
            self.downloads.mkdir(parents=True, exist_ok=True)
            log.info("Wish list: downloading %s from %s", line, url)
            self._wish(line, status="downloading", percent=0)
            last = [0.0]

            def progress(pc):
                if time.time() - last[0] > 5:
                    last[0] = time.time()
                    self._wish(line, percent=pc)
                    if self.paused_reason():
                        raise Paused(self.paused_reason())
            local = archive.download(url, self.downloads / name, size, progress)
            self._wish(line, percent=100)
            with self.lock:
                self.to_send.append((local, line))      # sent by the main loop, one movie at a time
        except Paused as e:
            log.info("Wish list: %s download paused (%s)", line, e)
            self._wish(line, status="found", percent=None, note=f"{e}: carries on later")
        except Exception as e:
            log.error("Wish list: %s failed: %s", line, e)
            self._wish(line, status="failed", percent=None, note=str(e)[:200])

    # ---- 3. receive
    def check_incoming_failures(self, releases):
        """A movie the cutter couldn't do: original goes to <output>/Failed, the GitHub copy is deleted."""
        for r in releases:
            if transport.kind_of(r) != "incoming":
                continue
            meta = transport.body_of(r)
            rid = transport.id_of(r)
            s = self.memory["sends"].get(rid)
            if meta.get("failed") and s and s["status"] == "sent":
                self.gh.delete_release(r["id"], r["tag_name"])
                if s.get("clips"):
                    self.clip_job_over(rid, ok=False, note=meta.get("error") or "the cutter couldn't make it")
                else:
                    self.finish_original(rid, failed=True, error=meta.get("error"))

    def receive_parts(self):
        releases = self.gh.releases()
        self.check_incoming_failures(releases)
        for r in releases:
            if r.get("draft") and transport.kind_of(r) == "parts":
                try:
                    self.receive_release(r)
                except Exception as e:
                    log.error("Receiving parts failed, will try again: %s", e)

    def receive_release(self, r):
        body = transport.body_of(r)
        rid = transport.id_of(r)
        movie = body.get("movie") or rid
        base = self.ready_dir / safe_name(movie)
        assets = {a["name"]: a for a in self.gh.assets(r["id"])}
        received = set(self.memory["received"])
        complete = True
        for p in body.get("parts", []):
            video = base / p["label"] / p["file"]
            # an edit pack: the plan and voice for FilmCraft, which builds the video here (build_edits)
            dest = video.with_name(video.stem + PACK_SUFFIX) if p.get("pack") else video
            rel = f"Ready to post/{safe_name(movie)}/{p['label']}/{p['file']}"
            chunks = [assets[n] for n in p.get("assets", []) if n in assets]
            if chunks and len(chunks) == len(p["assets"]) and all(a.get("state", "uploaded") == "uploaded"
                                                                   for a in chunks):
                log.info("Receiving %s", rel)
                transport.receive(self.gh, chunks, dest,
                                  lambda pc, f=p["file"]: self.set_transfer("receive", f, movie, pc),
                                  gate=self.gate)
                if not p.get("pack"):
                    repair_part(dest)
                if p.get("mode") == "clips" and p.get("caption"):
                    dest.with_name(dest.stem + " - caption.txt").write_text(p["caption"] + "\n", encoding="utf-8")
                self.set_transfer("receive", p["file"], movie, None)
                for a in chunks:
                    self.gh.delete_asset(a["id"])
                with self.lock:
                    self.memory["received"] = (self.memory["received"] + [rel])[-2000:]
                    self._save_memory()
            elif rel not in received and not dest.exists() and not video.exists():
                complete = False                    # still on its way
        self.write_lists(base, body)
        if body.get("done") and complete:
            self.gh.delete_release(r["id"], r["tag_name"])
            n = len(body.get("parts", []))
            log.info("All parts of %s are here", movie)
            self.notify("📥 Parts downloaded", f"{movie}: {n} part{'s' if n != 1 else ''} in {base}"
                        + (f"\n{body['summary']}" if body.get("summary") else ""), tags=["file_folder"])
            with open(self.output / "Finished.txt", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%d %b %Y, %I:%M %p}  -  {movie}  -  {body.get('summary') or n}  -  "
                        f"subtitles: {body.get('subtitles') or '-'}  (original file: {body.get('file')})\n")
            s = self.memory["sends"].get(rid) or {}
            if body.get("clips") or s.get("clips"):
                self.clip_job_over(rid, ok=not body.get("failed"))
            else:
                self.finish_original(rid, failed=bool(body.get("failed")), movie=movie)

    def find_movie_file(self, file_name):
        """The original movie an edit pack was made from, wherever it is on this laptop now."""
        for s in self.memory["sends"].values():
            if Path(s.get("path", "")).name == file_name and Path(s["path"]).is_file():
                return Path(s["path"])
        path, _ = self.find_original(status.movie_id(names.clean_name(file_name)), None)
        if path:
            return path
        for folder in (self.watch, self.output / "Done", self.downloads):
            if (folder / file_name).is_file():
                return folder / file_name
        return None

    def build_edits(self):
        """Edit packs from GitHub -> finished videos, built and exported by FilmCraft from the original
        movie on this laptop. Runs in the background; a pack stays put until its video is made."""
        if self.building or self.paused_reason():
            return
        packs = sorted(self.ready_dir.rglob("*" + PACK_SUFFIX))
        if not packs:
            return
        import fc_editor
        import filmcraft
        if not filmcraft.find_cli():
            if not self.memory.get("filmcraft_warned"):
                self.notify("🎬 FilmCraft needed", "Edits are waiting for FilmCraft. Install it on this laptop "
                            "and they'll be made automatically.", tags=["clapper"])
                with self.lock:
                    self.memory["filmcraft_warned"] = True
                    self._save_memory()
            return
        self.building = True
        try:
            for pack in packs:
                out = pack.with_name(pack.name[:-len(PACK_SUFFIX)] + ".mp4")
                if out.exists():
                    pack.unlink(missing_ok=True)
                    continue
                try:
                    import zipfile
                    with zipfile.ZipFile(pack) as z:
                        file_name = json.loads(z.read("plan.json")).get("movie_file", "")
                    movie = self.find_movie_file(file_name)
                    if not movie:
                        raise FileNotFoundError(f"the original movie ({file_name}) isn't on this laptop any more")
                    tmp = out.with_name(out.stem + ".building.mp4")
                    fc_editor.build_from_pack(pack, tmp, movie, log=log.info)
                    os.replace(tmp, out)
                    pack.unlink(missing_ok=True)
                    log.info("Made %s in FilmCraft", out.name)
                    self.notify("🎬 Edit ready", f"{out.parent.parent.name}: {out.name}", tags=["clapper"])
                except Exception as e:
                    fails = self.memory.setdefault("edit_failures", {})
                    fails[pack.name] = fails.get(pack.name, 0) + 1
                    log.error("FilmCraft couldn't make %s (try %d): %s", out.name, fails[pack.name], e)
                    if fails[pack.name] == 3:
                        self.notify("❌ Edit failed", f"FilmCraft couldn't make {out.name} after 3 tries. The edit pack "
                                    f"is kept in its folder.\n{e}", kind="failed", tags=["x"])
                    if fails[pack.name] >= 3:
                        pack.rename(pack.with_name(pack.name + ".failed"))
                    with self.lock:
                        self._save_memory()
        finally:
            self.building = False

    def write_lists(self, base, body):
        """_parts list.txt and captions.txt in each mode folder, ready for posting by hand."""
        for mode, text in (body.get("lists") or {}).items():
            folder = base / LABELS.get(mode, mode)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "_parts list.txt").write_text(text, encoding="utf-8")
            caps = [f"{'Highlight' if mode == 'highlights' else 'Part'} {p['n']}  ({p['file']})\n{p['caption']}\n"
                    for p in sorted(body.get("parts", []), key=lambda p: p["n"])
                    if p["mode"] == mode and p.get("caption")]
            if caps:
                (folder / "captions.txt").write_text("\n".join(caps), encoding="utf-8")

    def finish_original(self, rid, failed=False, error=None, movie=None):
        """What happens to the original movie once its parts are back (or it failed)."""
        s = self.memory["sends"].get(rid)
        if not s or s["status"] not in ("sent", "sending"):
            return
        how = "move" if failed else str(self.cfg["original_after"]).lower()
        targets = [Path(s["path"])] + ([Path(s["srt"])] if s.get("srt") else [])
        kept = {}
        for f in targets:
            if not f.exists():
                continue
            if how == "delete":
                bin_ok = (self.remote_settings.get("delete_originals") == "bin") and send_to_trash(f)
                if not bin_ok:
                    f.unlink()
            elif how == "move":
                folder = self.output / ("Failed" if failed else "Done")
                folder.mkdir(parents=True, exist_ok=True)
                to = unique(folder / f.name)
                shutil.move(str(f), str(to))
                kept["srt" if f.suffix.lower() == ".srt" else "path"] = str(to)
            else:
                kept["srt" if f.suffix.lower() == ".srt" else "path"] = str(f)
        if kept.get("path") and not failed:          # remembered so custom clips can be made later
            with self.lock:
                mid = status.movie_id(movie or names.clean_name(s["file"]))
                self.memory["originals"][mid] = {"path": kept["path"], "srt": kept.get("srt"), "file": s["file"]}
                self._save_memory()
        with self.lock:
            if failed:
                del self.memory["sends"][rid]          # moving it back to the watch folder tries again
                if s.get("wish") in self.memory["wishlist"]:
                    self.memory["wishlist"][s["wish"]].update(status="failed", note=str(error or "cutting failed")[:200])
            else:
                s["status"] = "done"
                if how != "keep":
                    del self.memory["sends"][rid]
            self._save_memory()
        if failed:
            self.notify("❌ Movie failed", f"{s['file']} couldn't be cut and is in {self.output / 'Failed'}. "
                        f"Move it back into the watch folder to try again.\n{error or ''}",
                        kind="failed", tags=["x"], event=False)
        self.heartbeat(force=True)

    # ---- commands from the app (config/commands.json)
    def folder_for(self, where):
        return {"library": self.downloads, "queue": self.watch, "done": self.output / "Done",
                "failed": self.output / "Failed"}.get(str(where or "").lower())

    def check_commands(self):
        """Does what was tapped in the app: move movies into To edit, reorder, restore/bin, custom clips.
        Runs in its own thread too, so taps are handled even while a movie is being sent."""
        if not self.cmd_lock.acquire(blocking=False):
            return                                  # already being done by the other thread
        try:
            self._check_commands()
        finally:
            self.cmd_lock.release()

    def _check_commands(self):
        try:
            text, _ = self.gh.read_file("config/commands.json")
        except Exception as e:
            log.warning("Couldn't read commands from GitHub: %s", e)
            return
        try:
            cmds = (json.loads(text) if text else {}).get("commands") or []
        except ValueError:
            log.warning("config/commands.json isn't valid JSON")
            return
        done = self.memory["commands_done"]
        clips = {}
        seen = {}                                   # the same part tapped twice: do it once
        for c in cmds:
            if c.get("type") in ("post_part", "share_part", "mark_posted") and str(c.get("id") or "") not in done:
                key = (c.get("type"), c.get("movie_id"), c.get("mode"), str(c.get("n")))
                if key in seen:
                    self.command_over(str(c["id"]), True, "Same as the request just before (done once)")
                    continue
                seen[key] = c["id"]
            cid = str(c.get("id") or "")
            if (not cid or cid in done or cid in self.working_clip_ids() or cid in (self.memory.get("post_jobs") or {})
                    or self.cmd_status.get(cid, {}).get("status") == "working"):
                continue
            kind = c.get("type")
            if kind in ("clip", "post_part", "share_part", "post_all", "share_all") and self.paused_reason():
                self.cmd_status[cid] = {"status": "waiting", "note": f"{self.paused_reason()}: it goes when you resume"}
                continue
            try:
                if kind == "clip":
                    clips.setdefault(str(c.get("movie_id") or ""), []).append(c)
                    continue
                note = self.run_command(kind, c)
                if note is not None or kind != "post_all":
                    self.command_over(cid, True, note)
            except Exception as e:
                log.warning("Command %s (%s) failed: %s", cid, kind, e)
                self.command_over(cid, False, str(e)[:200])
        for mid, group in clips.items():
            self.start_clips(mid, group)
        if self.memory.get("post_jobs"):
            self.run_post_jobs()

    def command_over(self, cid, ok, note=None):
        with self.lock:
            self.cmd_status[cid] = {"status": "done" if ok else "failed", "note": note}
            self.memory["commands_done"][cid] = {"ok": ok, "note": note, "at": now_iso()}
            if len(self.memory["commands_done"]) > 300:
                for k in sorted(self.memory["commands_done"], key=lambda k: self.memory["commands_done"][k]["at"])[:100]:
                    del self.memory["commands_done"][k]
            self._save_memory()
        self.heartbeat(force=True)

    def run_command(self, kind, c):
        name = plain_name(c.get("file"))
        if kind == "post_all":
            return self.post_all(c)
        if kind == "share_all":
            return self.share_all(c)
        if kind == "set_style":
            style = "zoom" if c.get("style") == "zoom" else "classic"
            with self.lock:
                self.memory["styles"][name] = style
                self._save_memory()
            return f"{name}: {'Zoomed + tracking' if style == 'zoom' else 'Classic'}"
        if kind == "cancel_post":
            return self.cancel_post(str(c.get("target") or "all"))
        if kind == "mark_posted" and c.get("ns"):
            marked = []
            for n in c["ns"]:
                try:
                    movie, mode, part, f = self.find_part(dict(c, n=n))
                except (LookupError, FileNotFoundError):
                    continue
                status.set_post(movie["id"], mode, part["n"], "posted")
                marked.append(str(n))
            status.flush()
            return f"Marked as posted: {', '.join(marked) or 'none'}"
        if kind in ("post_part", "mark_posted", "share_part"):
            movie, mode, part, f = self.find_part(c)
            if kind == "mark_posted":
                status.set_post(movie["id"], mode, part["n"], "posted")
                status.flush()
                return "Marked as posted"
            if kind == "share_part":
                return "Ready to save on your phone: " + self.share_part(movie, mode, part, f)
            if not self.cfg.get("composio_api_key"):
                raise RuntimeError("TikTok isn't set up on the laptop yet (composio_api_key in config.json)")
            ok, error = self.send_part(movie, mode, part, manual=True)
            if not ok:
                raise RuntimeError(error or "TikTok didn't accept it")
            return "Sent to TikTok"
        if kind == "queue_order":
            order = [n for n in (plain_name(x) for x in c.get("order") or []) if n]
            with self.lock:
                self.memory["queue_order"] = order
                self._save_memory()
            return "New order saved"
        if not name:
            raise ValueError("no file name given")
        if kind in ("to_queue", "to_library"):
            src_dir = self.folder_for(c.get("from") or "library")
            dest_dir = self.watch if kind == "to_queue" else self.downloads
            if src_dir is None or not (src_dir / name).is_file():
                raise FileNotFoundError(f"{name} isn't in that folder any more")
            dest_dir.mkdir(parents=True, exist_ok=True)
            to = unique(dest_dir / name)
            shutil.move(str(src_dir / name), str(to))
            srt = matching_srt(src_dir / name) if (src_dir / name).parent.is_dir() else None
            if srt:
                shutil.move(str(srt), str(unique(dest_dir / srt.name)))
            if kind == "to_queue" and c.get("first"):
                with self.lock:
                    self.memory["queue_order"] = [to.name] + [n for n in self.memory["queue_order"] if n != to.name]
                    self._save_memory()
            return f"Moved to {'To edit' if kind == 'to_queue' else 'your Movies folder'}"
        if kind == "to_bin":
            src_dir = self.folder_for(c.get("from") or "done")
            if src_dir is None or not (src_dir / name).is_file():
                raise FileNotFoundError(f"{name} isn't there any more")
            if not send_to_trash(src_dir / name):
                raise OSError("Windows wouldn't put it in the Recycle Bin, so it was left where it is")
            return "Put in the Recycle Bin"
        raise ValueError(f"unknown command {kind!r}")

    def find_original(self, movie_id, movie_name=None):
        """The original file of a movie (for custom clips), wherever it is now."""
        o = self.memory["originals"].get(movie_id)
        if o and Path(o["path"]).is_file():
            return Path(o["path"]), (Path(o["srt"]) if o.get("srt") and Path(o["srt"]).is_file() else None)
        for folder in (self.output / "Done", self.watch, self.downloads, self.output / "Failed"):
            if not folder.is_dir():
                continue
            for p in folder.iterdir():
                if p.is_file() and p.suffix.lower() in VIDEO_EXT and status.movie_id(names.clean_name(p.name)) == movie_id:
                    return p, matching_srt(p)
        return None, None

    def start_clips(self, movie_id, cmds):
        """Sends the original once more, with the clips to make; the cutter makes only those."""
        movie = next((c.get("movie") for c in cmds if c.get("movie")), None)
        path, _ = self.find_original(movie_id, movie)
        ids = [str(c["id"]) for c in cmds]
        if not path:
            for cid in ids:
                self.command_over(cid, False, "The original movie isn't on the laptop any more (it may have been "
                                              "deleted). Put it back in the Done folder to make clips.")
            return
        if self.paused_reason():
            return
        clips = [{"id": str(c["id"]), "start": float(c.get("start") or 0), "end": float(c.get("end") or 0),
                  "title": str(c.get("title") or "")[:60]} for c in cmds]
        for cid in ids:
            self.cmd_status[cid] = {"status": "working", "note": "Sending the movie to GitHub"}
        self.heartbeat(force=True)
        try:
            self.send_movie(path, "clip", clips=clips, movie=movie or names.clean_name(path.name))
            for cid in ids:
                self.cmd_status[cid] = {"status": "working", "note": "Being cut on GitHub"}
            self.heartbeat(force=True)
        except Paused:
            for cid in ids:
                self.cmd_status.pop(cid, None)
        except Exception as e:
            log.error("Sending %s for clips failed, will try again: %s", path.name, e)
            for cid in ids:
                self.cmd_status.pop(cid, None)

    def clip_job_over(self, rid, ok=True, note=None):
        s = self.memory["sends"].get(rid) or {}
        for c in s.get("clips") or []:
            self.command_over(c["id"], ok, note or ("Clip made" if ok else "Clip failed"))
        with self.lock:
            self.memory["sends"].pop(rid, None)
            self._save_memory()

    def working_clip_ids(self):
        return {str(c["id"]) for s in self.memory["sends"].values() if s.get("clips") for c in s["clips"]}

    # ---- keeping its own code up to date
    def self_update(self, force=False):
        """Fetches newer versions of the helper's own files from GitHub, checks they're valid Python, swaps
        them in (old ones saved in laptop/.backup) and restarts. Setting: auto_update_laptop."""
        if not force and not self.remote_settings.get("auto_update_laptop", True):
            return False
        root = HERE.parent
        try:                                    # one quick call: has anything changed on GitHub at all?
            head = (self.gh.call("GET", "/commits/main") or {}).get("sha")
        except Exception:
            head = None
        missing = any(not (root / rel).exists() for rel in CODE_FILES)   # a new file added in an update
        if head and head == self.memory.get("code_sha") and not force and not missing:
            return False
        changed = []
        for rel in CODE_FILES:
            text, sha = self.gh.read_file(rel)
            if text is None:
                continue
            local = root / rel
            data = text.encode("utf-8")
            old = local.read_bytes() if local.exists() else b""
            if git_blob_sha(old) == sha or old.replace(b"\r\n", b"\n") == data:
                continue
            compile(text, rel, "exec")                      # never install something broken
            changed.append((local, data, old))
        if head:
            with self.lock:
                self.memory["code_sha"] = head
                self._save_memory()
        if not changed:
            return False
        backup = HERE / ".backup"
        backup.mkdir(exist_ok=True)
        for local, data, old in changed:
            if old:
                (backup / local.name).write_bytes(old)
            tmp = local.with_suffix(local.suffix + ".new")
            tmp.write_bytes(data)
            os.replace(tmp, local)
        log.info("Updated %s; restarting", ", ".join(l.name for l, _, _ in changed))
        self.notify("🔄 Laptop helper updated", "Restarting with the newest version.", event=False, tags=["arrows_counterclockwise"])
        return True

    def repair_old_parts(self):
        """Once: fixes parts made before the chapter fix (phones showed '1 hr' and stopped after the real part)."""
        if not self.ready_dir.is_dir():
            return
        try:
            import mp4fix  # noqa: F401
        except ImportError:
            return                                   # not downloaded yet: next update brings it
        fixed = sum(1 for f in self.ready_dir.rglob("*.mp4") if repair_part(f))
        with self.lock:
            self.memory["mp4fix_done"] = True
            # copies already put on GitHub for the phone were made from the broken files: send fresh ones
            self.memory["shared"] = []
            self._save_memory()
        if fixed:
            log.info("Repaired %d parts (removed the movie-length chapter track)", fixed)
            self.notify("🔧 Parts repaired", f"{fixed} parts had a wrong length (showed about an hour and stopped "
                        "early on the phone). Fixed on the laptop.", event=False, tags=["wrench"])

    def cleanup_old_parts(self):
        """Setting "cleanup_parts_after_days": deletes parts older than that from Ready to post."""
        days = float(self.remote_settings.get("cleanup_parts_after_days") or 0)
        if days <= 0 or not self.ready_dir.is_dir():
            return
        limit = time.time() - days * 86400
        for f in self.ready_dir.rglob("*"):
            if f.is_file() and f.stat().st_mtime < limit:
                f.unlink()
        for d in sorted((d for d in self.ready_dir.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
            if not any(d.iterdir()):
                d.rmdir()

    # ---- 4. post to TikTok
    def refresh_settings(self, force=False):
        if not force and time.time() - self.last["settings"] < 60:
            return
        try:
            text, _ = self.gh.read_file("config/settings.json")
            was_paused = bool(self.remote_settings.get("paused"))
            self.remote_settings = settings.load(text=text or "{}", env={})
            self.last["settings"] = time.time()
            if was_paused and not self.remote_settings.get("paused"):
                log.info("Pause switched off: carrying on")
                self.start_cutter()             # a movie paused half-way carries on straight away
        except Exception as e:
            log.warning("Couldn't read settings from GitHub: %s", e)

    def post_if_due(self, force=False, dry_run=None):
        dry_run = self.cfg["posting_dry_run"] if dry_run is None else dry_run
        self.refresh_settings(force=True)
        cfg = self.remote_settings["posting"]
        tz = status.safe_zone(cfg.get("timezone") or "Pacific/Auckland")
        self.state_ready()
        status.refresh()
        state = status.load_state() or {}
        received = set(self.memory["received"])

        def available(p):
            if (self.output / p["drive_path"]).exists():
                return True
            return False if p["drive_path"] in received else None

        now = datetime.now(tz)
        if str(cfg.get("when") or "") == "as_ready" and not force and self.posted_recently():
            return None                                   # one part every 5 minutes at most
        pick, reason = posting.decide(state, cfg, now, force, available)
        if not dry_run:
            count = posting.posted_today(state, now)
            status.set_posting(cfg.get("enabled"), posting.next_at(now, cfg, count), count)
        if not pick:
            log.info("Posting: %s", reason)
            return None
        movie, mode, part = pick
        label = LABELS.get(mode, mode)
        log.info("Posting %s [%s] Part %s%s", movie["name"], label, part["n"], " - dry run, nothing posted" if dry_run else "")
        if dry_run:
            return pick
        self.send_part(movie, mode, part, cfg)
        state = status.load_state() or {}
        count = posting.posted_today(state, datetime.now(tz))
        status.set_posting(cfg.get("enabled"), posting.next_at(datetime.now(tz), cfg, count), count)
        status.flush()
        return pick

    def posted_recently(self, minutes=5):
        """'As soon as ready' sends at most one part every 5 minutes, so TikTok isn't flooded."""
        return time.time() - self.memory.get("last_post", 0) < minutes * 60

    def send_part(self, movie, mode, part, cfg=None, manual=False):
        """Sends one part to TikTok (drafts by default, or straight onto the profile with method=direct).
        Records the result in state.json and tells the phone. Returns (ok, error)."""
        cfg = cfg or self.remote_settings["posting"]
        self.state_ready()
        privacy = posting.PRIVACY.get(str(cfg.get("privacy", "self_only")).lower(), "SELF_ONLY")
        caption = posting.build_caption(part.get("caption"), cfg.get("hashtags"))
        label = LABELS.get(mode, mode)
        status.set_post(movie["id"], mode, part["n"], "scheduled")
        creds = {"api_key": self.cfg["composio_api_key"], "user_id": self.cfg["composio_user_id"],
                 "connected_account_id": self.cfg["composio_connected_account_id"],
                 "version": self.cfg["composio_tiktok_version"], "username": self.cfg["tiktok_username"]}
        direct = str(cfg.get("method") or "draft").lower() == "direct"
        try:
            ok, ref, error = posting.post_video(self.output / part["drive_path"], caption, privacy, creds,
                                                publish=direct)
        except Exception as e:
            ok, ref, error = False, None, str(e)
        url = ref if ref and str(ref).startswith("http") else None
        status.set_post(movie["id"], mode, part["n"], ("posted" if direct else "inbox") if ok else "failed", url=url,
                        error=None if ok else error)
        with self.lock:
            self.memory["last_post"] = time.time()
            self._save_memory()
        what = "Clip" if mode == "clips" else "Highlight" if mode == "highlights" else "Part"
        title = f"{movie['name']}: {what} {part['n']}"
        if ok and not direct:
            self.notify(f"📲 In your TikTok drafts: {title}",
                        f"[{label}] Open TikTok, tap the notification, paste the caption and post.\n\n📝 Caption:\n{caption}",
                        kind="posted", tags=["iphone"])
        elif ok:
            where = "privately (only you can see it)" if privacy == "SELF_ONLY" else "publicly"
            self.notify(f"📤 Posted to TikTok: {title}",
                        f"[{label}] Posted {where}.{(' ' + url) if url else ''}\n\n📝 Caption:\n{caption}",
                        kind="posted", tags=["outbox_tray"])
        else:
            again = "" if manual else (f"\nIt will be tried again at the next posting time "
                                       f"(up to {posting.MAX_ATTEMPTS} tries).")
            self.notify(f"⚠️ TikTok post failed: {title}", f"[{label}] {error}{again}",
                        kind="post_failed", tags=["warning"])
        status.flush()
        return ok, error

    def post_all(self, c):
        """'Send all parts to TikTok': queues every part of the movie (one mode) that hasn't been sent, in
        order. run_post_jobs() then sends them one by one until all of them are in TikTok."""
        if not self.cfg.get("composio_api_key"):
            raise RuntimeError("TikTok isn't set up on the laptop yet (composio_api_key in config.json)")
        self.state_ready()
        status.refresh()
        state = status.load_state() or {}
        movie = next((m for m in state.get("movies", []) if m.get("id") == c.get("movie_id")), None)
        if not movie:
            raise LookupError("That movie isn't in the list any more")
        mode = str(c.get("mode") or "")
        parts = sorted((movie.get("modes", {}).get(mode) or {}).get("parts", []), key=lambda p: p.get("n", 0))
        todo = [p["n"] for p in parts if (p.get("post") or {}).get("status") not in ("posted", "inbox")]
        if not todo:
            return "Every part was already sent"
        cid = str(c["id"])
        with self.lock:
            self.memory.setdefault("post_jobs", {})[cid] = {"movie_id": movie["id"], "mode": mode, "left": todo,
                                                            "total": len(todo), "next_try": 0, "fails": 0}
            self._save_memory()
        self.cmd_status[cid] = {"status": "working", "note": f"Sending {len(todo)} parts to TikTok, in order…"}
        self.run_post_jobs()
        return None                                    # finished later by run_post_jobs

    POST_RETRY_MINUTES = 10

    def cancel_post(self, target):
        """Stops a 'send all' (or every one, target "all"). Parts already in TikTok stay there."""
        jobs = self.memory.get("post_jobs") or {}
        hit = [cid for cid in jobs if target in (cid, "all")]
        for cid in hit:
            job = jobs.pop(cid)
            self.command_over(cid, False, f"Cancelled. {job['total'] - len(job['left'])} of {job['total']} "
                                          f"were sent before that")
        with self.lock:
            self._save_memory()
        return "Stopped sending to TikTok" if hit else "Nothing was being sent"

    def cancel_requested(self, cid):
        """A Cancel tapped in the app while parts are going out (checked before each part)."""
        try:
            text, _ = self.gh.read_file("config/commands.json")
            cmds = (json.loads(text) if text else {}).get("commands") or []
        except Exception:
            return False
        done = self.memory["commands_done"]
        return any(c.get("type") == "cancel_post" and str(c.get("target") or "all") in (cid, "all")
                   and str(c.get("id")) not in done for c in cmds)

    def run_post_jobs(self):
        """Sends queued 'send all' parts strictly in order. When TikTok won't take more drafts for now, it
        waits and tries the same part again every 10 minutes (room frees up as drafts get posted), so in the
        end every part is in TikTok. Parts cut after the tap go too. Survives restarts (agent_memory.json)."""
        jobs = self.memory.get("post_jobs") or {}
        for cid, job in list(jobs.items()):
            for _round in range(50):
                if not self.send_job_parts(cid, job):          # waiting (TikTok, pause) or gave up
                    break
                more, cutting = self.unsent_parts(job)
                if more:
                    with self.lock:
                        job["left"] += more
                        job["total"] += len(more)
                        self._save_memory()
                    continue                                  # send the new ones straight away
                if cutting:
                    with self.lock:
                        job["next_try"] = time.time() + 120
                        self._save_memory()
                    self.cmd_status[cid] = {"status": "working", "note": f"Sent {job['total']} so far. Waiting "
                                            "for the next part to be cut, then it goes too"}
                    self.heartbeat(force=True)
                    break
                with self.lock:
                    jobs.pop(cid, None)
                    self._save_memory()
                self.command_over(cid, True, f"All {job['total']} parts are in TikTok, in order")
                break

    def send_job_parts(self, cid, job):
        """Sends the job's parts in order. True when all of them went; False when it has to wait or gave up."""
        jobs = self.memory["post_jobs"]
        while job["left"]:
            if self.paused_reason() or time.time() < job.get("next_try", 0):
                return False
            if self.cancel_requested(cid):
                self.cancel_post(cid)
                return False
            done = job["total"] - len(job["left"])
            n = job["left"][0]
            self.cmd_status[cid] = {"status": "working", "note": f"Sending part {n} ({done + 1} of {job['total']})…"}
            self.heartbeat(force=True)
            try:
                movie, mode, part, f = self.find_part({"movie_id": job["movie_id"], "mode": job["mode"], "n": n})
                if (part.get("post") or {}).get("status") in ("posted", "inbox"):
                    ok, error = True, None                     # sent another way meanwhile
                else:
                    ok, error = self.send_part(movie, mode, part, manual=True)
            except FileNotFoundError:
                ok, error = False, "not on the laptop yet"
            except LookupError as e:
                ok, error = False, str(e)
            with self.lock:
                if ok:
                    job["last_n"] = job["left"].pop(0)
                    job["fails"] = 0
                    job["next_try"] = 0
                elif re.search(r"pending|spam_risk|too_many|rate.?limit|limit|not on the laptop", str(error), re.I):
                    job["next_try"] = time.time() + self.POST_RETRY_MINUTES * 60
                    why = ("the part isn't on the laptop yet" if "laptop" in str(error) else
                           "TikTok is holding the rest until you post some drafts")
                    self.cmd_status[cid] = {"status": "working", "note": f"Sent {done} of {job['total']}. "
                                            f"Waiting: {why}. Trying part {n} again in {self.POST_RETRY_MINUTES} min"}
                else:
                    job["fails"] += 1
                    job["next_try"] = time.time() + self.POST_RETRY_MINUTES * 60
                    if job["fails"] >= 3:
                        jobs.pop(cid, None)
                        self._save_memory()
                        self.command_over(cid, False, f"Sent {done} of {job['total']}, then part {n} kept "
                                                      f"failing: {error}")
                        return False
                    self.cmd_status[cid] = {"status": "working", "note": f"Sent {done} of {job['total']}. Part "
                                            f"{n} failed ({error}); trying again in {self.POST_RETRY_MINUTES} min"}
                self._save_memory()
            self.heartbeat(force=True)
            if not ok:
                return False
            if job["left"]:
                time.sleep(15)                                 # TikTok allows a few uploads a minute
        return True

    def unsent_parts(self, job):
        """New parts of a 'send all' movie that came out after the tap (it may still be cutting)."""
        if time.time() < job.get("next_try", 0):
            return [], True
        try:
            status.refresh()
            state = status.load_state() or {}
        except Exception:
            return [], True
        movie = next((m for m in state.get("movies", []) if m.get("id") == job["movie_id"]), None)
        if not movie:
            return [], False
        last = job.get("last_n", 0)
        parts = sorted((movie.get("modes", {}).get(job["mode"]) or {}).get("parts", []), key=lambda p: p.get("n", 0))
        more = [p["n"] for p in parts if p.get("n", 0) > last
                and (p.get("post") or {}).get("status") not in ("posted", "inbox")]
        return more, movie.get("status") not in ("done", "failed")

    def find_part(self, c):
        """(movie, mode, part) from state.json for a command's movie_id/mode/n, checking the file is here."""
        self.state_ready()
        status.refresh()
        state = status.load_state() or {}
        movie = next((m for m in state.get("movies", []) if m.get("id") == c.get("movie_id")), None)
        if not movie:
            raise LookupError("That movie isn't in the list any more")
        mode = str(c.get("mode") or "")
        part = next((p for p in (movie.get("modes", {}).get(mode) or {}).get("parts", [])
                     if str(p.get("n")) == str(c.get("n"))), None)
        if not part:
            raise LookupError("That part isn't in the list any more")
        f = (self.output / part["drive_path"]).resolve()
        if self.output.resolve() not in f.parents:
            raise ValueError("bad file path")
        if not f.is_file():
            raise FileNotFoundError("The part isn't on the laptop (not downloaded yet, or it was cleaned up)")
        return movie, mode, part, f

    def share_all(self, c):
        """'Save all to phone': puts the listed parts (the ones not already on GitHub) up for the phone, in
        order, one at a time, so the phone can download each as soon as it's there."""
        ns = [int(n) for n in (c.get("ns") or [])]
        sent, missing = 0, []
        for i, n in enumerate(ns, 1):
            if self.paused_reason():
                raise RuntimeError(f"Paused after {sent} of {len(ns)}")
            self.cmd_status[str(c["id"])] = {"status": "working", "note": f"Sending part {n} ({i} of {len(ns)})…"}
            self.heartbeat(force=True)
            try:
                movie, mode, part, f = self.find_part(dict(c, n=n))
            except (LookupError, FileNotFoundError):
                missing.append(n)
                continue
            path = f"share/{movie['id']}/{mode}-{n:03}.bin"
            if path not in (self.memory.get("shared") or []):
                self.share_part(movie, mode, part, f)
            sent += 1
            self.heartbeat(force=True)
        return f"{sent} parts ready to save" + (f" (not on the laptop: {', '.join(map(str, missing))})" if missing else "")

    def share_part(self, movie, mode, part, f):
        """Puts a full-quality copy of one part (encrypted with the ntfy topic, like the previews) on the
        'previews' branch so the phone can save it. Keeps only the newest SHARE_KEEP shared parts."""
        if not self.cfg.get("ntfy_topic"):
            raise RuntimeError("ntfy_topic isn't set in config.json (it's the lock for files sent to the phone)")
        data = f.read_bytes()
        if len(data) > 400_000_000:
            raise ValueError(f"The part is {round(len(data) / 1e6)} MB; the phone can get files up to 400 MB")
        prev = ensure_previews()
        path = f"share/{movie['id']}/{mode}-{int(part['n']):03}.bin"
        blobs = prev.blobs_for(self.gh, path, prev.encrypt(data, self.cfg["ntfy_topic"]))   # pieces if big
        with self.lock:
            shared = [x for x in self.memory.setdefault("shared", []) if x != path] + [path]
            self.memory["shared"] = shared[-SHARE_KEEP:]
            self._save_memory()
        for attempt in range(3):           # the cutter may be rewriting this branch at the same moment
            files = self.gh.branch_files(prev.BRANCH)
            files = {p: s for p, s in files.items()
                     if not p.startswith("share/") or re.sub(r"\.bin\.\d+$", ".bin", p) in self.memory["shared"]}
            files = prev.without(files, path)
            files.update(blobs)
            try:
                self.gh.replace_branch(prev.BRANCH, files, f"share {path}")
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(3)
        return path

    # ---- 5. laptop.json
    def snapshot(self):
        with self.lock:
            wl = []
            for line in self.memory["order"]:
                e = dict(self.memory["wishlist"].get(line) or {"title": line, "status": "searching",
                                                                "source_url": None, "note": None})
                e.pop("checked", None)
                e.pop("paused", None)
                if e.get("percent") is None:
                    e.pop("percent", None)
                wl.append(e)
            transfers = list(self.transfers.values())
            send = next((t for t in transfers if t["direction"] == "send"), None)
            return {"updated": now_iso(), "online": self.online, "host": socket.gethostname(),
                    "watch_folder": str(self.watch).replace("\\", "/"),
                    "ready_folder": str(self.ready_dir).replace("\\", "/"),
                    "uploading": {"file": send["file"], "percent": send["percent"]} if send else None,
                    "cutting": {"file": Path(self.cutting).name} if self.cutting else None,
                    "transfers": transfers,
                    "uploaded": self.memory["uploaded"][:20], "wishlist": wl,
                    "paused": self.paused_reason(), "battery": self.battery,
                    "queue": self.queue_files(), "library": self.folder_files(self.downloads),
                    "library_folder": str(self.downloads).replace("\\", "/"),
                    "bin": ([dict(f, folder="done") for f in self.folder_files(self.output / "Done")]
                            + [dict(f, folder="failed") for f in self.folder_files(self.output / "Failed")]),
                    "commands": {**{cid: {"status": "working", "note": "Being cut on GitHub"}
                                      for cid in self.working_clip_ids()},
                                     **{cid: {"status": "working", "note": f"Sending to TikTok: {j['total'] - len(j['left'])} "
                                                                           f"of {j['total']} done"}
                                        for cid, j in (self.memory.get("post_jobs") or {}).items()},
                                     **self.cmd_status}, "commands_done": sorted(self.memory["commands_done"])[-150:],
                    "originals": sorted(self.memory["originals"]), "shared": list(self.memory.get("shared", [])),
                    "version": 2}

    def folder_files(self, folder, limit=150):
        """Movie files (top level only) in a folder, newest first."""
        if not folder.is_dir():
            return []
        out = [file_info(p) for p in folder.iterdir()
               if p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith((".", "~"))]
        return sorted([f for f in out if f], key=lambda f: f["modified"], reverse=True)[:limit]

    def style_for(self, name):
        """Cutting style picked in the app for this movie, else the default from Settings."""
        return (self.memory.get("styles") or {}).get(name) or \
            ("zoom" if (self.remote_settings or {}).get("cut_style") == "zoom" else "classic")

    def queue_files(self):
        """Movies in To edit that haven't been sent yet, in the order they'll go."""
        busy = self.waiting_paths()
        sending = {t["file"] for t in self.transfers.values() if t["direction"] == "send"}
        files = [p for p in self.watcher.candidates() if str(p) not in busy]
        out = []
        for p in self.queue_sorted(files):
            info = file_info(p)
            if info:
                info["sending"] = p.name in sending
                info["style"] = self.style_for(p.name)
                out.append(info)
        return out

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
                self.gh.save_file("laptop.json", lambda _old: json.dumps(snap, indent=1, ensure_ascii=False) + "\n",
                                  "status", "laptop heartbeat")
                self.last_push, self.last_sent, self.progress_changed = time.time(), body, False
            except Exception as e:
                log.warning("Couldn't update the app (laptop.json): %s", e)
                self.last_push = time.time() - float(self.cfg["heartbeat_minutes"]) * 60 + 60   # retry in 1 min

    # ---- the loop
    def tick(self, now=None):
        """One round: send ready files, fetch finished parts, post if due, tidy up."""
        now = time.time() if now is None else now
        self.refresh_settings()
        if now - self.last["commands"] >= 30:
            self.last["commands"] = now
            try:
                self.check_commands()
            except Exception as e:
                log.error("Checking the app's commands failed: %s", e)
        if self.paused_reason():
            self.heartbeat()
            return
        self.check_folder()
        if now - self.last["receive"] >= float(self.cfg["receive_seconds"]):
            self.last["receive"] = now
            try:
                self.receive_parts()
            except Exception as e:
                log.error("Checking for finished parts failed: %s", e)
        as_ready = str((self.remote_settings.get("posting") or {}).get("when") or "") == "as_ready"
        every = 1 if as_ready else float(self.cfg["posting_check_minutes"])   # as soon as ready: check each minute
        if now - self.last["posting"] >= every * 60:
            self.last["posting"] = now
            try:
                self.post_if_due()
            except Exception as e:
                log.error("Posting check failed: %s", e)
        if not self.memory.get("mp4fix_done") and not self.transfers:
            self.repair_old_parts()
        if now - self.last["cleanup"] >= 3600:
            self.last["cleanup"] = now
            try:
                self.cleanup_old_parts()
            except Exception as e:
                log.error("Cleaning up old parts failed: %s", e)
        if now - self.last["update"] >= 300 and not self.transfers:
            self.last["update"] = now
            try:
                if self.self_update():
                    raise Restart()
            except Restart:
                raise
            except Exception as e:
                log.warning("Couldn't check for a newer helper: %s", e)
        self.heartbeat()

    def stop(self):
        self.online = False
        self.transfers.clear()
        self.heartbeat(force=True)
        status.flush()


# ---------------------------------------------------------------- config + first-time setup
def read_config_json(path):
    """Reads config.json. Windows paths typed with backslashes (C:\\Users\\...) break JSON, so in the
    folder lines they're turned into / and the file is saved again. Other mistakes get a plain message."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    folder_line = re.compile(r'("(?:watch|output|download)_folder"\s*:\s*")([^"\n]*)(")')
    fixed = folder_line.sub(lambda m: m.group(1) + re.sub(r"\\+", "/", m.group(2)) + m.group(3), text)
    try:
        raw = json.loads(fixed)
    except json.JSONDecodeError as e:
        sys.exit(f"config.json has a typing mistake on line {e.lineno} (column {e.colno}): {e.msg}. "
                 f"Open it in Notepad, fix that line (check the quotes and commas), save, and try again.")
    if fixed != text:
        path.write_text(fixed, encoding="utf-8")
        print("Fixed config.json: folder paths now use / instead of \\.")
    return raw


def load_config(path):
    path = Path(path)
    if not path.exists():
        sys.exit(f"No settings file at {path}. Copy config.example.json to config.json and fill it in.")
    cfg = read_config_json(path)
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def ensure_secrets(path):
    """Makes an ntfy name the first time (if you didn't give one) and saves it in config.json."""
    path = Path(path)
    raw = read_config_json(path)
    changed = False
    if PLACEHOLDER.search(str(raw.get("ntfy_topic") or "")):
        raw["ntfy_topic"] = "movies-" + secrets.token_hex(6)
        changed = True
    if changed:
        path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def setup(path):
    """`--setup`: checks everything and writes github-secrets.txt with exactly what to paste into GitHub."""
    cfg = ensure_secrets(path)
    lines = [f"OK  Python {sys.version.split()[0]} (nothing else to install)"]
    cfg = dict(DEFAULTS, **cfg)
    gh = ghapi.GitHub(f"{cfg['github_owner']}/{cfg['github_repo']}", cfg["github_token"], cfg["github_api_url"])
    try:
        info = gh.call("GET", "")
        perms = info.get("permissions") or {}
        lines.append("OK  GitHub token works" + ("" if perms.get("push", True) else
                                                 "  (!! but it can't write: give it Contents: Read and write)"))
        gh.run_active(WORKFLOW)
        lines.append("OK  token can see GitHub Actions")
    except Exception as e:
        lines.append(f"!!  GitHub check failed ({e}). Check github_owner, github_repo and github_token; the token "
                     f"needs Contents: Read and write + Actions: Read and write on {cfg['github_repo']}.")
    text = "\n".join(lines) + f"""

NEXT STEPS
1. Add this 1 secret on GitHub (only once), so the cutter can send phone notifications:
     github.com/{cfg['github_owner']}/{cfg['github_repo']} > Settings > Secrets and variables > Actions
     > New repository secret
       Name:   NTFY_TOPIC
       Secret: {cfg['ntfy_topic']}
2. In the ntfy app on your iPhone, subscribe to:  {cfg['ntfy_topic']}
3. Drop a movie into:  {cfg['watch_folder']}
   Finished parts arrive in:  {Path(cfg['output_folder']) / 'Ready to post'}
"""
    out = Path(path).parent / "github-secrets.txt"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"(Also saved in {out})")
    return text


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


_LOCK = None


def single_instance(wait=40):
    """Only one helper may run at a time (two would send the same movie twice). Waits a little in case
    the old one is just restarting."""
    global _LOCK
    for _ in range(int(wait)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 47231))
            sock.listen(1)
            _LOCK = sock
            return True
        except OSError:
            sock.close()
            time.sleep(1)
    return False


def restart():
    """Starts the helper again (new code). Mac: launchd starts it by itself when this one ends."""
    if _LOCK:
        _LOCK.close()
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([sys.executable, str(HERE / "agent.py")] + sys.argv[1:], cwd=str(HERE),
                         creationflags=flags, close_fds=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Movie Splitter laptop helper")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--setup", action="store_true", help="first-time setup: check GitHub, show the secret to add")
    ap.add_argument("--once", action="store_true", help="one round of everything, then stop")
    ap.add_argument("--post-now", action="store_true", help="post the next part now (ignores the schedule)")
    ap.add_argument("--dry-run", action="store_true", help="with --post-now: only show what would be posted")
    ap.add_argument("--test-post", metavar="VIDEO", help="post this one video to TikTok privately (checks the Composio setup)")
    args = ap.parse_args(argv)
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f"No settings file at {cfg_path}. Copy config.example.json to config.json and fill it in.")
    if args.setup:
        setup(cfg_path)
        return 0
    if args.test_post:
        cfg = dict(DEFAULTS, **load_config(cfg_path))
        creds = {"api_key": cfg["composio_api_key"], "user_id": cfg["composio_user_id"],
                 "connected_account_id": cfg["composio_connected_account_id"],
                 "version": cfg["composio_tiktok_version"], "username": cfg["tiktok_username"]}
        print(f"Sending {args.test_post} to your TikTok drafts. This can take a minute...")
        ok, ref, error = posting.post_video(Path(args.test_post), "Test post from Movie Splitter 🎬 #test",
                                           "SELF_ONLY", creds, publish=False)
        print("Worked! It's in your TikTok drafts/inbox: open TikTok to see it." if ok else f"Didn't work: {error}")
        return 0 if ok else 1
    setup_logging(cfg_path.parent)
    agent = Agent(ensure_secrets(cfg_path), cfg_path.parent)
    for folder in (agent.watch, agent.ready_dir):
        folder.mkdir(parents=True, exist_ok=True)
    log.info("Started. Watching %s, parts go to %s", agent.watch, agent.ready_dir)

    def stop_signal(*_):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, stop_signal)   # Mac: launchd stopping the helper
    except (ValueError, AttributeError):
        pass
    try:
        agent.refresh_settings(force=True)
        agent.heartbeat(force=True)
        if args.post_now:
            agent.post_if_due(force=True, dry_run=args.dry_run or None)
            return 0
        if args.once:
            agent.check_wishlist()
            agent.watcher.ready()
            if agent.watcher.seen:                   # look again once they've had time to settle
                time.sleep(float(agent.cfg["stable_seconds"]) + 0.5)
            agent.tick(now=time.time() + 10 ** 6)    # (receive, post and tidy up straight away)
            agent.stop()
            return 0

        if not single_instance():
            log.info("Another copy of the helper is already running; this one stops.")
            return 0

        def every(minutes, fn):
            while True:
                time.sleep(float(minutes) * 60)
                try:
                    fn()
                except Exception as e:
                    log.error("%s: %s", getattr(fn, "__name__", "task"), e)
        threading.Thread(target=every, args=(agent.cfg["wishlist_minutes"], agent.check_wishlist),
                         daemon=True).start()
        threading.Thread(target=every, args=(agent.cfg["heartbeat_minutes"], lambda: agent.heartbeat(force=True)),
                         daemon=True).start()
        threading.Thread(target=every, args=(1, lambda: agent.refresh_settings(force=True)), daemon=True).start()
        threading.Thread(target=every, args=(0.5, agent.check_commands), daemon=True).start()   # app taps, every 30 s
        threading.Thread(target=every, args=(1, agent.build_edits), daemon=True).start()   # FilmCraft edits
        threading.Thread(target=agent.check_wishlist, daemon=True).start()   # first look straight away
        while True:
            try:
                agent.tick()
            except Restart:
                agent.heartbeat(force=True)
                status.flush()
                return restart()
            except Exception as e:
                log.error("Something went wrong (trying again shortly): %s", e)
            time.sleep(float(agent.cfg["poll_seconds"]))
    except KeyboardInterrupt:
        log.info("Stopping.")
        agent.stop()
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        with open(HERE / "agent.log", "a", encoding="utf-8") as _f:
            _f.write(f"{datetime.now()} ERROR The helper stopped:\n{traceback.format_exc()}\n")
        raise
