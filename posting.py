"""
TikTok posting rules, the browser poster (the default) and the older Composio call. Used by the laptop helper (laptop/agent.py), which
posts the parts it has downloaded, on the schedule set in the app (settings.json "posting"):

  * only when posting is switched on, during the hour of one of the posting times
    (in posting.timezone), at most one post per time and at most `per_day` posts a day;
  * oldest movie first, Part 1 before Part 2, only modes listed in posting.modes;
  * a part that failed is tried again at the next posting time, up to 3 tries.
  * when = "gap" (the default): one part every 15-30 minutes (a random gap each time, so it never
    looks like a bot), only between active_from and active_to, at most per_day a day.

Browser posting (method "browser", the default): the laptop opens TikTok Studio in its own Chrome
window that stays logged in to your account (log in once: python agent.py --tiktok-login), uploads
the part, sets its thumbnail as the cover, types the caption and taps Post. Needs no TikTok app
approval, so posts are public. The same window reads your views and watch time for Insights.

TikTok rule: until the TikTok app behind the Composio connection passes TikTok's audit, every
post is private ("Only me", SELF_ONLY), and the TikTok account itself must be set to private.
"""
import json
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

MAX_ATTEMPTS = 3   # a part that failed this many times is skipped
AS_READY_DAILY_MAX = 15   # "as soon as ready" mode: TikTok refuses more than about 15 posts a day from API apps
PRIVACY = {"self_only": "SELF_ONLY", "public": "PUBLIC_TO_EVERYONE",
           "friends": "MUTUAL_FOLLOW_FRIENDS", "followers": "FOLLOWER_OF_CREATOR"}


# ---------------------------------------------------------------- schedule
def parse_times(times):
    out = []
    for t in times or []:
        try:
            h, m = str(t).strip().split(":")
            out.append((int(h) % 24, int(m) % 60))
        except ValueError:
            print(f"(ignoring posting time {t!r}: use HH:MM)", flush=True)
    return sorted(set(out))


def slot_now(now, times):
    """The posting time whose hour we're in right now, e.g. (18, 0) at 18:07, or None."""
    return next((hm for hm in parse_times(times) if hm[0] == now.hour), None)


def _when(text, tz):
    try:
        return datetime.fromisoformat(text).astimezone(tz)
    except (TypeError, ValueError):
        return None


def all_parts(state):
    """(movie, mode, part) for every finished part, oldest movie first, parts in order."""
    movies = sorted(state.get("movies", []), key=lambda m: m.get("started") or "9999")
    for m in movies:
        for mode, md in (m.get("modes") or {}).items():
            for p in sorted(md.get("parts", []), key=lambda p: p.get("n", 0)):
                yield m, mode, p


def posted_times(state, tz):
    out = []
    for _, _, p in all_parts(state):
        post = p.get("post") or {}
        when = _when(post.get("at"), tz)
        if post.get("status") in ("posted", "inbox") and when:
            out.append(when)
    return out


def posted_today(state, now):
    return sum(1 for t in posted_times(state, now.tzinfo) if t.date() == now.date())


def posted_this_hour(state, now):
    return any(t.date() == now.date() and t.hour == now.hour for t in posted_times(state, now.tzinfo))


def next_part(state, modes, available=None):
    """
    The next part to post: oldest movie first, modes in the order given, Part 1 before Part 2.
    available(part) -> True (file is on the laptop), None (not downloaded yet: wait, keeping the
    order) or False (gone for good, e.g. cleaned up: skip it).
    """
    movies = sorted(state.get("movies", []), key=lambda m: m.get("started") or "9999")
    for m in movies:
        for mode in modes:
            for p in sorted((m.get("modes") or {}).get(mode, {}).get("parts", []), key=lambda p: p.get("n", 0)):
                post = p.get("post") or {}
                if post.get("status") in ("posted", "inbox"):
                    continue
                if post.get("status") == "failed" and post.get("attempts", 0) >= MAX_ATTEMPTS:
                    continue
                if not p.get("drive_path"):
                    continue
                here = True if available is None else available(p)
                if here is None:
                    return None
                if here is False:
                    continue
                return m, mode, p
    return None


def _hm(text, default):
    try:
        h, m = str(text).strip().split(":")
        return int(h) % 24 * 60 + int(m) % 60
    except (ValueError, AttributeError):
        return default


def in_active_hours(now, posting):
    """True between active_from and active_to (e.g. 09:00-23:30; a window past midnight works too)."""
    a, b = _hm(posting.get("active_from"), 9 * 60), _hm(posting.get("active_to"), 23 * 60 + 30)
    t = now.hour * 60 + now.minute
    return a <= t < b if a <= b else (t >= a or t < b)


def next_gap_minutes(posting, rng=random):
    """A random gap before the next post, between gap_min and gap_max minutes (15-30 by default)."""
    lo = max(5, int(posting.get("gap_min") or 15))
    hi = max(lo, int(posting.get("gap_max") or 30))
    return rng.uniform(lo, hi)


def gap_due(now, posting, next_due):
    """When the next gap-mode post may go (a datetime): the saved due time, moved into active hours."""
    t = max(now, next_due) if next_due else now
    if in_active_hours(t, posting):
        return t
    a = _hm(posting.get("active_from"), 9 * 60)       # asleep: the next time the posting hours start
    start = datetime(t.year, t.month, t.day, a // 60, a % 60, tzinfo=t.tzinfo)
    return start if start > t else start + timedelta(days=1)


def next_at(now, posting, count_today, next_due=None):
    """When the next post is due (ISO text), or None if posting is off."""
    if not posting.get("enabled") or str(posting.get("when") or "") == "as_ready":
        return None                        # as soon as ready: there's no fixed next time
    if str(posting.get("when") or "times") == "gap":
        t = gap_due(now, posting, next_due)
        if count_today >= int(posting.get("per_day", 10)):
            tomorrow = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo) + timedelta(days=1)
            t = gap_due(tomorrow, posting, None)
        return t.isoformat(timespec="seconds")
    times = parse_times(posting.get("times"))
    for days in range(0, 8):
        day = (now + timedelta(days=days)).date()
        for h, m in times:
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=now.tzinfo)
            if t <= now:
                continue
            if days == 0 and count_today >= int(posting.get("per_day", 0)):
                continue
            return t.isoformat(timespec="seconds")
    return None


def build_caption(part_caption, hashtags):
    """The part's caption plus any of your hashtags it doesn't already have (TikTok max 2200)."""
    text = (part_caption or "").strip()
    have = {w.lower() for w in text.split() if w.startswith("#")}
    extra = [h for h in (hashtags or "").split() if h.startswith("#") and h.lower() not in have]
    if extra:
        text = (text + ("\n" if text else "") + " ".join(extra)).strip()
    return text[:2200]


# ---------------------------------------------------------------- Composio / TikTok
def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _find_key(obj, key):
    """Finds `key` anywhere inside nested dicts/lists (Composio nests results under data / data.data)."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def _execute(client, slug, arguments, creds):
    kw = {"arguments": arguments}
    if creds.get("user_id"):
        kw["user_id"] = creds["user_id"]
    if creds.get("connected_account_id"):
        kw["connected_account_id"] = creds["connected_account_id"]
    version = creds.get("version") or "latest"
    try:
        res = client.tools.execute(slug, version=version,
                                   dangerously_skip_version_check=(version == "latest"), **kw)
    except TypeError:          # older SDKs without toolkit versions
        res = client.tools.execute(slug, **kw)
    ok = bool(_get(res, "successful", _get(res, "successfull", False)))
    return ok, _get(res, "data"), _get(res, "error")


def post_video(path, caption, privacy, creds=None, publish=True):
    """
    Posts one video file to TikTok through Composio's TikTok toolkit.
    privacy: SELF_ONLY / PUBLIC_TO_EVERYONE / MUTUAL_FOLLOW_FRIENDS / FOLLOWER_OF_CREATOR.
    Returns (ok, url_or_id, error).

    Uses TIKTOK_UPLOAD_VIDEO with publish=true (the SDK uploads the local file for us), then
    polls TIKTOK_FETCH_PUBLISH_STATUS until TikTok says it's done. See plan/COMPOSIO-NOTES.md.
    creds: {api_key, user_id, connected_account_id, version, username} (the laptop's config.json).
    publish=False: "draft" mode. The video goes to the owner's TikTok inbox as a draft and they
    tap Post in the TikTok app. Unaudited TikTok apps may only direct-post to PRIVATE accounts;
    drafts work with public accounts too.
    """
    creds = creds or {}
    if not creds.get("api_key"):
        return False, None, "composio_api_key isn't set in the laptop's config.json"
    if not (creds.get("user_id") or creds.get("connected_account_id")):
        return False, None, "composio_user_id isn't set in the laptop's config.json"
    try:
        from composio import Composio
    except ImportError:
        return False, None, "The composio Python package isn't installed (pip install composio)"
    path = Path(path).resolve()
    try:
        try:
            client = Composio(api_key=creds["api_key"],
                              dangerously_allow_auto_upload_download_files=True,
                              file_upload_dirs=[str(path.parent)])
        except TypeError:      # older SDKs upload local files automatically
            client = Composio(api_key=creds["api_key"])
        args = {"file_to_upload": str(path), "publish": bool(publish)}
        if publish:
            args.update(caption=caption, privacy_level=privacy)
        ok, data, error = _execute(client, "TIKTOK_UPLOAD_VIDEO", args, creds)
        if ok and not publish:
            return True, _find_key(data, "publish_id"), None
        if not ok:
            return False, None, str(error or data or "TikTok upload failed")[:500]
        publish_id = _find_key(data, "publish_id")
        if not publish_id:
            return True, None, None
        # TikTok finishes the post in the background: check on it for up to about 5 minutes.
        waited = 0
        for delay in (5, 10, 20, 30, 30, 30, 30, 30, 30, 30, 30):
            time.sleep(delay)
            waited += delay
            ok, data, error = _execute(client, "TIKTOK_FETCH_PUBLISH_STATUS", {"publish_id": publish_id}, creds)
            if not ok:
                continue
            state = str(_find_key(data, "status") or "")
            print(f"  TikTok status after {waited}s: {state}", flush=True)
            if state == "FAILED":
                return False, publish_id, str(_find_key(data, "fail_reason") or "TikTok rejected the video")
            if state in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                ids = _find_key(data, "publicaly_available_post_id") or _find_key(data, "publicly_available_post_id")
                user = str(creds.get("username") or "").lstrip("@")
                if ids and user:
                    return True, f"https://www.tiktok.com/@{user}/video/{ids[0]}", None
                return True, publish_id, None
        return True, publish_id, None   # accepted by TikTok, still processing
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"[:500]


def decide(state, posting, now, force=False, available=None, next_due=None):
    """(movie, mode, part) to post right now, or (None, reason). next_due: gap mode's next allowed time."""
    count = posted_today(state, now)
    if not posting.get("enabled") and not force:
        return None, "Posting is switched off in the settings."
    when = str(posting.get("when") or "times").lower()
    as_ready = when == "as_ready"
    if when == "gap" and not force:
        if not in_active_hours(now, posting):
            return None, f"Outside posting hours ({now:%H:%M})."
        if count >= int(posting.get("per_day", 10)):
            return None, f"Already posted {count} today (limit {posting.get('per_day')})."
        if next_due and now < next_due:
            return None, f"Next post after {next_due:%H:%M} (random 15-30 min gap)."
    elif as_ready and not force:
        if count >= AS_READY_DAILY_MAX:
            return None, f"Already sent {count} today (TikTok's daily limit is about {AS_READY_DAILY_MAX})."
    elif not force:
        if not slot_now(now, posting.get("times")):
            return None, f"Not a posting time ({now:%H:%M})."
        if count >= int(posting.get("per_day", 0)):
            return None, f"Already posted {count} today (limit {posting.get('per_day')})."
        if posted_this_hour(state, now):
            return None, "Already posted in this hour's slot."
    pick = next_part(state, posting.get("modes") or ["recap", "character", "hype", "stretch", "highlights"], available)
    return (pick, None) if pick else (None, "Nothing to post yet.")


# ---------------------------------------------------------------- browser posting (TikTok Studio)
STUDIO_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
STUDIO_CONTENT = "https://www.tiktok.com/tiktokstudio/content"
LOGIN_URL = "https://www.tiktok.com/login"


def ensure_playwright(log=print):
    """The browser robot (Playwright): installed the first time it's needed. True when ready."""
    import subprocess
    import sys
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        pass
    log("Installing Playwright (the browser robot that posts to TikTok), once...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "playwright"], timeout=1800,
                   capture_output=True)
    import importlib
    importlib.invalidate_caches()
    try:
        import playwright  # noqa: F401,F811
        return True
    except ImportError:
        return False


def _install_chromium():
    import subprocess
    import sys
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], timeout=1800, capture_output=True)


def _pause(lo=0.6, hi=1.8):
    time.sleep(random.uniform(lo, hi))            # human-ish pauses between clicks


class Browser:
    """A Chrome window with its own profile folder, so the TikTok login is remembered between runs."""

    def __init__(self, profile_dir, headless=False):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.pw = self.ctx = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.pw = sync_playwright().start()
        kw = dict(user_data_dir=str(self.profile_dir), headless=self.headless, viewport={"width": 1366, "height": 860},
                  args=["--disable-blink-features=AutomationControlled"], ignore_default_args=["--enable-automation"])
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(channel="chrome", **kw)   # your real Chrome
        except Exception:
            try:
                self.ctx = self.pw.chromium.launch_persistent_context(**kw)
            except Exception:
                _install_chromium()
                self.ctx = self.pw.chromium.launch_persistent_context(**kw)
        return self

    def page(self):
        return self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def logged_in(self):
        return any(c.get("name") == "sessionid" and c.get("value") for c in self.ctx.cookies("https://www.tiktok.com"))

    def __exit__(self, *exc):
        for fn in (lambda: self.ctx.close(), lambda: self.pw.stop()):
            try:
                fn()
            except Exception:
                pass


def browser_login(profile_dir, wait_minutes=15, log=print):
    """Opens TikTok's login page in the poster's own Chrome window and waits until you've logged in."""
    if not ensure_playwright(log):
        return False, "Couldn't install Playwright (py -m pip install playwright)"
    with Browser(profile_dir, headless=False) as b:
        page = b.page()
        if b.logged_in():
            return True, None
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        log(f"Log in to TikTok in the window that just opened (waiting up to {wait_minutes} minutes)...")
        end = time.time() + wait_minutes * 60
        while time.time() < end:
            if b.logged_in():
                time.sleep(3)                          # let TikTok finish saving the login
                return True, None
            time.sleep(2)
    return False, "Not logged in yet: run it again and finish logging in"


def _click_text(page, texts, timeout=4000):
    for t in texts:
        loc = page.get_by_text(t, exact=True)
        try:
            loc.first.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _set_cover(page, cover, log=print):
    """Edit cover -> Upload cover -> the part's thumbnail -> Confirm. Best effort: posting goes on without it."""
    try:
        if not _click_text(page, ["Edit cover", "Select cover"], timeout=8000):
            return False
        _pause()
        _click_text(page, ["Upload cover", "Upload image"], timeout=6000)
        _pause()
        inp = page.locator('input[type="file"][accept*="image"]')
        inp.first.set_input_files(str(cover), timeout=10000)
        _pause(2, 4)
        page.get_by_role("button", name=re.compile(r"^(Confirm|Save|Done)$")).last.click(timeout=15000)
        _pause(1, 2)
        return True
    except Exception as e:
        log(f"  (couldn't set the cover, posting with TikTok's own: {e})")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


# Pop-ups TikTok Studio puts over the editor (feature tours, "turn on content checks?", "keep editing?").
# Only buttons that close them without changing anything; never "Turn on" or "Discard".
DISMISS = ["Got it", "Not now", "Cancel", "Skip", "Close", "Maybe later", "OK", "Dismiss", "Continue editing"]
CAPTION_BOX = 'div[contenteditable="true"]'


def _dismiss_popups(page, rounds=3):
    """Closes whatever modal or tour is covering the upload page. True if it closed something."""
    closed = False
    for _ in range(rounds):
        hit = False
        for scope in ('[role="dialog"]', '[class*="modal" i]', '[class*="joyride" i]', '[class*="tour" i]',
                      '[class*="popover" i]', '[class*="tooltip" i]'):
            for t in DISMISS:
                btn = page.locator(scope).get_by_role("button", name=t, exact=True)
                try:
                    if btn.count() and btn.first.is_visible():
                        btn.first.click(timeout=2000)
                        hit = closed = True
                        _pause(0.4, 0.9)
                except Exception:
                    continue
        if not hit:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            break
    return closed


def _upload_done(page, post_btn):
    return post_btn.get_attribute("aria-disabled") != "true" and post_btn.is_enabled() \
        and not page.get_by_text(re.compile(r"Uploading|\d+%")).count()


def _wait_upload(page, post_btn, minutes=15):
    """Waits for the video to finish uploading (Post turns on), closing pop-ups as they show up."""
    end = time.time() + minutes * 60
    while time.time() < end:
        if _upload_done(page, post_btn):
            return True
        _dismiss_popups(page, rounds=1)
        time.sleep(3)
    return False


def _focus_caption(page, log=print):
    """Puts the cursor in the caption box. A pop-up or the upload overlay can sit on top of it (the click
    then times out though the box is "visible, enabled and stable"), so: close pop-ups and try a normal
    click a few times, then a forced click, then focus it straight through the page."""
    box = page.locator(CAPTION_BOX).first
    box.wait_for(state="visible", timeout=60000)
    for i in range(3):
        _dismiss_popups(page)
        try:
            box.scroll_into_view_if_needed(timeout=5000)
            box.click(timeout=8000)
            return box
        except Exception as e:
            log(f"  (caption box is covered, try {i + 1}: {str(e).splitlines()[0][:120]})")
            _pause(1.5, 3)
    try:
        box.click(timeout=5000, force=True)
    except Exception:
        pass
    box.evaluate("el => { el.focus(); const r = document.createRange(); r.selectNodeContents(el); r.collapse(false);"
                 " const s = getSelection(); s.removeAllRanges(); s.addRange(r); }")
    if not box.evaluate("el => el === document.activeElement || el.contains(document.activeElement)"):
        raise RuntimeError("Couldn't get into TikTok's caption box (something is covering it)")
    return box


def _type_caption(page, caption, log=print):
    _focus_caption(page, log)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    _pause(0.3, 0.8)
    lines = caption.split("\n")
    for i, line in enumerate(lines):
        for word in line.split(" "):
            page.keyboard.type(word, delay=random.randint(25, 70))
            if word.startswith(("#", "@")):
                time.sleep(1.2)                         # hashtag suggestions pop up...
            page.keyboard.type(" ")                     # ...a space closes them without picking one
        if i < len(lines) - 1:
            page.keyboard.press("Shift+Enter")


def _snapshot(page, profile_dir):
    """Keeps a screenshot of the page when a post fails, next to the Chrome profile, for working out why."""
    try:
        shot = Path(profile_dir).parent / "tiktok-last-error.png"
        page.screenshot(path=str(shot), full_page=True, timeout=10000)
        return shot
    except Exception:
        return None


def browser_post(path, caption, profile_dir, cover=None, headless=False, log=print):
    """Posts one video through TikTok Studio in the logged-in Chrome window. Returns (ok, url, error)."""
    if not ensure_playwright(log):
        return False, None, "Couldn't install Playwright (py -m pip install playwright)"
    path = Path(path).resolve()
    if not path.exists():
        return False, None, f"The video isn't on the laptop any more: {path.name}"
    try:
        with Browser(profile_dir, headless=headless) as b:
            page = b.page()
            try:
                return _studio_post(b, page, path, caption, cover, log)
            except Exception:
                shot = _snapshot(page, profile_dir)          # while the window is still open
                if shot:
                    log(f"  (screenshot of the TikTok page: {shot})")
                raise
    except Exception as e:
        msg = str(e).split("Call log:")[0].strip()          # Playwright's call log fills the app card
        return False, None, f"{type(e).__name__}: {msg}"[:300]


def _studio_post(b, page, path, caption, cover, log):
    page.goto(STUDIO_UPLOAD, wait_until="domcontentloaded", timeout=90000)
    _pause(2, 4)
    if not b.logged_in() or "login" in page.url:
        return False, None, "TikTok isn't logged in on the laptop (run: py agent.py --tiktok-login)"
    page.locator('input[type="file"][accept*="video"]').first.set_input_files(str(path), timeout=60000)
    post_btn = page.locator('button[data-e2e="post_video_button"]')
    post_btn.wait_for(state="visible", timeout=10 * 60000)
    _pause(2, 4)
    _wait_upload(page, post_btn)                    # the editor is covered while it uploads
    _dismiss_popups(page)
    _type_caption(page, caption, log)
    _pause()
    if cover and Path(cover).exists():
        _set_cover(page, Path(cover).resolve(), log)
    _dismiss_popups(page)
    _wait_upload(page, post_btn)
    _pause(2, 5)
    try:
        post_btn.click(timeout=15000)
    except Exception:
        _dismiss_popups(page)
        post_btn.click(timeout=15000, force=True)
    _pause(2, 3)
    _click_text(page, ["Post now", "Post anyway"], timeout=3000)   # TikTok's "content check" pop-up
    end = time.time() + 5 * 60
    while time.time() < end and "/content" not in page.url:
        if page.get_by_text(re.compile(r"(Your video (has been|is being) (uploaded|posted))|Manage your posts")).count():
            break
        time.sleep(2)
    if "/content" not in page.url:
        page.goto(STUDIO_CONTENT, wait_until="domcontentloaded", timeout=60000)
    _pause(3, 5)
    url = None
    try:
        href = page.locator('a[href*="/video/"]').first.get_attribute("href", timeout=15000)
        url = href if href and href.startswith("http") else f"https://www.tiktok.com{href}" if href else None
    except Exception:
        pass
    return True, url, None


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


SETTLED_DAYS = 14     # after two weeks a video's watch time hardly changes: its last reading is kept


def needs_analytics(v, known, now=None):
    """True when this video's analytics page is worth opening: it's new to us, or it's under two weeks old and
    has gained 10%+ views since we last read its watch time. Older, settled videos keep their last numbers."""
    k = (known or {}).get(v["id"]) or {}
    if k.get("avg_watch") is None:
        return True
    young = (now or time.time()) - v.get("created", 0) < SETTLED_DAYS * 86400
    return young and v.get("views", 0) >= 1.1 * max(1, int(k.get("views") or 0))


def browser_stats(profile_dir, username, deep=15, headless=True, log=print, known=None, progress=None):
    """Your TikTok videos with their numbers, read from your profile and TikTok Studio:
    [{"id", "desc", "created", "views", "likes", "comments", "shares", "saves", "seconds",
      "avg_watch", "full_watch"}]. Views, likes etc. for every video come from one look at your profile.
    avg_watch (seconds) and full_watch (% who watched to the end) need each video's own analytics page, so
    only videos that changed are opened (see needs_analytics, at most `deep`); the rest keep the numbers in
    `known` ({id: video from last time})."""
    if not ensure_playwright(log):
        return None, "Couldn't install Playwright"
    user = str(username or "").lstrip("@")
    if not user:
        return None, "Your TikTok username isn't set (Settings > TikTok > Username)"
    items = {}
    try:
        with Browser(profile_dir, headless=headless) as b:
            page = b.page()

            def grab(resp):
                if "/api/post/item_list" not in resp.url:
                    return
                try:
                    for it in (resp.json() or {}).get("itemList") or []:
                        st = it.get("stats") or {}
                        items[str(it.get("id"))] = {
                            "id": str(it.get("id")), "desc": it.get("desc") or "", "created": _num(it.get("createTime")),
                            "views": _num(st.get("playCount")), "likes": _num(st.get("diggCount")),
                            "comments": _num(st.get("commentCount")), "shares": _num(st.get("shareCount")),
                            "saves": _num(st.get("collectCount")), "seconds": _num((it.get("video") or {}).get("duration")),
                            "url": f"https://www.tiktok.com/@{user}/video/{it.get('id')}"}
                except Exception:
                    pass
            page.on("response", grab)
            page.goto(f"https://www.tiktok.com/@{user}", wait_until="domcontentloaded", timeout=90000)
            for _ in range(12):                                 # scroll to load older videos
                time.sleep(2.5)
                page.mouse.wheel(0, 4000)
            for v in items.values():                           # settled videos: last reading
                k = (known or {}).get(v["id"]) or {}
                for key in ("avg_watch", "full_watch", "followers"):
                    if k.get(key) is not None:
                        v[key] = k[key]
            todo = [v for v in sorted(items.values(), key=lambda x: -x["created"]) if needs_analytics(v, known)][:deep]
            log(f"Insights: {len(items)} videos, opening analytics for {len(todo)} that changed")
            for i, v in enumerate(todo):
                if progress:
                    progress(i, len(todo))
                try:
                    page.goto(f"https://www.tiktok.com/tiktokstudio/analytics/{v['id']}", wait_until="domcontentloaded",
                              timeout=60000)
                    time.sleep(6)
                    text = page.inner_text("body", timeout=15000)
                    m = re.search(r"Average watch time\s*\n?\s*([\d.,]+)\s*s", text)
                    if m:
                        v["avg_watch"] = float(m.group(1).replace(",", ""))
                    m = re.search(r"Watched full video\s*\n?\s*([\d.,]+)\s*%", text)
                    if m:
                        v["full_watch"] = float(m.group(1).replace(",", ""))
                    m = re.search(r"New followers\s*\n?\s*([\d,]+)", text)
                    if m:
                        v["followers"] = int(m.group(1).replace(",", ""))
                    _pause(1, 3)
                except Exception as e:
                    log(f"  (no watch-time numbers for video {v['id']}: {e})")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:500]
    return sorted(items.values(), key=lambda x: -x["created"]), None
