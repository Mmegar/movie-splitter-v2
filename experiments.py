"""
Posting experiments (A/B tests) for Insights, and the guard rails for what Claude may change by itself.

One experiment runs at a time. It tries 2-3 values ("arms") of one thing, e.g. the narrator at 1.2x vs 1.3x:
- "movie" things (narration, thumbnails, part length...) alternate per movie: each new movie is cut with the
  arm that has the fewest movies so far.
- "post" things (hashtags) alternate per post, "day" things (posting hours) per day.
- "observe" compares what's already being posted (e.g. which kind of video) without changing anything.
Each arm needs MIN_PER_ARM videos that are at least MATURE_HOURS old. The arm whose median views beat every
other arm by WIN_MARGIN (without losing watch time) wins and becomes the setting; otherwise nothing changes.
Then the next experiment starts. All of it lives in the laptop's memory and is shown in insights.json.
"""
import re
import statistics
import time
from datetime import datetime, timezone

MIN_PER_ARM = 6        # videos per arm before a verdict
MATURE_HOURS = 24      # a video's views count once it's this old
WIN_MARGIN = 1.25      # the winner's median views must be 25% above every other arm
WATCH_FLOOR = 0.9      # ...and its watch time no worse than 90% of the current setting's
MAX_DAYS = 21          # an experiment that can't decide by then ends with no change
HISTORY = 20

MODES = ("recap", "character", "hype", "stretch", "highlights", "clips", "all")
FILTERS = ("vivid", "mystery", "warm", "teal_orange", "noir", "none")
TEMPOS = ("1", "1.1", "1.2", "1.3", "1.5")
KEEP_MODES = ("stretch",)   # the owner's viral videos came from Best stretch: never switched off by Claude

# what can be tested: setting -> (how arms are given out, name shown in the app)
FACTORS = {
    "narrator_tempo": ("movie", "Narrator speed"),
    "narrator_funny": ("movie", "Funny dial"),
    "narrator_intensity": ("movie", "Expressiveness"),
    "thumbnail_badge": ("movie", "Thumbnail badge"),
    "script_direction": ("movie", "Script notes"),
    "caption_direction": ("movie", "Caption notes"),
    "recap_part_seconds": ("movie", "Part length"),
    "recap_style": ("movie", "Recap style"),
    "filter": ("movie", "Film look"),
    "posting.hashtags": ("post", "Hashtags"),
    "posting.hours": ("day", "Posting hours"),
    "mode_first": ("observe", "Kind of video posted first"),
}
LIMITS = {"narrator_funny": (0.0, 1.0), "narrator_intensity": (0.5, 1.2), "recap_part_seconds": (60, 300)}

# tried in this order when Claude doesn't suggest one (the current value is always arm A)
DEFAULT_PLAN = [
    {"factor": "mode_first", "arms": [], "why": "See which kind of video gets the most views per part."},
    {"factor": "posting.hours", "arms": ["12:00-23:30", "17:00-23:30"], "why": "Do evening posts get more views?"},
    {"factor": "narrator_tempo", "arms": ["1.3"], "why": "Does a faster narrator keep people watching?"},
    {"factor": "thumbnail_badge", "arms": ["PART {n} 🔥"], "why": "Does a louder badge get more taps?"},
    {"factor": "recap_part_seconds", "arms": [150], "why": "Do shorter parts get watched to the end more?"},
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(settings, key):
    """settings value for a key; "posting.x" reads the posting section, "posting.hours" both active times."""
    if key == "posting.hours":
        p = settings.get("posting") or {}
        return f"{p.get('active_from') or '09:00'}-{p.get('active_to') or '23:30'}"
    if key == "mode_first":
        return ((settings.get("posting") or {}).get("modes") or [None])[0]
    if key.startswith("posting."):
        return (settings.get("posting") or {}).get(key.split(".", 1)[1])
    return settings.get(key)


def clean_value(key, v):
    """A value Claude proposed for a setting, made safe, or None when it isn't allowed."""
    if v is None:
        return None
    try:
        if key == "narrator_tempo":
            v = str(v).strip().rstrip("x")
            v = v[:-2] if v.endswith(".0") else v
            return v if v in TEMPOS else None
        if key in ("narrator_funny", "narrator_intensity"):
            lo, hi = LIMITS[key]
            return round(min(hi, max(lo, float(v))), 2)
        if key == "recap_part_seconds":
            lo, hi = LIMITS[key]
            return int(min(hi, max(lo, int(float(v)))))
        if key == "thumbnail_badge":
            v = str(v).strip()
            return v if "{n}" in v and len(v) <= 40 else None
        if key in ("script_direction", "caption_direction"):
            v = str(v).strip()
            return v if 0 < len(v) <= 600 else None
        if key == "recap_style":
            return v if v in ("fast", "chatty") else None
        if key == "filter":
            return v if v in FILTERS else None
        if key == "posting.hashtags":
            tags = [t for t in str(v).split() if re.fullmatch(r"#\w{1,40}", t)]
            return " ".join(tags[:6]) if tags else None
        if key == "posting.hours":
            m = re.fullmatch(r"\s*(\d{1,2}):(\d\d)\s*-\s*(\d{1,2}):(\d\d)\s*", str(v))
            if not m:
                return None
            a, b = (int(m[1]), int(m[2])), (int(m[3]), int(m[4]))
            if not all(h < 24 and mi < 60 for h, mi in (a, b)) or b <= a or (b[0] - a[0]) < 4:
                return None                                  # at least 4 hours a day, so posts still go out
            return f"{a[0]:02d}:{a[1]:02d}-{b[0]:02d}:{b[1]:02d}"
        if key == "mode_first":
            return v if v in MODES else None
    except (TypeError, ValueError):
        return None
    return None


def apply_changes(settings, changes):
    """Puts {"key": value} changes into a settings dict (in place); handles posting.* and posting.hours."""
    for k, v in changes.items():
        if k == "posting.hours":
            a, b = v.split("-")
            settings.setdefault("posting", {}).update(active_from=a, active_to=b)
        elif k == "mode_first":
            p = settings.setdefault("posting", {})
            p["modes"] = [v] + [m for m in (p.get("modes") or list(MODES[:5])) if m != v]
        elif k.startswith("posting."):
            settings.setdefault("posting", {})[k.split(".", 1)[1]] = v
        else:
            settings[k] = v
    return settings


# ---------------------------------------------------------------- what made the hits: never switched off
def protected_modes(videos, keep=KEEP_MODES):
    """Modes behind the channel's biggest videos (top 3, if they're at least twice the median), plus `keep`."""
    out = set(keep or ())
    seen = [v for v in videos if v.get("mode")]
    if seen:
        med = statistics.median(int(v.get("views") or 0) for v in videos) if videos else 0
        for v in sorted(seen, key=lambda v: -int(v.get("views") or 0))[:3]:
            if int(v.get("views") or 0) >= max(2 * med, 1000):
                out.add(v["mode"])
    return out


def mode_stats(videos):
    """{mode: {"videos", "median"}} for matched videos."""
    by = {}
    for v in videos:
        if v.get("mode"):
            by.setdefault(v["mode"], []).append(int(v.get("views") or 0))
    return {m: {"videos": len(x), "median": statistics.median(x)} for m, x in by.items()}


def guard_modes(proposed, current, videos, protected):
    """Claude's new list of modes to make. It may add modes freely; it may only drop a mode that isn't protected
    and has clearly failed: at least 8 videos with median views under half the channel's median."""
    cur = [m for m in (current or []) if m in MODES]
    want = [m for m in (proposed or []) if m in MODES]
    if not want:
        return None
    stats = mode_stats(videos)
    allv = [int(v.get("views") or 0) for v in videos if v.get("mode")]
    med = statistics.median(allv) if allv else 0
    out = list(dict.fromkeys(want))
    for m in cur:
        if m in out:
            continue
        s = stats.get(m)
        failed = s and s["videos"] >= 8 and med and s["median"] < 0.5 * med
        if m in protected or not failed:
            out.append(m)                                    # kept: no proof it doesn't work
    return out if out != cur else None


# ---------------------------------------------------------------- experiments
def new_experiment(proposal, settings, now=None):
    """An experiment from Claude's {"factor", "arms": [values], "why"} (or a DEFAULT_PLAN entry). Arm A is
    always the current setting. None if it isn't a valid test."""
    if not isinstance(proposal, dict) or proposal.get("factor") not in FACTORS:
        return None
    factor = proposal["factor"]
    level, label = FACTORS[factor]
    if level == "observe":
        modes = [m for m in ((settings.get("posting") or {}).get("modes") or []) if m in MODES]
        made = [m for m in (settings.get("modes") or []) if m in MODES]
        values = list(dict.fromkeys([m for m in made if m in modes] or modes))[:4]
    else:
        control = clean_value(factor, get(settings, factor))
        if control is None and factor in ("script_direction", "caption_direction"):
            control = ""                                     # blank notes are a fine "A"
        values = [control] + [clean_value(factor, v) for v in (proposal.get("arms") or [])]
        values = list(dict.fromkeys(v for v in values if v is not None))[:3]
        if not values or values[0] != control:
            return None
    if len(values) < 2:
        return None
    t = now or time.time()
    return {"id": datetime.fromtimestamp(t, timezone.utc).strftime("e%Y%m%d-%H%M"), "factor": factor, "level": level,
            "label": label, "why": str(proposal.get("why") or "")[:200],
            "arms": [{"name": "ABC"[i], "value": v, "control": level != "observe" and i == 0}
                     for i, v in enumerate(values)],
            "started": datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds"), "started_ts": t,
            "status": "running", "assign": {}}


def _least_used(exp):
    counts = [0] * len(exp["arms"])
    for i in exp["assign"].values():
        if 0 <= i < len(counts):
            counts[i] += 1
    return counts.index(min(counts))


def movie_overrides(exp, movie):
    """Settings to cut this movie with (and records its arm). {} when the experiment isn't per movie."""
    if not exp or exp.get("status") != "running" or exp["level"] != "movie" or not movie:
        return {}
    i = exp["assign"].get(movie)
    if i is None:
        i = exp["assign"][movie] = _least_used(exp)
    return {exp["factor"]: exp["arms"][i]["value"]}


def post_key(movie, mode, n):
    return f"{movie}|{mode}|{n}"


def post_overrides(exp, key, day=None):
    """Posting settings for this post (and records its arm). day: today's date text, for per-day tests."""
    if not exp or exp.get("status") != "running" or exp["level"] not in ("post", "day"):
        return {}
    i = exp["assign"].get(key)
    if i is None:
        if exp["level"] == "day":
            days = exp.setdefault("days", {})
            if day not in days:
                days[day] = len(days) % len(exp["arms"])
            i = days[day]
        else:
            i = _least_used(exp)
        exp["assign"][key] = i
    return {exp["factor"]: exp["arms"][i]["value"]}


def day_overrides(exp, day):
    """Posting hours for today while a per-day test runs (used before a post is picked)."""
    if not exp or exp.get("status") != "running" or exp["level"] != "day":
        return {}
    days = exp.setdefault("days", {})
    if day not in days:
        days[day] = len(days) % len(exp["arms"])
    return {exp["factor"]: exp["arms"][days[day]]["value"]}


def arm_of(exp, v):
    if exp["level"] == "observe":
        vals = [a["value"] for a in exp["arms"]]
        return vals.index(v.get("mode")) if v.get("mode") in vals else None
    if exp["level"] == "movie":
        return exp["assign"].get(v.get("movie"))
    return exp["assign"].get(post_key(v.get("movie"), v.get("mode"), v.get("part")))


def score(exp, videos, now=None):
    """Per arm: videos so far, mature ones, median views, average watch time and full-watch %."""
    now = now or time.time()
    since = float(exp.get("started_ts") or 0)
    arms = [{"videos": 0, "mature": 0, "views": [], "watch": [], "full": []} for _ in exp["arms"]]
    for v in videos:
        if not v.get("movie"):
            continue
        created = float(v.get("created") or 0)
        if exp["level"] == "observe" and created and created < since:
            continue                                         # only videos posted while the test ran
        i = arm_of(exp, v)
        if i is None:
            continue
        a = arms[i]
        a["videos"] += 1
        if created and now - created < MATURE_HOURS * 3600:
            continue
        a["mature"] += 1
        a["views"].append(int(v.get("views") or 0))
        if v.get("avg_watch") is not None:
            a["watch"].append(float(v["avg_watch"]))
        if v.get("full_watch") is not None:
            a["full"].append(float(v["full_watch"]))
    out = []
    for a in arms:
        out.append({"videos": a["videos"], "mature": a["mature"],
                    "median_views": statistics.median(a["views"]) if a["views"] else None,
                    "avg_watch": round(sum(a["watch"]) / len(a["watch"]), 1) if a["watch"] else None,
                    "full_watch": round(sum(a["full"]) / len(a["full"]), 1) if a["full"] else None})
    return out


def decide(exp, stats, now=None):
    """("running", None) | ("won", arm index) | ("no_difference", None) | ("timeout", None)."""
    now = now or time.time()
    days = (now - float(exp.get("started_ts") or now)) / 86400
    if min(s["mature"] for s in stats) < MIN_PER_ARM:
        return ("timeout", None) if days >= MAX_DAYS else ("running", None)
    order = sorted(range(len(stats)), key=lambda i: -(stats[i]["median_views"] or 0))
    best, rest = order[0], order[1:]
    bv = stats[best]["median_views"] or 0
    ctl = next((i for i, a in enumerate(exp["arms"]) if a.get("control")), None)
    watch_ok = True
    if ctl is not None and ctl != best and stats[ctl]["avg_watch"] and stats[best]["avg_watch"] is not None:
        watch_ok = stats[best]["avg_watch"] >= WATCH_FLOOR * stats[ctl]["avg_watch"]
    if bv > 0 and watch_ok and all(bv >= WIN_MARGIN * (stats[i]["median_views"] or 0) for i in rest):
        return "won", best
    if min(s["mature"] for s in stats) >= 2 * MIN_PER_ARM or days >= MAX_DAYS:
        return "no_difference", None
    return "running", None


def progress(exp, stats):
    """0..100: how far the experiment is towards a verdict (for the app's progress bar)."""
    need = MIN_PER_ARM * len(stats)
    return int(100 * sum(min(s["mature"], MIN_PER_ARM) for s in stats) / need) if need else 0


def step(state, settings, videos, proposal=None, enabled=True, now=None):
    """Runs the experiment loop once (every Insights look). state: {"current", "history"} kept by the laptop.
    Returns (state, settings changes to save, one-line note or None)."""
    state = dict(state or {})
    state.setdefault("history", [])
    cur, changes, note = state.get("current"), {}, None
    if cur and cur.get("status") == "running" and not enabled:      # switched off in the app: stop it
        cur.update(status="stopped", ended=now_iso())
        state["history"] = (state["history"] + [cur])[-HISTORY:]
        state["current"] = cur = None
        note = "Experiment stopped (Experiments switched off)"
    if cur and cur.get("status") == "running":
        stats = score(cur, videos, now)
        cur["stats"], cur["progress"] = stats, progress(cur, stats)
        verdict, win = decide(cur, stats, now)
        if verdict != "running":
            cur.update(status=verdict, ended=now_iso())
            if verdict == "won":
                arm = cur["arms"][win]
                cur["winner"] = arm["name"]
                if not arm.get("control") and get(settings, cur["factor"]) != arm["value"]:
                    changes = {cur["factor"]: arm["value"]}
                note = f"{cur['label']}: {arm['name']} ({arm['value']}) won"
            else:
                note = f"{cur['label']}: no clear winner, kept as it was"
            state["history"] = (state["history"] + [cur])[-HISTORY:]
            state["current"] = cur = None
    if enabled and not cur:
        after = apply_changes(_copy(settings), changes)
        for p in [proposal] + _untried(state["history"]):
            exp = new_experiment(p, after, now)
            if exp:
                state["current"] = exp
                break
    return state, changes, note


def stop(state):
    """The app's Stop button: ends the running experiment with no change."""
    state = dict(state or {})
    cur = state.get("current")
    if not cur or cur.get("status") != "running":
        return state, None
    cur.update(status="stopped", ended=now_iso())
    state["history"] = (state.get("history") or []) + [cur]
    state["history"], state["current"] = state["history"][-HISTORY:], None
    return state, cur["label"]


def brief(exp):
    """An experiment without its per-movie bookkeeping (for Claude and the app)."""
    if not exp:
        return None
    out = {k: exp.get(k) for k in ("id", "factor", "label", "level", "why", "started", "ended", "status", "winner",
                                   "progress")}
    stats = exp.get("stats") or [{}] * len(exp["arms"])
    out["arms"] = [dict(a, **{k: s.get(k) for k in ("videos", "mature", "median_views", "avg_watch", "full_watch")})
                   for a, s in zip(exp["arms"], stats)]
    out["need"] = MIN_PER_ARM
    return out


def public(state):
    state = state or {}
    return {"current": brief(state.get("current")), "history": [brief(h) for h in (state.get("history") or [])][-8:][::-1],
            "rules": {"min_per_arm": MIN_PER_ARM, "mature_hours": MATURE_HOURS, "win_margin": WIN_MARGIN,
                      "max_days": MAX_DAYS}}


def _copy(settings):
    import copy
    return copy.deepcopy(settings)


def _untried(history):
    """Default experiments not run yet (or not in the last 5), in order."""
    recent = {h.get("factor") for h in history[-5:]}
    return [p for p in DEFAULT_PLAN if p["factor"] not in recent]


def testing_keys(state):
    """Settings the running experiment owns: auto-tune leaves these alone so the test stays fair."""
    cur = (state or {}).get("current") or {}
    if cur.get("status") != "running":
        return set()
    return {"posting.modes"} if cur["factor"] == "mode_first" else \
        {"posting.active_from", "posting.active_to"} if cur["factor"] == "posting.hours" else {cur["factor"]}


# ---------------------------------------------------------------- holding back weak parts
HOLD_DAYS = 7
MAX_HOLDS = 3


def clean_holds(proposed, holds, movies, protected, now=None):
    """Claude's {"movie", "mode", "why"} list of parts to stop posting for now. Only movies that have numbers,
    never a protected mode, at most MAX_HOLDS at once, each for HOLD_DAYS. Returns the new holds list."""
    t = now or time.time()
    out = [h for h in (holds or []) if float(h.get("until") or 0) > t]
    for x in proposed or []:
        if not isinstance(x, dict):
            continue
        movie, mode = str(x.get("movie") or ""), str(x.get("mode") or "")
        m = (movies or {}).get(movie)
        if not m or mode not in MODES or mode in protected or (m.get("modes") or {}).get(mode) is None:
            continue
        if m.get("videos", 0) < 2 or any(h["movie"] == movie and h["mode"] == mode for h in out):
            continue
        if len(out) >= MAX_HOLDS:
            break
        out.append({"movie": movie, "mode": mode, "why": str(x.get("why") or "")[:200],
                    "at": now_iso(), "until": t + HOLD_DAYS * 86400})
    return out


def is_held(holds, movie, mode, now=None):
    t = now or time.time()
    return any(h["movie"] == movie and h["mode"] == mode and float(h.get("until") or 0) > t for h in holds or [])
