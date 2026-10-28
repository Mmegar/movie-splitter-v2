"""What people are watching right now: the season (horror in October, Christmas movies in December...) and the
films and shows most read about on Wikipedia in the last few days. Insights hands this to Claude, which picks
the next movies to download from it together with the channel's own numbers. Titles only: nothing is downloaded."""
import json
import re
import urllib.parse
import urllib.request
from datetime import date, timedelta

UA = "MovieSplitter/1.0 (github.com/Mmegar/movie-splitter-v2)"

# (from mm-dd, to mm-dd, moment, genres people watch more of then)
SEASONS = [
    ("01-01", "01-31", "New Year, cold nights in", ["Thriller", "Mystery", "Crime"]),
    ("02-01", "02-14", "Valentine's Day", ["Romance", "Psychological thriller"]),
    ("03-01", "04-15", "Spring, awards season just ended", ["Drama", "Mystery"]),
    ("05-01", "08-31", "Summer blockbuster season", ["Action", "Adventure", "Sci-fi", "Disaster"]),
    ("06-01", "08-31", "Summer holidays (shark, camp and survival movies trend)", ["Survival", "Slasher", "Thriller"]),
    ("09-01", "09-30", "Back to school, autumn starts", ["Mystery", "Crime", "Thriller"]),
    ("09-20", "11-02", "Spooky season / Halloween", ["Horror", "Supernatural", "Slasher", "Mystery"]),
    ("11-03", "11-30", "True-crime season, long dark evenings", ["Crime", "Thriller", "Mystery"]),
    ("11-20", "12-31", "Christmas holidays", ["Holiday", "Family", "Fantasy", "Comedy"]),
]


def season(today=None):
    """The moments that are on today, with the genres people watch more of."""
    md = (today or date.today()).strftime("%m-%d")
    out = [{"moment": m, "genres": g} for a, b, m, g in SEASONS if a <= md <= b]
    d = today or date.today()
    if d.day == 13 and d.weekday() == 4:
        out.append({"moment": "Friday the 13th", "genres": ["Horror", "Slasher"]})
    return out


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


SCREEN = re.compile(r"\b(film|movie|television series|tv series|miniseries|web series|anime|k-drama|drama series)\b", re.I)
NOT_SCREEN = re.compile(r"\b(actor|actress|director|filmmaker|producer|singer|rapper|soundtrack|novel|video game|"
                        r"character|franchise|award|festival|company|studio|episode)\b", re.I)


def is_screen(description):
    """'2025 film by Ryan Coogler' -> True, 'American actor (born 1980)' -> False."""
    d = str(description or "")
    return bool(SCREEN.search(d)) and not NOT_SCREEN.search(d.split(" by ")[0])


def wikipedia_trending(days=3, limit=15, today=None, get=_get):
    """Films and TV shows with the most English Wikipedia page views over the last few days (a good stand-in
    for what people are watching and searching right now)."""
    views = {}
    end = (today or date.today()) - timedelta(days=1)          # yesterday is the newest full day
    for i in range(days):
        d = end - timedelta(days=i)
        try:
            items = get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/"
                        f"{d:%Y/%m/%d}")["items"][0]["articles"]
        except Exception:
            continue
        for a in items[:300]:
            t = a.get("article") or ""
            if ":" in t or t == "Main_Page":
                continue
            views[t] = views.get(t, 0) + int(a.get("views") or 0)
    if not views:
        return []
    top = sorted(views, key=views.get, reverse=True)[:400]
    desc = {}
    for i in range(0, len(top), 50):
        q = urllib.parse.urlencode({"action": "query", "prop": "description", "format": "json", "formatversion": 2,
                                    "titles": "|".join(top[i:i + 50])})
        try:
            pages = get(f"https://en.wikipedia.org/w/api.php?{q}")["query"]["pages"]
        except Exception:
            continue
        for p in pages:
            desc[p["title"].replace(" ", "_")] = p.get("description") or ""
    out = []
    for t in top:
        if is_screen(desc.get(t)):
            out.append({"title": t.replace("_", " "), "about": desc[t], "views": views[t]})
        if len(out) >= limit:
            break
    return out


def context(today=None, get=_get):
    """Everything Insights tells Claude about what's in demand right now."""
    today = today or date.today()
    try:
        trending = wikipedia_trending(today=today, get=get)
    except Exception:
        trending = []
    return {"date": today.isoformat(), "season": season(today), "trending": trending}
