"""
Settings for the movie cutter and the TikTok poster.

The owner changes settings in the iPhone app, which saves them to config/settings.json.
This file reads that, fills in anything missing with the defaults below, and lets
environment variables (the old v1 names, e.g. MODES, FLIP_EVERY, COCKROACH_SIZE,
or POSTING_ENABLED for the "posting" section) override any single setting.
"""
import copy
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "config" / "settings.json"

# Used when settings.json is missing a key (or is missing / broken altogether).
DEFAULTS = {
    "modes": ["recap", "character", "hype"],   # see story.py; the older stretch / highlights / all still work
    "filter": "mystery",                 # film look on every part: mystery / warm / teal_orange / noir / none
    "filter_strength": 0.5,              # 0 = off ... 1 = strong (0.5: clearly graded, original still obvious)
    "character": "",                     # Character story + Hype edit: who to follow; blank = the main character
    "narrator_voice": "en-US-GuyNeural", # Microsoft neural voice (edge-tts), e.g. en-US-ChristopherNeural, en-GB-RyanNeural
    "narrator_speed": "+8%",
    "recap_part_seconds": 90,            # Movie recap / Character story: length of each narrated part
    "recap_parts": 0,                    # 0 = automatic (3 to 8 parts, by movie length)
    "music": True,                       # background music from assets/music (royalty-free tracks you add)
    "music_volume": 0.12,                # under the narration (hype montages play the song at full volume)
    "hype_count": 1,                     # Hype edits per movie
    "hype_montage_seconds": 15,
    "cut_on": "laptop",                  # laptop = the laptop cuts movies itself (no upload); github = GitHub cuts them
    "story_editor": "ffmpeg",            # ffmpeg = GitHub cuts and narrates the parts (default);
                                         # filmcraft = the laptop builds them in FilmCraft
    "story_model": "claude-haiku-5-5",   # reads the subtitles + plot and watches frames (secret ANTHROPIC_API_KEY);
                                         # or a free local model such as gemma3:4b
    "stretch_minutes": 12,
    "stretch_from": 0.20,
    "stretch_to": 0.75,
    "stretch_part_seconds": 75,
    "part_max_extra": 225,
    "highlight_seconds": 75,
    "highlight_count": 0,
    "part_seconds": 120,
    "flip": "mirror",
    "flip_every": "25-30",
    "auto_subtitles": True,
    "whisper_model": "small",
    "line": True,
    "line_opacity": 0.45,
    "line_thickness": 1,
    "cockroach": True,
    "cockroach_chance": 1,
    "cockroach_size": 60,
    "captions": True,
    "cut_style": "classic",              # classic = whole picture on a blurred background (default); zoom = zoomed band that follows faces
    "zoom_part_seconds": 210,            # zoom style: parts of about 3.5 minutes...
    "zoom_part_max_extra": 90,           # ...that may run up to this much longer to end at a pause (so 3-5 min)
    "caption_style": "goofy",            # goofy = jokes + a quote from the clip; plain = the older, calmer captions
    "paused": False,                     # True = laptop stops sending/receiving and GitHub doesn't start cutting
    "pause_on_battery": False,           # laptop waits while it's running on battery
    "upload_limit_mbps": 0,              # cap the laptop's upload speed (megabits/s); 0 = no limit
    "auto_update_laptop": True,          # the laptop helper fetches its newest code from GitHub by itself
    "previews": True,                    # small encrypted copies of each part, so the app can play them
    "preview_keep_movies": 3,            # previews are kept for this many most recent movies
    "wishlist_archive": True,            # look up wish-list titles on archive.org (public domain only); False = off
    "video_quality": 23,                 # x264 CRF: 18 = best/biggest ... 28 = smallest
    "delete_originals": "permanent",     # "permanent" = gone for good, "bin" = Google Drive's bin (30 days)
    "cleanup_parts_after_days": 0,       # delete finished parts older than this; 0 = never
    "posting": {
        "enabled": False,
        "privacy": "self_only",
        "when": "times",                 # times = at the post times below; as_ready = each part as soon as it's on the laptop
        "method": "draft",               # draft = sent to your TikTok inbox, you tap Post; direct = posted straight away (needs a private account until TikTok approves the app)
        "modes": ["recap", "character", "hype", "stretch", "highlights"],   # older parts still get posted
        "per_day": 3,
        "times": ["12:00", "18:00", "21:00"],
        "timezone": "Pacific/Auckland",
        "hashtags": "#movie #movieclips #fyp",
    },
}

OFF_WORDS = ("off", "no", "0", "false", "")


def env_name(key, prefix=""):
    """'flip_every' -> 'FLIP_EVERY'; ('enabled', 'posting') -> 'POSTING_ENABLED'."""
    return (f"{prefix}_{key}" if prefix else key).upper()


def _coerce(text, like):
    """Turns an environment variable's text into the same kind of value as `like`."""
    if isinstance(like, bool):
        return text.strip().lower() not in OFF_WORDS
    if isinstance(like, int):
        v = float(text)
        return int(v) if v.is_integer() else v      # e.g. cockroach_chance "0.5" stays a fraction
    if isinstance(like, float):
        return float(text)
    if isinstance(like, list):
        return [x.strip() for x in text.split(",") if x.strip()]
    return text


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None, env=None, text=None):
    """
    Settings as a dict: defaults <- settings.json <- environment variables.
    text: the settings.json content itself (the laptop fetches it from GitHub).
    """
    env = os.environ if env is None else env
    path = Path(path or env.get("SETTINGS_FILE") or SETTINGS_FILE)
    try:
        data = json.loads(text if text is not None else path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        data = {}
    except Exception as e:
        print(f"(settings file {path} couldn't be read, using defaults: {e})", flush=True)
        data = {}
    out = _merge(DEFAULTS, data)

    def override(section, defaults, prefix=""):
        for k, v in list(section.items()):
            if isinstance(v, dict):
                override(v, defaults.get(k, {}), k)
                continue
            name = env_name(k, prefix)
            if name in env:
                try:
                    section[k] = _coerce(env[name], defaults.get(k, v))
                except ValueError:
                    print(f"(ignoring {name}={env[name]!r}: not a valid value)", flush=True)
    override(out, DEFAULTS)
    return out


def _as_env_text(v):
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    return str(v)


def apply_to_env(settings=None, env=None):
    """
    Puts every setting into the environment under its v1 name (MODES, FLIP_EVERY, ...),
    without replacing variables that are already set. process.py reads them from there.
    """
    env = os.environ if env is None else env
    settings = load(env=env) if settings is None else settings

    def put(section, prefix=""):
        for k, v in section.items():
            if isinstance(v, dict):
                put(v, k)
            else:
                env.setdefault(env_name(k, prefix), _as_env_text(v))
    put(settings)
    return settings
