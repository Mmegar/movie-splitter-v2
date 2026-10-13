"""
TikTok poster: runs every hour on GitHub Actions (.github/workflows/post.yml).

If posting is switched on in the app (settings.json "posting" section), and right now is in
the hour of one of your posting times, and fewer than "per_day" parts went out today, it
takes the oldest part that hasn't been posted yet (oldest movie first, Part 1 before Part 2),
downloads it from Google Drive, posts it to TikTok through Composio, and records the result
in the app's status file and as a phone notification.

TikTok rule: until the TikTok app behind the connection passes TikTok's audit, every post is
private ("Only me", SELF_ONLY), and the TikTok account itself must be set to private.

Test without posting:  python3 poster.py --dry-run [--force]
    --force   ignore the schedule (post the next part now)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import settings
import status

REMOTE = "gdrive:"
WORK = Path("work-post")
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
        if post.get("status") == "posted" and when:
            out.append(when)
    return out


def posted_today(state, now):
    return sum(1 for t in posted_times(state, now.tzinfo) if t.date() == now.date())


def posted_this_hour(state, now):
    return any(t.date() == now.date() and t.hour == now.hour for t in posted_times(state, now.tzinfo))


def next_part(state, modes):
    """The next part to post: oldest movie first, modes in the order given, Part 1 before Part 2."""
    movies = sorted(state.get("movies", []), key=lambda m: m.get("started") or "9999")
    for m in movies:
        for mode in modes:
            for p in sorted((m.get("modes") or {}).get(mode, {}).get("parts", []), key=lambda p: p.get("n", 0)):
                post = p.get("post") or {}
                if post.get("status") == "posted":
                    continue
                if post.get("status") == "failed" and post.get("attempts", 0) >= MAX_ATTEMPTS:
                    continue
                if not p.get("drive_path"):
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


def _execute(client, slug, arguments):
    kw = {"arguments": arguments}
    if os.environ.get("COMPOSIO_USER_ID"):
        kw["user_id"] = os.environ["COMPOSIO_USER_ID"]
    if os.environ.get("COMPOSIO_CONNECTED_ACCOUNT_ID"):
        kw["connected_account_id"] = os.environ["COMPOSIO_CONNECTED_ACCOUNT_ID"]
    version = os.environ.get("COMPOSIO_TIKTOK_VERSION", "latest")
    try:
        res = client.tools.execute(slug, version=version,
                                   dangerously_skip_version_check=(version == "latest"), **kw)
    except TypeError:          # older SDKs without toolkit versions
        res = client.tools.execute(slug, **kw)
    ok = bool(_get(res, "successful", _get(res, "successfull", False)))
    return ok, _get(res, "data"), _get(res, "error")


def post_video(path, caption, privacy):
    """
    Posts one video file to TikTok through Composio's TikTok toolkit.
    privacy: SELF_ONLY / PUBLIC_TO_EVERYONE / MUTUAL_FOLLOW_FRIENDS / FOLLOWER_OF_CREATOR.
    Returns (ok, url_or_id, error).

    Uses TIKTOK_UPLOAD_VIDEO with publish=true (the SDK uploads the local file for us), then
    polls TIKTOK_FETCH_PUBLISH_STATUS until TikTok says it's done. See plan/COMPOSIO-NOTES.md.
    """
    if not os.environ.get("COMPOSIO_API_KEY"):
        return False, None, "COMPOSIO_API_KEY isn't set (add it in GitHub > Settings > Secrets)"
    if not (os.environ.get("COMPOSIO_USER_ID") or os.environ.get("COMPOSIO_CONNECTED_ACCOUNT_ID")):
        return False, None, "COMPOSIO_USER_ID isn't set (the user id you connected TikTok with in Composio)"
    try:
        from composio import Composio
    except ImportError:
        return False, None, "The composio Python package isn't installed (pip install composio)"
    path = Path(path).resolve()
    try:
        try:
            client = Composio(api_key=os.environ["COMPOSIO_API_KEY"],
                              dangerously_allow_auto_upload_download_files=True,
                              file_upload_dirs=[str(path.parent)])
        except TypeError:      # older SDKs upload local files automatically
            client = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
        ok, data, error = _execute(client, "TIKTOK_UPLOAD_VIDEO", {
            "file_to_upload": str(path), "caption": caption, "publish": True, "privacy_level": privacy,
        })
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
            ok, data, error = _execute(client, "TIKTOK_FETCH_PUBLISH_STATUS", {"publish_id": publish_id})
            if not ok:
                continue
            state = str(_find_key(data, "status") or "")
            print(f"  TikTok status after {waited}s: {state}", flush=True)
            if state == "FAILED":
                return False, publish_id, str(_find_key(data, "fail_reason") or "TikTok rejected the video")
            if state in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                ids = _find_key(data, "publicaly_available_post_id") or _find_key(data, "publicly_available_post_id")
                user = os.environ.get("TIKTOK_USERNAME", "").lstrip("@")
                if ids and user:
                    return True, f"https://www.tiktok.com/@{user}/video/{ids[0]}", None
                return True, publish_id, None
        return True, publish_id, None   # accepted by TikTok, still processing
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"[:500]


# ---------------------------------------------------------------- one run
def download(drive_path):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    local = WORK / Path(drive_path).name
    res = subprocess.run(["rclone", "copyto", f"{REMOTE}{drive_path}", str(local)], capture_output=True, text=True)
    if res.returncode != 0 or not local.exists():
        raise RuntimeError(f"Couldn't download '{drive_path}' from Google Drive (deleted?) {res.stderr.strip()[-200:]}")
    return local


def notify(title, message, tags, kind):
    try:
        import process
        process.notify(title, message, tags, kind=kind)
    except Exception as e:
        print(f"(notification not sent: {e})", flush=True)
        status.add_event(kind, title, message, urgent=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Posts the next part to TikTok if one is due.")
    ap.add_argument("--dry-run", action="store_true", help="show what would be posted; post nothing, change nothing")
    ap.add_argument("--force", action="store_true", help="ignore the schedule and post the next part now")
    args = ap.parse_args(argv)

    cfg = settings.load()
    posting = cfg["posting"]
    tz = ZoneInfo(posting.get("timezone") or "Pacific/Auckland")
    now = datetime.now(tz)
    state = status.load_state() or {}
    count = posted_today(state, now)

    def summary(n):
        if not args.dry_run:
            status.set_posting(posting.get("enabled"), next_at(datetime.now(tz), posting, n), n)

    if not posting.get("enabled") and not args.force:
        print("Posting is switched off in the settings.")
        summary(count)
        return 0
    if not args.force:
        if not slot_now(now, posting.get("times")):
            print(f"Not a posting time ({now:%H:%M}; times: {', '.join(posting.get('times') or [])}).")
            summary(count)
            return 0
        if count >= int(posting.get("per_day", 0)):
            print(f"Already posted {count} today (limit {posting.get('per_day')}).")
            summary(count)
            return 0
        if posted_this_hour(state, now):
            print("Already posted in this hour's slot.")
            summary(count)
            return 0

    pick = next_part(state, posting.get("modes") or ["stretch"])
    if not pick:
        print("Nothing left to post.")
        summary(count)
        return 0
    movie, mode, part = pick
    privacy = PRIVACY.get(str(posting.get("privacy", "self_only")).lower(), "SELF_ONLY")
    caption = build_caption(part.get("caption"), posting.get("hashtags"))
    label = status.LABELS.get(mode, mode)
    print(f"Next: {movie['name']} [{label}] Part {part['n']} -> TikTok ({privacy})\n{caption}", flush=True)
    if args.dry_run:
        print("(dry run: nothing downloaded or posted)")
        return 0

    run_idle = not (state.get("run") or {}).get("active")
    if run_idle:    # don't overwrite the stage while a movie is being cut
        status.set_stage("posting", movie=movie["name"], mode=mode, part=part["n"])
    status.set_post(movie["id"], mode, part["n"], "scheduled")
    try:
        local = download(part["drive_path"])
        ok, ref, error = post_video(local, caption, privacy)
    except Exception as e:
        ok, ref, error = False, None, str(e)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    url = ref if ref and str(ref).startswith("http") else None
    status.set_post(movie["id"], mode, part["n"], "posted" if ok else "failed", url=url, error=None if ok else error)
    title = f"{movie['name']}: Part {part['n']}"
    if ok:
        count += 1
        where = "privately (only you can see it)" if privacy == "SELF_ONLY" else "publicly"
        notify(f"📤 Posted to TikTok: {title}",
               f"[{label}] Posted {where}.{(' ' + url) if url else ''}\n\n📝 Caption:\n{caption}",
               ["outbox_tray"], "posted")
    else:
        notify(f"⚠️ TikTok post failed: {title}",
               f"[{label}] {error}\nIt will be tried again at the next posting time "
               f"(up to {MAX_ATTEMPTS} tries).", ["warning"], "post_failed")
    if run_idle:
        status.set_stage("idle", movie=None, mode=None)
    summary(count)
    status.flush()
    return 0   # a failed post was already reported; don't mark the whole GitHub run as broken


if __name__ == "__main__":
    sys.exit(main())
