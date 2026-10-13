"""Turning a movie's file name into a tidy name (shared by the cutter and the laptop helper)."""
import re
from pathlib import Path

JUNK = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|uhd|hdr|10bit|bluray|blu ray|brrip|bdrip|webrip|web dl|web|"
    r"hdtv|dvdrip|hdrip|x264|x265|h264|h265|hevc|aac|ac3|dts|proper|repack|extended|remastered|"
    r"512kb|archive|ia)\b.*$",
    re.I,
)


def clean_name(filename):
    """'Nosferatu.1922.1080p.mkv' -> 'Nosferatu (1922)'"""
    stem = Path(filename).stem
    base = re.sub(r"[._]+", " ", stem)
    base = re.sub(r"[\[\]\(\)\{\}]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    name = None
    for m in re.finditer(r"\b(1[89]\d{2}|20\d{2})\b", base):
        if m.start() > 0:
            name = f"{base[:m.start()].strip(' -')} ({m.group(1)})"
            break
    if not name:
        name = JUNK.sub("", base).strip(" -") or stem
    return safe(name)


def safe(name):
    return re.sub(r'[\\/:*?"<>|%]', "", name).strip()
