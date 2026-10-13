"""
Live status for the iPhone app: keeps state.json (see plan/CONTRACT.md section 3) up to date.

On GitHub Actions it's saved to the "status" branch of this repo (made the first time if it
doesn't exist yet), using the run's own GITHUB_TOKEN. The laptop helper saves its parts of it
(TikTok post results) through GitHub's contents API (use_github). Anywhere else it's just
written to a local folder (STATUS_DIR, default "status-local") so it can be tested.
STATUS=off turns it off.

Nothing in here may ever stop a movie run: every public function catches its own errors.
Saves to GitHub are bundled (at most about once every 20 seconds), except finish/fail/post,
which are saved straight away.
"""
import atexit
import copy
import functools
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
STATE_FILE = "state.json"
LAPTOP_FILE = "laptop.json"
BRANCH = "status"
PUSH_EVERY = float(os.environ.get("STATUS_PUSH_EVERY", "20"))
MAX_EVENTS = 200
MAX_MOVIES = 50
STAGES = {"idle", "downloading", "analysing", "subtitles", "planning", "rendering", "uploading",
          "posting", "done", "failed"}
EVENT_TYPES = {"new_file", "started", "planned", "part_ready", "finished", "failed", "posted",
               "post_failed", "paused", "info"}
LABELS = {"highlights": "Highlights", "stretch": "Best stretch", "all": "Whole movie"}
KEEP = object()   # "leave this as it is"


def safe_zone(name):
    """ZoneInfo(name); Windows without the 'tzdata' package has no time-zone list, so fall back to
    the computer's own time zone instead of crashing."""
    for n in (name, "Pacific/Auckland"):
        try:
            return ZoneInfo(n)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo


def _timezone():
    try:
        import settings
        return safe_zone(settings.load()["posting"]["timezone"])
    except Exception:
        return safe_zone("Pacific/Auckland")


TZ = _timezone()


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def movie_id(name):
    """'The Man from U.N.C.L.E. (2015)' -> 'the-man-from-uncle-2015'"""
    s = re.sub(r"['.’]", "", name.lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "movie"


def empty_state():
    return {
        "version": 1,
        "updated": now_iso(),
        "run": {"active": False, "run_id": None, "started": None, "movie": None, "mode": None,
                "stage": "idle", "part": None, "parts_total": None},
        "queue": [],
        "movies": [],
        "posting": {"enabled": False, "next_at": None, "posted_today": 0},
        "events": [],
    }


def safe(fn):
    """Status problems are printed, never raised."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            print(f"(status not updated: {fn.__name__}: {e})", flush=True)
    return wrapper


# ---------------------------------------------------------------- storage
class Store:
    """
    Where state.json lives. mode:
      "git"   GitHub Actions: a clone of the status branch, saved with git push
      "api"   the laptop (or STATUS_BACKEND=api): GitHub's contents API with GITHUB_TOKEN
      "local" a plain folder (STATUS_DIR), for testing
      "off"   nothing (STATUS=off)
    In "git" and "api" mode, if someone else saved first, their file is read and our own
    changes since the last save are redone on top of it.
    """

    def __init__(self, mode=None, repo=None, token=None, api_url=None):
        env = os.environ
        if env.get("STATUS", "on").strip().lower() in ("off", "no", "0", "false"):
            mode = "off"
        repo, token = repo or env.get("GITHUB_REPOSITORY"), token or env.get("GITHUB_TOKEN")
        if not mode:
            mode = env.get("STATUS_BACKEND") or ("git" if env.get("GITHUB_ACTIONS") == "true" and repo and token
                                                 else "local")
        self.mode = mode
        self.off, self.git, self.remote = mode == "off", mode == "git", mode in ("git", "api")
        default_dir = (Path(env.get("RUNNER_TEMP", "/tmp")) / "status-branch" if self.git
                       else HERE / "status-local")
        self.dir = Path(env.get("STATUS_DIR") or default_dir)
        self.lock = threading.RLock()
        self.pending = []          # changes not yet on GitHub (replayed if someone else saved first)
        self.last_push = 0.0
        self.timer = None
        self.sha = None
        self.state = empty_state()
        if mode == "api":
            import ghapi
            self.gh = ghapi.GitHub(repo, token, api_url or env.get("GITHUB_API_URL") or ghapi.API)
            self.refresh()
            return
        if self.off:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.git:
            self._git_setup()
        self.state = self._read_file() or empty_state()

    # ---- files
    def _read_file(self, name=STATE_FILE):
        if self.mode == "api":
            text, _ = self.gh.read_file(name, BRANCH)
            try:
                data = json.loads(text) if text else None
            except ValueError:
                data = None
            return data if isinstance(data, dict) else None
        p = self.dir / name
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _text(self):
        return json.dumps(self.state, ensure_ascii=False, indent=1)

    def _write_file(self):
        tmp = self.dir / (STATE_FILE + ".tmp")
        tmp.write_text(self._text(), encoding="utf-8")
        os.replace(tmp, self.dir / STATE_FILE)

    def refresh(self):
        """api mode: reads the newest state.json from GitHub (keeping our unsaved changes)."""
        if self.mode != "api":
            return
        with self.lock:
            text, self.sha = self.gh.read_file(STATE_FILE, BRANCH)
            try:
                self.state = json.loads(text) if text else empty_state()
            except ValueError:
                self.state = empty_state()
            for change in self.pending:
                change(self.state)

    # ---- git (GitHub Actions only)
    def _run_git(self, *args):
        return subprocess.run(["git", "-C", str(self.dir), *args], capture_output=True, text=True, timeout=120)

    def _git_setup(self):
        url = os.environ.get("STATUS_REMOTE_URL") or (       # (STATUS_REMOTE_URL: for tests only)
            f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{os.environ['GITHUB_REPOSITORY']}.git")
        if not (self.dir / ".git").exists():
            self._run_git("init", "-q")
            self._run_git("remote", "add", "origin", url)
        self._run_git("config", "user.name", "movie-splitter-bot")
        self._run_git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        if self._run_git("fetch", "-q", "--depth", "1", "origin", BRANCH).returncode == 0:
            self._run_git("checkout", "-q", "-B", BRANCH, "FETCH_HEAD")
            self._run_git("reset", "-q", "--hard", "FETCH_HEAD")
        else:
            print("(no status branch yet: making one)", flush=True)
            self._run_git("checkout", "-q", "--orphan", BRANCH)

    def _push(self, message):
        if self.mode == "api":
            return self._push_api(message)
        for attempt in range(5):
            self._write_file()
            self._run_git("add", STATE_FILE)
            if self._run_git("diff", "--cached", "--quiet").returncode != 0:
                self._run_git("commit", "-q", "-m", message)
            res = self._run_git("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
            if res.returncode == 0:
                self.pending.clear()
                return True
            # Someone else (the laptop, or the other workflow) saved first: take theirs,
            # then redo our own changes on top of it.
            if self._run_git("fetch", "-q", "--depth", "1", "origin", BRANCH).returncode == 0:
                self._run_git("reset", "-q", "--hard", "FETCH_HEAD")
                self.state = self._read_file() or empty_state()
                for change in self.pending:
                    change(self.state)
                self.state["updated"] = now_iso()
            time.sleep(1 + attempt)
        print(f"(status not saved to GitHub: {res.stderr.strip()[-300:]})", flush=True)
        return False

    def _push_api(self, message):
        for attempt in range(6):
            sha, refused = self.gh.write_file(STATE_FILE, self._text(), BRANCH, message, self.sha)
            if sha:
                self.sha = sha
                self.pending.clear()
                return True
            self.refresh()                       # someone saved first: theirs + our changes again
            self.state["updated"] = now_iso()
            time.sleep(min(5, 0.5 * (attempt + 1)))
        print("(status not saved to GitHub: kept being refused)", flush=True)
        return False

    # ---- changes
    def change(self, fn, urgent=False, message="status"):
        if self.off:
            return
        with self.lock:
            fn(self.state)
            self.state["updated"] = now_iso()
            if not self.remote:
                self._write_file()
                return
            self.pending.append(fn)
            wait = PUSH_EVERY - (time.time() - self.last_push)
            if urgent or wait <= 0:
                self._flush_locked(message)
            elif self.timer is None:
                self.timer = threading.Timer(wait, self.flush)
                self.timer.daemon = True
                self.timer.start()

    def _flush_locked(self, message="status"):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.remote and self.pending:
            self.last_push = time.time()
            self._push(message)

    def flush(self):
        with self.lock:
            try:
                self._flush_locked()
            except Exception as e:
                print(f"(status not saved: {e})", flush=True)


_STORE = None


def store():
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return _STORE


def use_github(repo, token, api_url=None):
    """The laptop: keep state.json on GitHub through the contents API."""
    global _STORE
    reset()
    _STORE = Store("api", repo, token, api_url)
    return _STORE


@safe
def refresh():
    store().refresh()


def reset():
    """Forgets the loaded state (tests use this after changing STATUS_DIR)."""
    global _STORE
    if _STORE and _STORE.timer:
        _STORE.timer.cancel()
    _STORE = None


@atexit.register
def _at_exit():
    if _STORE:
        _STORE.flush()


# ---------------------------------------------------------------- reading
@safe
def load_state():
    """A copy of the current state.json."""
    return copy.deepcopy(store().state)


@safe
def read_laptop():
    """laptop.json as last fetched (or {} if there isn't one)."""
    s = store()
    return (s._read_file(LAPTOP_FILE) if not s.off else None) or {}


def _find(state, name_or_id):
    mid = movie_id(name_or_id)
    return next((m for m in state["movies"] if m.get("id") in (name_or_id, mid)), None)


# ---------------------------------------------------------------- the run
@safe
def run_started(run_id=None):
    def f(st):
        st["run"] = {"active": True, "run_id": run_id, "started": now_iso(), "movie": None, "mode": None,
                     "stage": "idle", "part": None, "parts_total": None}
    store().change(f, urgent=True, message="run started")


@safe
def set_stage(stage, movie=KEEP, mode=KEEP, part=None, parts_total=None):
    if stage not in STAGES:
        stage = "idle"
    def f(st):
        r = st["run"]
        r["active"] = stage not in ("idle", "done", "failed")
        r["stage"] = stage
        if movie is not KEEP:
            r["movie"] = movie
        if mode is not KEEP:
            r["mode"] = mode
        r["part"], r["parts_total"] = part, parts_total
    store().change(f, message=f"stage {stage}")


@safe
def run_finished(failed=False):
    def f(st):
        st["run"].update(active=False, stage="failed" if failed else "done", movie=None, mode=None,
                         part=None, parts_total=None)
    store().change(f, urgent=True, message="run finished")
    store().flush()


@safe
def set_queue(items):
    """items: [{"name", "source", "added", "size_mb"}], first in line first."""
    items = [dict(i) for i in items]
    def f(st):
        st["queue"] = items
    store().change(f, message="queue")


# ---------------------------------------------------------------- movies
@safe
def movie_started(name, source="drive", modes=()):
    def f(st):
        st["movies"] = [m for m in st["movies"] if m.get("id") != movie_id(name)]
        st["movies"].insert(0, {
            "id": movie_id(name), "name": name, "status": "processing", "source": source,
            "started": now_iso(), "finished": None, "duration": None, "subtitles": None, "error": None,
            "modes": {m: {"label": LABELS.get(m, m), "planned": 0, "parts": []} for m in modes},
        })
        del st["movies"][MAX_MOVIES:]
        st["queue"] = [q for q in st["queue"] if q.get("name") != name]
    store().change(f, message=f"started {name}")


@safe
def movie_duration(name, seconds):
    """How long the whole movie is (the app uses it to draw where each part sits)."""
    def f(st):
        m = _find(st, name)
        if m:
            m["duration"] = round(float(seconds), 1)
    store().change(f, message=f"duration {name}")


@safe
def movie_planned(name, mode, count):
    def f(st):
        m = _find(st, name)
        if m:
            m["modes"].setdefault(mode, {"label": LABELS.get(mode, mode), "planned": 0, "parts": []})
            m["modes"][mode]["planned"] = count
    store().change(f, message=f"planned {name}")


@safe
def part_ready(name, mode, n, start, end, drive_path, caption=None):
    def f(st):
        m = _find(st, name)
        if not m:
            return
        md = m["modes"].setdefault(mode, {"label": LABELS.get(mode, mode), "planned": 0, "parts": []})
        old = next((p for p in md["parts"] if p.get("n") == n), None)
        part = {"n": n, "start": round(start, 1), "end": round(end, 1), "seconds": round(end - start, 1),
                "drive_path": drive_path, "caption": caption,
                "post": (old or {}).get("post") or {"status": "none", "at": None, "url": None, "error": None}}
        md["parts"] = sorted([p for p in md["parts"] if p.get("n") != n] + [part], key=lambda p: p["n"])
    store().change(f, message=f"{name} part {n}")


@safe
def movie_finished(name, subtitles=None):
    def f(st):
        m = _find(st, name)
        if m:
            m.update(status="done", finished=now_iso(), subtitles=subtitles, error=None)
    store().change(f, urgent=True, message=f"finished {name}")


@safe
def movie_failed(name, error):
    def f(st):
        m = _find(st, name)
        if m:
            m.update(status="failed", finished=now_iso(), error=str(error)[:500])
    store().change(f, urgent=True, message=f"failed {name}")


# ---------------------------------------------------------------- events + posting
@safe
def add_event(kind, title, message="", urgent=False):
    event = {"at": now_iso(), "type": kind if kind in EVENT_TYPES else "info", "title": title, "message": message}
    def f(st):
        st["events"].insert(0, dict(event))
        del st["events"][MAX_EVENTS:]
    store().change(f, urgent=urgent, message=f"event {event['type']}")


@safe
def set_post(movie, mode, n, status, url=None, error=None):
    """Records a TikTok post result for one part. status: none | scheduled | posted | failed."""
    at = now_iso()
    def f(st):
        m = _find(st, movie)
        part = m and next((p for p in m["modes"].get(mode, {}).get("parts", []) if p.get("n") == n), None)
        if not part:
            return
        post = part.setdefault("post", {})
        attempts = post.get("attempts", 0) + (1 if status in ("posted", "failed") else 0)
        post.update(status=status, at=at, url=url, error=error, attempts=attempts)
    store().change(f, urgent=True, message=f"post {status}")


@safe
def set_posting(enabled, next_at, posted_today):
    new = {"enabled": bool(enabled), "next_at": next_at, "posted_today": int(posted_today)}
    if store().state.get("posting") == new:
        return   # nothing changed: don't save (the poster checks every hour)
    def f(st):
        st["posting"] = dict(new)
    store().change(f, urgent=True, message="posting")


@safe
def flush():
    store().flush()
