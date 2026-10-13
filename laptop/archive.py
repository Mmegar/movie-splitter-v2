"""
Wish list: finds PUBLIC-DOMAIN films on archive.org and downloads them (resuming if stopped).
Never uses any other source.
"""
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".webm", ".mpg", ".mpeg", ".ts", ".ogv"}
NOT_AVAILABLE = "Not public domain - add the file to your laptop folder instead"
ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_META = "https://archive.org/metadata/"
DEFAULT_PD_COLLECTIONS = ["feature_films", "film_noir", "scifi_horror", "silent_films", "comedy_films",
                          "classic_cartoons", "prelinger", "publicmovies212", "moviesandfilms_pd"]


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
