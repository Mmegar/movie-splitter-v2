"""
TikTok posting rules and the Composio call. Used by the laptop helper (laptop/agent.py), which
posts the parts it has downloaded, on the schedule set in the app (settings.json "posting"):

  * only when posting is switched on, during the hour of one of the posting times
    (in posting.timezone), at most one post per time and at most `per_day` posts a day;
  * oldest movie first, Part 1 before Part 2, only modes listed in posting.modes;
  * a part that failed is tried again at the next posting time, up to 3 tries.

TikTok rule: until the TikTok app behind the Composio connection passes TikTok's audit, every
post is private ("Only me", SELF_ONLY), and the TikTok account itself must be set to private.
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

MAX_ATTEMPTS = 3   # a part that failed this many times is skipped
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


def next_at(now, posting, count_today):
    """When the next post is due (ISO text), or None if posting is off."""
    if not posting.get("enabled"):
        return None
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


def decide(state, posting, now, force=False, available=None):
    """(movie, mode, part) to post right now, or (None, reason)."""
    count = posted_today(state, now)
    if not posting.get("enabled") and not force:
        return None, "Posting is switched off in the settings."
    if not force:
        if not slot_now(now, posting.get("times")):
            return None, f"Not a posting time ({now:%H:%M})."
        if count >= int(posting.get("per_day", 0)):
            return None, f"Already posted {count} today (limit {posting.get('per_day')})."
        if posted_this_hour(state, now):
            return None, "Already posted in this hour's slot."
    pick = next_part(state, posting.get("modes") or ["stretch"], available)
    return (pick, None) if pick else (None, "Nothing to post yet.")
