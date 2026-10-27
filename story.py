"""
The storytelling modes, made from the movie's subtitles:

  recap      "Movie recap": a narrator tells the whole story over the matching scenes, in Part 1, 2, 3...
             RECAP_STYLE "fast" (default) copies the big recap channels: a plain third-person storyteller that
             never stops talking, in one steady voice, with short caption phrases and an even -14 LUFS mix.
             "chatty" talks to the viewer, jokes (Funny dial), pauses, and has a welcome intro and sign-off.
  character  "Character story": the same, but following one character's arc (CHARACTER, or the most
             important character when that's blank), staying faithful to what they actually do.
  hype       "Hype edit": the character gets disrespected or doubted, answers it in style, then a
             beat-synced montage (white flash cuts, three-panel diagonal mask slides, slow motion). The
             flashes are only in the Hype edit: narrated parts never flash.
             over music from assets/music.

A small AI model running on GitHub's computer (Ollama, STORY_MODEL, no API key or account) reads the
timed subtitles and plans the parts: which scenes, and what the narrator says. A small model can't
take a whole movie in at once, so it first writes short scene notes for each stretch of subtitles,
then plans the story from those notes. Set STORY_MODEL to a Claude model (and add the secret
ANTHROPIC_API_KEY) for better writing. The voice is Microsoft's free neural text-to-speech
(edge-tts); everything else is ffmpeg. By default the picture is zoomed in (a tall 4:5 picture on the
phone screen) and the crop follows the characters (tracking.py: whoever is talking, else the main face,
else a person's body, never the background); the "classic" cutting style shows the whole picture
instead. The film look (FILTER) goes on top. If the model can't be reached, the narrated modes are skipped (with
a notification saying why) and the Hype edit picks its scenes from loud, fast moments instead.

Music: assets/music/tracks.json lists free-to-use songs (downloaded by the workflow) with the credit
line their licence asks for, which goes into the caption. Songs you drop into assets/music are used too.
"""
import json
import math
import os
import random
import re
import subprocess
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

P = None   # the process module (bound by process.py, so these modes share its helpers and settings)


def bind(process_module):
    global P
    P = process_module
    load_settings()


OFF = ("off", "no", "0", "false", "none", "")
MODE_NAMES = {"recap": "Movie recap", "character": "Character story", "hype": "Hype edit"}
NEEDS_TRANSCRIPT = set(MODE_NAMES)

FILTER, FILTER_STRENGTH, CHARACTER, VOICE, SPEED = "vivid", 0.4, "", "en-US-GuyNeural", "+0%"
RECAP_PART_SECONDS, RECAP_PARTS, MUSIC, MUSIC_VOLUME = 180.0, 0, True, 0.12
HYPE_COUNT, HYPE_MONTAGE_SECONDS, MODEL = 1, 15.0, "claude-haiku-5-5"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def load_settings(env=None):
    """Reads these modes' settings from the environment (process.py puts settings.json there first)."""
    global FILTER, FILTER_STRENGTH, CHARACTER, VOICE, SPEED, RECAP_PART_SECONDS, RECAP_PARTS
    global MUSIC, MUSIC_VOLUME, HYPE_COUNT, HYPE_MONTAGE_SECONDS, MODEL
    env = os.environ if env is None else env
    FILTER = env.get("FILTER", "vivid").strip().lower()              # vivid / mystery / warm / teal_orange / noir / none
    FILTER_STRENGTH = float(env.get("FILTER_STRENGTH", "0.5"))       # 0 = off ... 1 = strong
    CHARACTER = env.get("CHARACTER", "").strip()                     # blank = the most important character
    VOICE = env.get("NARRATOR_VOICE", "en-US-GuyNeural")
    SPEED = env.get("NARRATOR_SPEED", "+0%")
    RECAP_PART_SECONDS = min(PART_MAX_SECONDS, max(PART_MIN_SECONDS, float(env.get("RECAP_PART_SECONDS", "180"))))
    RECAP_PARTS = int(float(env.get("RECAP_PARTS", "0")))            # 0 = automatic (at least 3)
    MUSIC = env.get("MUSIC", "on").strip().lower() not in OFF
    MUSIC_VOLUME = float(env.get("MUSIC_VOLUME", "0.12"))            # under the narration
    HYPE_COUNT = int(float(env.get("HYPE_COUNT", "1")))
    HYPE_MONTAGE_SECONDS = float(env.get("HYPE_MONTAGE_SECONDS", "15"))
    MODEL = env.get("STORY_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"
    global VOICE_ENGINE, INTENSITY, TEMPO, FUNNY, DIRECTION, SWEEP_SECONDS, LIGHT_SWEEP
    LIGHT_SWEEP = env.get("LIGHT_SWEEP", "off").strip().lower() not in OFF
    SWEEP_SECONDS = float(env.get("LIGHT_SWEEP_SECONDS", "3"))
    FUNNY = min(1.0, max(0.0, float(env.get("NARRATOR_FUNNY", "0.7"))))  # 0 straight ... 1 full comedy
    DIRECTION = env.get("SCRIPT_DIRECTION", "").strip()[:600]           # the owner's own notes for the writer
    global CAPTIONS_BY_WRITER, CAPTION_DIRECTION
    CAPTIONS_BY_WRITER = (env.get("CAPTIONS", "on").strip().lower() not in OFF
                          and env.get("CAPTION_WRITER", "claude").strip().lower() == "claude")
    CAPTION_DIRECTION = env.get("CAPTION_DIRECTION", "").strip()[:600]
    global THUMBNAIL_BADGE
    THUMBNAIL_BADGE = env.get("THUMBNAIL_BADGE", "PART {n}").strip()[:40] or "PART {n}"
    TEMPO = float(env.get("NARRATOR_TEMPO", "1.2"))
    VOICE_ENGINE = env.get("NARRATOR_ENGINE", "chatterbox").strip().lower()
    INTENSITY = float(env.get("NARRATOR_INTENSITY", "0.9"))   # Expressiveness: 0.5 calm ... 1 wild (every voice)
    global EDITOR, RECAP_STYLE
    EDITOR = env.get("STORY_EDITOR", "ffmpeg").strip().lower()       # ffmpeg (cut + narrate here) or filmcraft (laptop)
    RECAP_STYLE = "chatty" if env.get("RECAP_STYLE", "fast").strip().lower() == "chatty" else "fast"


EDITOR = "ffmpeg"
RECAP_STYLE = "fast"   # fast = the TikTok recap-channel style (storyteller, steady voice, short captions); chatty = talks to "you"
PACK_SUFFIX = ".editpack.zip"


MUSIC_DIR = Path(__file__).resolve().parent / "assets" / "music"
MUSIC_UNDER_LUFS = -32   # narrated parts: songs are evened out to this before mixing (the voice sits around -16)
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus"}
DUCK = 0.18          # movie sound under the narrator
WORDS_PER_SECOND = 2.6
JOKE_PAUSE = 0.8     # seconds of footage (no voice) after a joke, so it lands
PART_MIN_SECONDS, PART_MAX_SECONDS = 60.0, 300.0   # every narrated part runs 1 to 5 minutes
PART_OVERRUN = 1.2   # ...or a bit over 5 to end on a cliffhanger; only a part past 5 min x this is split
FUNNY, DIRECTION = 0.7, ""
CAPTIONS_BY_WRITER, CAPTION_DIRECTION = True, ""
THUMBNAIL_BADGE = "PART {n}"   # the thumbnail's badge text; {n} = the part number
FPS = 30
STORY_BAND = (1080, 1350)               # zoomed: the picture on the 1080x1920 screen (4:5, followed by tracking)
STORY_BAND_TOP = (1920 - STORY_BAND[1]) // 2
ZOOM_TEXT_BOTTOM = 1300                 # zoomed: captions sit on the lower part of the picture, clear of TikTok's buttons

_override = {"character": None}


class Skip(Exception):
    """This mode can't be made for this movie (the message says why)."""


def set_character(name):
    """The character picked for this movie in the app (overrides CHARACTER)."""
    _override["character"] = (name or "").strip() or None


def character():
    return _override["character"] or CHARACTER


# ---------------------------------------------------------------- the film look
def grade(name=None, strength=None):
    """
    A visible but gentle colour grade, so the clip clearly has a look while the original is still
    obvious. Returns filter text ending in "," (or "" when off), to go right after the black-bar crop.
    """
    name = (name or FILTER).lower()
    s = max(0.0, min(1.0, FILTER_STRENGTH if strength is None else float(strength)))
    if name in OFF or s <= 0:
        return ""
    if name == "vivid":    # bright, crisp and colourful, but not oversaturated (white specks are added per part)
        parts = [f"eq=contrast={1 + 0.15 * s:.3f}:saturation={1 + 0.45 * s:.3f}:brightness={0.02 * s:.3f}",
                 f"unsharp=5:5:{0.8 * s:.2f}"]
        return ",".join(parts) + ","
    if name == "noir":
        parts = [f"eq=contrast={1 + 0.15 * s:.3f}:saturation={1 - 0.75 * s:.3f}:gamma={1 - 0.04 * s:.3f}"]
    elif name == "warm":
        parts = [f"colorbalance=rs={0.06 * s:.3f}:gs={0.02 * s:.3f}:bs={-0.05 * s:.3f}:rm={0.04 * s:.3f}:bm={-0.03 * s:.3f}",
                 f"eq=contrast={1 + 0.05 * s:.3f}:saturation={1 + 0.08 * s:.3f}"]
    elif name == "teal_orange":
        parts = [f"colorbalance=rs={-0.07 * s:.3f}:bs={0.08 * s:.3f}:rh={0.07 * s:.3f}:gh={0.02 * s:.3f}:bh={-0.07 * s:.3f}",
                 f"eq=contrast={1 + 0.07 * s:.3f}:saturation={1 + 0.06 * s:.3f}"]
    else:   # mystery: cool shadows, slightly drained colour, a little more contrast
        parts = [f"colorbalance=rs={-0.05 * s:.3f}:gs={0.01 * s:.3f}:bs={0.07 * s:.3f}:rm={-0.02 * s:.3f}:bm={0.03 * s:.3f}"
                 f":rh={0.02 * s:.3f}:bh={-0.01 * s:.3f}",
                 f"eq=contrast={1 + 0.09 * s:.3f}:saturation={1 - 0.2 * s:.3f}:gamma={1 - 0.03 * s:.3f}"]
    parts.append(f"vignette=angle={0.25 + 0.35 * s:.3f}")
    if s >= 0.2:
        parts.append(f"noise=alls={max(1, round(5 * s))}:allf=t")    # a touch of film grain
    return ",".join(parts) + ","


# ---------------------------------------------------------------- planning (Claude reads the subtitles)
def transcript(cues):
    """The subtitles as '[seconds] text' lines, joining lines that follow on closely."""
    lines, cur, start = [], "", None
    for a, b, t in cues:
        t = re.sub(r"\s+", " ", str(t)).strip()
        if not t:
            continue
        if cur and (a - start > 12 or len(cur) > 160):
            lines.append(f"[{int(start)}] {cur}")
            cur = ""
        if not cur:
            start = a
        cur = f"{cur} {t}".strip()
    if cur:
        lines.append(f"[{int(start)}] {cur}")
    return "\n".join(lines)


def parse_json(text):
    """The JSON object in Claude's answer (ignores any ```json fences or words around it)."""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON in the answer")
    return json.loads(text[a:b + 1])


FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")


def claude_app():
    """The Claude Code program on this computer (the one the Claude app installs), or None. Using it runs
    on your Claude plan instead of a paid API key."""
    import shutil
    home = Path.home()
    windows = os.name == "nt"
    for p in [os.environ.get("CLAUDE_CLI"), home / ".local" / "bin" / ("claude.exe" if windows else "claude"),
              Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd", shutil.which("claude.exe"),
              shutil.which("claude.cmd"), None if windows else shutil.which("claude")]:
        if p and Path(p).is_file():
            return str(p)
    return None


def using_claude():
    return MODEL.startswith("claude") and bool(claude_app() or os.environ.get("ANTHROPIC_API_KEY"))


CLI_FRAMES = 24       # stills shown to Claude through the app (each one is a file it opens)


def ask_claude_app(system, prompt):
    """One request through the Claude app's command line (your Claude plan, no API key), with some frames
    from the movie as image files it can look at. Returns the JSON object it answers with."""
    import base64
    import tempfile
    with tempfile.TemporaryDirectory(prefix="frames-") as tmp:
        shots = frames()
        if len(shots) > CLI_FRAMES:
            shots = [shots[round(i * (len(shots) - 1) / (CLI_FRAMES - 1))] for i in range(CLI_FRAMES)]
        listing = []
        for t, b64 in shots:
            f = Path(tmp) / f"frame_{t:05d}s.jpg"
            f.write_bytes(base64.b64decode(b64))
            listing.append(f"{f} (second {t})")
        look = ("Before writing, open these frames from the movie (use the Read tool on each) to see who is on "
                "screen and which moments look most dramatic:\n" + "\n".join(listing) + "\n\n") if listing else ""
        text = f"{system}\n\n{look}{prompt}\n\nReply with the JSON only, no other text."
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}   # so it uses your Claude plan
        r = subprocess.run([claude_app(), "-p", "--model", MODEL, "--output-format", "text", "--allowedTools", "Read",
                            "--add-dir", tmp], input=text, capture_output=True, text=True, encoding="utf-8",
                           timeout=2400, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"Claude app: {(r.stderr or r.stdout)[-300:]}")
        return parse_json(r.stdout)


MAX_FRAMES = 100      # frames Claude "watches" per movie (about one every 30 s of a 50-minute show, 70 s of a 2-hour film)
_FRAMES = {"src": None, "list": None}


def set_source(src):
    """The movie file being cut (so Claude can watch frames of it)."""
    _FRAMES.update(src=src, list=None)


def frames():
    """[(second, base64 JPEG)]: small stills spread evenly through the movie, made once per movie."""
    import base64
    if _FRAMES["list"] is not None or not _FRAMES["src"] or P is None:
        return _FRAMES["list"] or []
    src = Path(_FRAMES["src"])
    out = []
    try:
        total = P.duration(src)
        lo, hi = max(0.0, WINDOW[0]), min(total, WINDOW[1])         # never the opening titles or end credits
        step = max(30.0, (hi - lo) / MAX_FRAMES)
        t = lo + step / 2
        total = hi
        tmp = Path("frame.jpg")
        print(f"Grabbing {int(total / step)} frames for Claude to watch...", flush=True)
        while t < total:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.1f}", "-i", str(src.resolve()), "-frames:v", "1",
                            "-vf", f"{P.CROP['filter']}scale=512:-2", "-q:v", "5", str(tmp)], check=True)
            out.append((int(t), base64.b64encode(tmp.read_bytes()).decode("ascii")))
            t += step
        tmp.unlink(missing_ok=True)
    except Exception as e:
        print(f"(no frames to watch: {e})", flush=True)
    _FRAMES["list"] = out
    return out


def ask_claude(system, prompt):
    """One request to Claude, with frames from the movie so it sees who's on screen; returns its JSON."""
    import anthropic
    client = anthropic.Anthropic()
    extra = {}
    content = []
    shots = frames()
    if shots:
        content.append({"type": "text", "text": "Frames from the movie, each labelled with its second. Use them to see "
                        "who is on screen and which moments look most dramatic, so the footage you pick matches the words."})
        for t, b64 in shots:
            content.append({"type": "text", "text": f"[frame at {t}s]"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": prompt})
    kw = dict(model=MODEL, max_tokens=32000, system=system, output_config={"effort": "medium"},
              messages=[{"role": "user", "content": content}])
    if MODEL in FALLBACK_MODELS:    # if a safety check declines, the server retries on a fallback model
        kw["betas"] = ["server-side-fallback-2026-07-01"]
        extra = {"fallbacks": "default"}
    with client.beta.messages.stream(extra_body=extra, **kw) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise Skip("Claude declined to plan this movie.")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return parse_json(text)


def ask_local(system, prompt, schema, num_ctx=16384, max_tokens=6000):
    """One request to the small model on this computer (Ollama), its answer held to the JSON schema."""
    body = {"model": MODEL, "stream": False, "format": schema,
            "options": {"num_ctx": num_ctx, "num_predict": max_tokens, "temperature": 0.5},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        data = json.load(r)
    return parse_json(data["message"]["content"])


def local_ready():
    """Is the story model running on this computer (the workflow starts it)?"""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as r:
            names = [m.get("name", "") for m in json.load(r).get("models", [])]
    except Exception:
        return False
    return any(n == MODEL or n == f"{MODEL}:latest" for n in names)


def ask(system, prompt, schema):
    if using_claude() and claude_app():
        print(f"Asking Claude ({MODEL}) through the Claude app to plan the story...", flush=True)
        try:
            return ask_claude_app(system, prompt)
        except Exception as e:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise
            print(f"(the Claude app didn't answer, using the API key instead: {e})", flush=True)
    if using_claude():
        print(f"Asking Claude ({MODEL}) to plan the story...", flush=True)
        return ask_claude(system, prompt)
    print(f"Asking the story model ({MODEL}) to plan the story...", flush=True)
    return ask_local(system, prompt, schema)


SYSTEM = (
    "You are the writer for a TikTok movie-recap channel. You plan short vertical videos from a movie's timed "
    "subtitles (or scene notes made from them). You only describe what they support: never invent events, names "
    "or endings. When something is unclear, stay vague rather than guess. Mystery and thriller stories are the "
    "channel's speciality: set up the question early, point out the clues and suspects as they appear, keep the "
    "reveal back until the last part, and end every other part on a cliffhanger. The same storytelling works for "
    "any genre. You answer with JSON only."
)

NOTES_SCHEMA = {"type": "object", "required": ["notes"], "properties": {"notes": {"type": "array", "items": {
    "type": "object", "required": ["at", "note"],
    "properties": {"at": {"type": "number"}, "note": {"type": "string"}}}}}}
BEATS = {"type": "array", "items": {"type": "object", "required": ["say", "at"], "properties": {
    "say": {"type": "string"}, "at": {"type": "number"}, "until": {"type": "number"}, "joke": {"type": "boolean"},
    "mood": {"type": "string", "enum": ["calm", "curious", "tense", "funny", "shocked", "sad", "hype", "whisper"]}}}}
STORY_SCHEMA = {"type": "object", "required": ["character", "parts"], "properties": {
    "character": {"type": "string"},
    "cast": {"type": "array", "items": {"type": "string"}},
    "intro": BEATS,
    "outro": BEATS,
    "parts": {"type": "array", "items": {"type": "object", "required": ["title", "beats"], "properties": {
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "beats": BEATS}}}}}
SPAN = {"type": "array", "items": {"type": "number"}}
HYPE_SCHEMA = {"type": "object", "required": ["character", "edits"], "properties": {
    "character": {"type": "string"},
    "cast": {"type": "array", "items": {"type": "string"}},
    "edits": {"type": "array", "items": {"type": "object", "required": ["title", "setup", "payoff", "montage"],
                                         "properties": {"title": {"type": "string"}, "setup": SPAN, "payoff": SPAN,
                                                        "montage": {"type": "array", "items": SPAN}}}}}}

_NOTES = {}


def blocks_of(cues, chunk_chars=9000):
    """The subtitles in stretches small enough for a small model to read in one go."""
    out, block = [], []
    for ln in transcript(cues).split("\n"):
        block.append(ln)
        if sum(len(x) for x in block) > chunk_chars:
            out.append(block)
            block = []
    if block and block != [""]:
        out.append(block)
    return out


_PLOTS = {}
WIKI = "https://en.wikipedia.org/w/api.php"


def _wiki(params):
    url = WIKI + "?" + urllib.parse.urlencode(dict(params, format="json", formatversion=2))
    req = urllib.request.Request(url, headers={"User-Agent": "movie-splitter (github.com/Mmegar/movie-splitter-v2)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def plot_section(extract):
    """The Plot / Synopsis / Premise section of a Wikipedia article's plain text, or None."""
    m = re.search(r"\n==+ ?(Plot|Synopsis|Plot summary|Premise|Story) ?==+\n(.*?)(?=\n==[^=]|\Z)", extract, re.S)
    return re.sub(r"\n{2,}", "\n", m.group(2)).strip() if m else None


def movie_plot(name):
    """What actually happens in the movie, from its Wikipedia article ('Knives Out (2019)' -> the Plot
    section), so the writer knows the story instead of guessing it from dialogue. None if not found."""
    if name in _PLOTS:
        return _PLOTS[name]
    m = re.match(r"(.*?)\s*\((\d{4})\)\s*$", name)
    title, year = (m.group(1), m.group(2)) if m else (name, "")
    best = None
    try:
        seen = []
        for q in ([f"{title} {year} film", f"{title} film"] if year else []) + [title]:
            for r in _wiki({"action": "query", "list": "search", "srsearch": q, "srlimit": 5})["query"]["search"]:
                if r["title"] not in seen:
                    seen.append(r["title"])
        for t in seen[:8]:
            try:
                pages = _wiki({"action": "query", "prop": "extracts", "explaintext": 1, "titles": t,
                               "redirects": 1})["query"]["pages"]
            except Exception as e:
                print(f"(couldn't read '{t}' on Wikipedia: {e})", flush=True)
                continue
            text = pages[0].get("extract") or ""
            plot = plot_section(text)
            if not plot or len(plot) < 300:
                continue
            head = text[:600].lower()
            score = ((2 if year and year in head else 0) + (1 if "film" in head or "series" in head else 0)
                     + (1 if title.lower() in t.lower() else 0))
            if best is None or score > best[0]:
                best = (score, t, plot)
                _ARTICLES[name] = text
            if score >= 4:
                break
    except Exception as e:
        print(f"(couldn't look up the plot on Wikipedia: {e})", flush=True)
    if best and best[0] >= 2:
        print(f"Plot found on Wikipedia: '{best[1]}' ({len(best[2])} characters)", flush=True)
        _PLOTS[name] = best[2][:12000]
    else:
        print("(no Wikipedia plot found; the writer works from the dialogue only)", flush=True)
        _PLOTS[name] = None
        _ARTICLES.pop(name, None)
    return _PLOTS[name]


_ARTICLES = {}      # the Wikipedia article found by movie_plot, for the cast list
_PLAN_CAST = {}     # the cast the story writer named, used when Wikipedia has no cast list


def remember_cast(name, data):
    cast = [str(c).strip() for c in (data.get("cast") or []) if str(c).strip()] if isinstance(data, dict) else []
    if cast:
        _PLAN_CAST[name] = cast[:3]
    return data


def movie_cast(name, n=3):
    """The first few actors in the movie's Wikipedia Cast section ('Daniel Craig as Benoit Blanc' -> 'Daniel Craig')."""
    movie_plot(name)
    m = re.search(r"\n==+ ?(Cast|Starring|Cast and characters|Voice cast) ?==+\n(.*?)(?=\n==[^=]|\Z)",
                  _ARTICLES.get(name) or "", re.S)
    out = []
    for line in (m.group(2) if m else "").split("\n"):
        actor = re.split(r"\s+as\s+|\s*[,:(–—-]\s*", line.strip(), maxsplit=1)[0].strip()
        if 2 <= len(actor) <= 40 and len(actor.split()) <= 4 and actor[0].isupper() and actor not in out:
            out.append(actor)
        if len(out) >= n:
            break
    return out or _PLAN_CAST.get(name, [])[:n]


def _tag(text):
    """'Ana de Armas' -> '#AnaDeArmas' (each word capitalised, so it reads in the hashtag)."""
    words = re.split(r"[\s\-]+", text)
    return "#" + re.sub(r"[^0-9A-Za-z]", "", "".join(w[:1].upper() + w[1:] for w in words))


def movie_hashtags(name):
    """At most 5 hashtags that hit search: the movie's title, its top cast, then #movie / #fyp."""
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", name)
    tags = [_tag(title)] + [_tag(a) for a in movie_cast(name)] + ["#movie", "#fyp"]
    out = []
    for t in tags:
        if len(t) > 2 and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return " ".join(out[:5])


def plot_text(name):
    """The plot summary as the first thing the writer reads (or "" when there isn't one)."""
    plot = movie_plot(name)
    return ("Plot summary (from Wikipedia). This is what really happens: narrate from it, and use the notes "
            f"or subtitles only to find the second each event is on screen:\n{plot}\n\n") if plot else ""


def scene_notes(name, cues):
    """Short '[second] what happens' notes for the whole movie, one stretch of subtitles at a time.
    Made once per movie and shared by all the modes."""
    if name in _NOTES:
        return _NOTES[name]
    blocks = blocks_of(cues)
    notes = []
    for k, b in enumerate(blocks, 1):
        P.checkpoint()
        print(f"  scene notes {k} of {len(blocks)}...", flush=True)
        prompt = (f"Movie: {name}\nHere is one stretch of its subtitles ([second] line). Write 5 to 10 scene notes "
                  "in story order. Each note is one sentence saying who does or learns what (clues, suspects, "
                  "accusations, insults, threats, reveals, fights, cool moments), with the second it happens. Use "
                  "character names when the dialogue gives them.\n\n" + plot_text(name)
                  + "Subtitles:\n" + "\n".join(b))
        try:
            got = ask_local(SYSTEM, prompt, NOTES_SCHEMA, max_tokens=1200).get("notes") or []
        except Exception as e:
            print(f"  (no notes for this stretch: {e})", flush=True)
            continue
        for n in got:
            try:
                notes.append((float(n["at"]), re.sub(r"\s+", " ", str(n["note"])).strip()))
            except (KeyError, TypeError, ValueError):
                pass
    _NOTES[name] = sorted(notes)
    return _NOTES[name]


def source(name, cues):
    """What the writer reads: the full subtitles for Claude, scene notes for the small local model."""
    if using_claude():
        return plot_text(name) + "Subtitles ([second] line):\n" + transcript(cues)
    notes = scene_notes(name, cues)
    if not notes:
        raise Skip("The story model couldn't make sense of the dialogue.")
    return plot_text(name) + "Scene notes ([second] what happens):\n" + "\n".join(f"[{int(a)}] {t}" for a, t in notes)


MIN_STORY_PARTS = 5
MAX_STORY_PARTS = 8


def part_count(total):
    """The fewest parts to aim for: at least 5, more for a long movie (the writer may use more still)."""
    return min(MAX_STORY_PARTS, max(MIN_STORY_PARTS, RECAP_PARTS or round(total / 900)))


def fast():
    """True for the recap-channel style (the default): see voice_rules and render_segment."""
    return RECAP_STYLE == "fast"


def fast_voice_rules():
    """The recap-channel storyteller: a fast, plain, third-person retelling that never stops moving."""
    rules = """Voice: a fast recap-channel storyteller, the kind with millions of views on TikTok.
- Third person, past tense, plain and simple English a 12-year-old can follow: "The boy suddenly realized the man
  standing in front of him was actually an alien. Terrified, he turned around and ran."
- Call characters by who they are ("the boy", "the alien", "the woman", "the principal", "the rich man") and use a
  real name only for the two or three main characters, once the viewer knows who they are.
- Pure story: what happens, what they do, why, and what it leads to. No "you", no talking to the viewer or the
  characters, no opinions, no jokes, no welcome, no "let's get into it".
- Non-stop: every sentence moves the story forward. Link events with "but", "just then", "the next day",
  "at that moment", "suddenly", "only to find that", "to make things worse", so it flows like one breathless story.
- Explain the clever or strange details the way a friend retelling it would ("he deliberately tilted the glass to
  see if the man was thirsty, because infected people can't resist water").
- Short sentences, mostly 8 to 16 words, one event each.
- One continuous story across all the parts: each part picks up exactly where the last stopped, never recaps.
- Every fact stays true to the movie."""
    if DIRECTION:
        rules += (f"\n- The channel owner's notes (follow them where they fit this storyteller voice; skip anything that "
                  f"would add jokes or talk to the viewer): {DIRECTION}")
    return rules


def voice_rules():
    """How the narrator talks: personal ("you"), punchy, and as funny as the Funny dial says."""
    if fast():
        return fast_voice_rules()
    if FUNNY < 0.25:
        fun = "Mostly serious and gripping; at most one light joke per part."
    elif FUNNY < 0.6:
        fun = "Dramatic with a sense of humour: a joke or a sarcastic aside every few beats."
    elif FUNNY < 0.85:
        fun = ("Funny first: a joke, roast or sarcastic aside in most beats (about the characters' choices, the "
               "danger, the obvious red flags), like a friend who can't help commenting.")
    else:
        fun = ("Full comedy: almost every beat has a punchline, roast or absurd comparison, delivered deadpan, but the "
               "story still moves forward every beat.")
    rules = f"""Voice: a recap narrator talking straight to the viewer, like a friend watching the movie with them.
- Personal and connected, never a detached third-person summary. You are in the story with the viewer:
  - Talk to the viewer with "you" and "we" ("you just know this is a bad idea", "and we're not ready for what's
    behind that door"), and put them in the scene ("you're standing in that hallway with him").
  - Talk to the characters directly now and then ("Sir. Put the knife down." "Girl, don't open that door.").
  - Give the main characters warm or teasing nicknames and keep using them ("our boy", "my guy", "Detective
    Sunglasses", "the world's worst roommate"); call the lead "our" or "my" so the viewer is on their side.
  - React like you're watching it live with a friend ("and I'm already stressed", "no, because why would he...").
- Punchy: short sentences, mostly under 15 words. Cut filler. Start lines strong. One idea per beat.
- {fun}
- Jokes land: end a joke beat on its punchline (and mark it "joke": true); the next beat moves the story on.
- Humour is dry and deadpan: understatement, flat observations, comparisons to everyday real life (rent, group
  chats, bosses, Monday mornings). If the movie is based on a manhwa, manga, comic, book or game, you may nod to
  the source ("manhwa readers already know..."), but never spoil what the movie hasn't shown yet.
- One continuous story, not separate episodes: every part remembers the earlier parts. Use callbacks and running
  gags ("remember that suspicious umbrella from Part 2?"), keep nicknames you gave characters, and let jokes build
  on earlier ones. Never re-introduce a character or setting the viewer already met; don't recap, just reference.
- Every fact stays true to the movie. Present tense, plain spoken English."""
    if DIRECTION:
        rules += f"\n- The channel owner's direction for this script (follow it unless it breaks the rules above): {DIRECTION}"
    return rules


def caption_field():
    return '\n             "caption": "<the TikTok caption for this part>",' if CAPTIONS_BY_WRITER else ""


def caption_rules():
    """The caption each part gets on TikTok, written with the story (the part number and hashtags are added after)."""
    if not CAPTIONS_BY_WRITER:
        return ""
    rules = """Each part also gets a "caption" for TikTok: one or two short lines that make people stop and watch this
part, about what happens in it, without spoiling its cliffhanger or the ending. Talk to the viewer, in the same
voice as the narration; one or two emoji are fine. No hashtags and no part numbers (they are added after).
"""
    if CAPTION_DIRECTION:
        rules += f"The channel owner's direction for captions: {CAPTION_DIRECTION}\n"
    return rules + "\n"


CHATTY_FLOW = """Flow, which matters most:
- The narrated intro ("intro", played at the very start of Part 1): two to four beats that welcome the viewer and
  hook them for the whole series: what they're in for and why they should care (the central question, the
  stakes, the wildest thing that's coming), e.g. "Okay, you need to see this one. One locked room. One body. And
  six people who all swear they were asleep. Let's get into it." Its footage is the most gripping moments of the
  story (it may come from later in the movie, but never shows or tells the ending or a twist). Part 1's own beats
  then start the story from the beginning ("But it all starts three days earlier...").
- The narrated outro ("outro", played at the very end of the last part, after the ending): two to four beats
  that wrap the movie up: what it all meant, a last callback or joke, your verdict, and a sign-off to the viewer
  (ask what they'd have done, or tell them to follow for the next recap). Its footage is the movie's final
  scenes and closing shots of the main characters, from before the end credits start.
- Every part except the last ends on a real cliffhanger: stop right before a reveal or a decision, and end with a
  line that makes people need the next part (a question, a threat, "and that's when..."). Never end a part on a
  calm moment or mid-thought.
- Each new part picks up exactly where the last one stopped. Never recap or repeat what earlier parts said.
- Smooth footage: inside a part, footage moves forward through the movie in order. Consecutive beats use the
  same scene or the next moments of it; never jump back and forth between scenes or back to footage already used.
- Every change of scene is led by the narration, never cut first and explained after: the beat whose footage is
  the new scene starts with the transition itself ("Meanwhile, our boy heads to the station...", "Cut to the
  mansion, where...", "Three days later...", "Back at the house..."), so the words and the cut arrive together.

"""

FAST_FLOW = """Flow, which matters most:
- No welcome and no intro: "intro" is empty. Part 1's first line drops the viewer straight into the most gripping
  situation of the opening, mid-action ("While fixing his principal's broken toy cat, the boy found a hidden
  camera inside."), and the story runs on from there.
- "outro": one or two beats that finish the story with its final outcome. No verdict, no sign-off, no "follow".
- Every part except the last ends on a cliffhanger, cut off at the most tense moment: right before a reveal, a
  decision or a danger hits ("Just then, a mysterious person appeared and gave the boy a hint."). Never end a part
  on a calm moment.
- Each new part picks up exactly where the last one stopped. Never recap or repeat what earlier parts said.
- Smooth footage: inside a part, footage moves forward through the movie in order. Consecutive beats use the
  same scene or the next moments of it; never jump back and forth between scenes or back to footage already used.
- Scene changes are led by the narration ("The next day, the boy went to the clinic..."), so words and cut arrive
  together.
- Keep talking: the narration covers the whole part with no gaps. Don't use original-moment beats (empty "say")
  and never mark a "joke".

"""


def beat_extras():
    if fast():
        return ("Keep the delivery steady and urgent like a storyteller: tense or curious for most lines, shocked on "
                "a twist, whisper a creepy detail.\n")
    return ("""Vary it the way a real
storyteller does: build tension before a reveal, go shocked on the twist, funny on the jokes, whisper the creepy
bits, hype on the big win; write the line to match (short words and a "?" or "!" where the delivery needs one).
When a beat's "say" ends on a joke or punchline, add
"joke": true and the footage keeps playing for a moment with no voice, so the joke lands. One to three times per part, use an original-moment beat
instead: "say" is empty, and "at" and "until" mark a 3 to 8 second stretch where a key line is spoken, played
with its own sound. """)


def beats_shape():
    if fast():
        return ('[{"say": "<narration>", "at": <second>, "mood": "tense"},\n'
                '                       {"say": "<the next event>", "at": <second>, "mood": "curious"}]')
    return ('[{"say": "<narration>", "at": <second>, "mood": "tense"},\n'
            '                       {"say": "<a line ending on a punchline>", "at": <second>, "joke": true, "mood": "funny"},\n'
            '                       {"say": "", "at": <start second>, "until": <end second>}]')


def flow_rules():
    return FAST_FLOW if fast() else CHATTY_FLOW.format()


def story_prompt(name, total, text, mode, who):
    n = part_count(total)
    words, least, most = (part_words(RECAP_PART_SECONDS), part_words(PART_MIN_SECONDS * 1.25),
                          part_words(PART_MAX_SECONDS))
    focus = ("Tell the whole movie's story from start to finish." if mode == "recap" else
             f"Follow {'the character ' + who if who else 'the single most important character (name them in the character field)'} "
             "only: use scenes they are in or that are directly about them, and tell their arc faithfully (what they "
             "want, what they do, how they change). Mention other plot only as brief context, and don't drift into "
             "other characters' storylines.")
    return f"""Movie: {name}
Length: {int(total)} seconds

{focus}

Make {n} to {MAX_STORY_PARTS} parts (at least {MIN_STORY_PARTS}, never more than {MAX_STORY_PARTS}), each about {int(RECAP_PART_SECONDS)} seconds when read
aloud (about {words} words of narration per part), in story order. Every part must run at least 1 minute and up to 5
minutes: never fewer than {least} words of narration in a part, and at most about {most}. A part may run a little
over that only to end on a cliffhanger or to wrap up what the part is about; never cut a part off mid-moment. Use more parts (up to {MAX_STORY_PARTS}) rather
than squeezing or skipping important story.

Footage: never use the movie's own opening logos, title cards or credits, or its end credits. Only use seconds
from {int(WINDOW[0])} to {int(WINDOW[1])}, and skip any moment there that still has credits or a title on screen.

{voice_rules()}

{flow_rules()}Each part is a list of beats. A narration beat has "say" (one or two short sentences) and "at" (the second in the movie
where matching footage starts); that footage plays, with its own sound turned down, while the sentence is read,
so pick a moment that shows what the sentence says. Give every narration beat a "mood" for how it is read aloud:
calm, curious, tense, funny, shocked, sad, hype or whisper (a secret or a creepy detail). {beat_extras()}Only use seconds that appear below.

{caption_rules()}Answer with this JSON shape:
{{"character": "<who it follows; empty for a whole-movie recap>",
  "cast": ["<the real names of the movie's top 3 billed actors>"],
  "intro": [{{"say": "<narration>", "at": <second>}}],
  "outro": [{{"say": "<narration>", "at": <second>}}],
  "parts": [{{"title": "<3 to 6 words, for the thumbnail>",{caption_field()}
             "beats": {beats_shape()}}}]}}

{text}
"""


def hype_prompt(name, total, text, who):
    return f"""Movie: {name}
Length: {int(total)} seconds

Plan {HYPE_COUNT} "badass edit"{'s' if HYPE_COUNT != 1 else ''} about {'the character ' + who if who else 'the most iconic character (name them)'}.
Each edit has three pieces:
1. setup: 10 to 25 seconds where the character is insulted, doubted, threatened or underestimated (for a
   detective: mocked or dismissed before the case is cracked).
2. payoff: 10 to 30 seconds, later in the movie, where they answer it in style (a cold comeback, a reveal,
   outsmarting someone, a fight won).
3. montage: 8 to 14 short moments (each 1 to 4 seconds, anywhere in the movie) of the character looking
   their coolest, for a fast music edit.
Skip the intro and end credits: only use seconds from {int(WINDOW[0])} to {int(WINDOW[1])}. The title is their name or nickname, 1 to 3 words, for big on-screen text.

Answer with this JSON shape:
{{"character": "<name>",
  "cast": ["<the real names of the movie's top 3 billed actors>"],
  "edits": [{{"title": "<name>", "setup": [<start>, <end>], "payoff": [<start>, <end>],
             "montage": [[<start>, <end>], ...]}}]}}

{text}
"""


WINDOW = [0.0, 1e9]       # the movie without its intro and end credits (set per movie by plan)

# subtitle lines that aren't the movie's dialogue: subtitle-site ads and credits, song lyrics, sound labels
NOT_DIALOGUE = re.compile(r"opensubtitles|subtitles? (by|from|ripped|downloaded)|sync(ed|hronized)? (&|and |by)|"
                          r"corrected by|captioning|captions by|translated by|www\.|https?:|\.(com|org|net)\b|"
                          r"addic7ed|subscene|yts\b|yify|advertise your", re.I)


def is_dialogue(text):
    t = re.sub(r"<[^>]+>|\{[^}]*\}", "", str(text)).strip()
    if not t or NOT_DIALOGUE.search(t):
        return False
    if "♪" in t or "♫" in t or t.startswith("#"):                    # song lyrics (the end-credits song)
        return False
    return bool(re.sub(r"\[[^\]]*\]|\([^)]*\)|[-\s.…!?]", "", t))   # not just "[music]" or "(door slams)"


def dark_from(bright, start, end, run=30, share=0.8, level=40.0):
    """The first second in start..end where the picture stays dark (black, or white text on black) for most
    of the next `run` seconds: where the end credits start. None if it never does."""
    if not bright:
        return None
    if max(bright) > 300:                                          # 10-bit video: brightness runs to 1023
        level *= 4
    dark = [1 if v < level else 0 for v in bright]
    for c in range(max(0, int(start)), min(int(end), len(dark) - run) + 1):
        if sum(dark[c:c + run]) >= share * run:
            return float(c + dark[c:c + run].index(1))
    return None


def story_window(total, cues, bright=()):
    """Where the story is: after the opening logos/titles and before the end credits. Starts just before the
    first line of real dialogue (never more than 8% in) and ends where the picture fades into the dark end
    credits after the last line of dialogue (or 20 seconds after it). Subtitle-site ads and end-credit song
    lyrics don't count as dialogue."""
    real = [c for c in cues if is_dialogue(c[2])]
    if len(real) < 10:
        return [round(total * 0.02, 2), round(total * 0.92, 2)]
    lo = min(max(0.0, real[0][0] - 5.0), total * 0.08)
    last = real[-1][1]
    if last < total * 0.6:                                         # subtitles stop early: they're not the whole movie
        return [round(lo, 2), round(total * 0.92, 2)]
    fade = dark_from(list(bright), last, last + 180)
    hi = fade if fade is not None else last + 20.0
    return [round(lo, 2), round(max(last, min(hi, total - 1.0)), 2)]


def _num(v, total, default=0.0):
    try:
        return max(WINDOW[0], min(float(v), max(0.0, total - 1.0), WINDOW[1]))
    except (TypeError, ValueError):
        return default


def clean_beats(raw, total):
    """The writer's beats with valid times: narration {"say", "at"} or original moments {"keep", "until"}."""
    beats = []
    for b in raw or []:
        if not isinstance(b, dict):
            continue
        if str(b.get("say") or "").strip() and b.get("at") is not None:
            beats.append({"say": str(b["say"]).strip(), "at": round(_num(b["at"], total), 2)})
            if b.get("joke") is True:
                beats[-1]["joke"] = True
            mood = str(b.get("mood") or "").strip().lower()
            if mood in MOODS:
                beats[-1]["mood"] = mood
            elif b.get("joke") is True:
                beats[-1]["mood"] = "funny"
        elif b.get("keep") is not None or (b.get("at") is not None and b.get("until") is not None):
            a = _num(b["keep"] if b.get("keep") is not None else b["at"], total)
            e = _num(b.get("until", a + 5), total, a + 5)
            e = min(max(e, a + 2.0), a + 10.0, total)
            if e - a >= 1.0:
                beats.append({"keep": round(a, 2), "until": round(e, 2)})
    return beats


def clean_story(data, total):
    """Checks the writer's plan: valid times, at least 2 parts. Returns [(start, end, part info)]."""
    parts = []
    for p in data.get("parts") or []:
        beats = clean_beats(p.get("beats"), total)
        if fast():                                             # non-stop narration: no silent moments
            beats = [{k: v for k, v in b.items() if k != "joke"} for b in beats if "say" in b]
        if any("say" in b for b in beats):
            parts.append({"title": str(p.get("title") or "").strip()[:60], "beats": beats,
                          "caption": re.sub(r"\s*#\w+", "", str(p.get("caption") or "")).strip()[:300]})
    if len(parts) == 1 and len(parts[0]["beats"]) >= 6:          # one long part: share it out into two
        beats = parts[0]["beats"]
        half = len(beats) // 2
        parts = [{"title": parts[0]["title"], "beats": beats[:half], "caption": parts[0]["caption"]},
                 {"title": "", "beats": beats[half:], "caption": ""}]
    parts = [q for p in parts for q in split_long(p)]          # never a part well over 5 minutes
    parts = merge_short(parts)                                 # nor one under a minute
    if len(parts) > MAX_STORY_PARTS:                            # never more than 8: the extra goes into the last
        extra = [b for p in parts[MAX_STORY_PARTS - 1:] for b in p["beats"]]
        parts = parts[:MAX_STORY_PARTS - 1] + [{"title": parts[MAX_STORY_PARTS - 1]["title"], "beats": extra,
                                                "caption": parts[MAX_STORY_PARTS - 1]["caption"]}]
    if len(parts) < 2:
        raise Skip("The story plan didn't have enough to make at least 2 parts.")
    intro = [b for b in clean_beats(data.get("intro"), total) if "say" in b]     # narrated intro and outro
    outro = [b for b in clean_beats(data.get("outro"), total) if "say" in b]
    parts[0]["beats"] = intro + parts[0]["beats"]
    parts[-1]["beats"] = parts[-1]["beats"] + outro
    who = str(data.get("character") or "").strip()
    out = []
    for p in parts:
        times = [b.get("at", b.get("keep")) for b in p["beats"]]
        p["character"] = who
        if WINDOW[1] < total:
            p["story_end"] = WINDOW[1]                         # footage never runs on into the end credits
        out.append((min(times), max(times) + 8.0, p))
    return out


def part_words(seconds):
    """Words of narration that take about this long to read at the narrator's speed."""
    return int(seconds * WORDS_PER_SECOND * max(0.5, TEMPO))


def part_seconds(beats):
    """About how long a part plays: the narration (sped up by TEMPO), the pauses, and the original moments."""
    out = 0.0
    for b in beats:
        if "say" in b:
            out += len(b["say"].split()) / WORDS_PER_SECOND / max(0.5, TEMPO) + line_gap() + (JOKE_PAUSE if b.get("joke") else 0)
        else:
            out += b["until"] - b["keep"]
    return out


def line_gap():
    """Seconds of footage after each narrated line: the fast style barely breathes."""
    return 0.1 if fast() else 0.35


def split_long(part):
    """A part well over 5 minutes is cut in two (or more) at a narration beat."""
    beats = part["beats"]
    if part_seconds(beats) <= PART_MAX_SECONDS * PART_OVERRUN or len(beats) < 4:
        return [part]
    target, acc, cut = part_seconds(beats) / 2, 0.0, len(beats) // 2
    for k, b in enumerate(beats):
        acc += part_seconds([b])
        if acc >= target and "say" in b:
            cut = k + 1
            break
    first, second = beats[:cut], beats[cut:]
    if not any("say" in b for b in second) or not any("say" in b for b in first):
        return [part]
    return (split_long({"title": part["title"], "beats": first, "caption": part.get("caption", "")}) +
            split_long({"title": part["title"], "beats": second, "caption": ""}))


def merge_short(parts):
    """A part under a minute joins its neighbour (the next one, or the one before for the last part),
    as long as the two together stay within 5 minutes and at least 2 parts are left."""
    parts = list(parts)
    k = 0
    while k < len(parts) and len(parts) > 2:
        if part_seconds(parts[k]["beats"]) >= PART_MIN_SECONDS:
            k += 1
            continue
        j = k + 1 if k + 1 < len(parts) else k - 1
        if k + 1 < len(parts) and k > 0 and (part_seconds(parts[k - 1]["beats"]) < part_seconds(parts[k + 1]["beats"])):
            j = k - 1                                          # the shorter neighbour takes it
        a, b = sorted((j, k))
        if part_seconds(parts[a]["beats"] + parts[b]["beats"]) > PART_MAX_SECONDS:
            k += 1
            continue
        parts[a] = {"title": parts[a]["title"] or parts[b]["title"], "beats": parts[a]["beats"] + parts[b]["beats"],
                    "caption": parts[a].get("caption") or parts[b].get("caption", "")}
        del parts[b]
        k = a
    return parts


def clean_hype(data, total):
    who = str(data.get("character") or "").strip()
    out = []
    for e in (data.get("edits") or [])[:max(1, HYPE_COUNT)]:
        try:
            sa, sb = (_num(x, total) for x in e["setup"][:2])
            pa, pb = (_num(x, total) for x in e["payoff"][:2])
        except (KeyError, TypeError, ValueError):
            continue
        sb, pb = min(max(sb, sa + 4), sa + 30, total), min(max(pb, pa + 4), pa + 35, total)
        shots = []
        for m in e.get("montage") or []:
            try:
                a, b = (_num(x, total) for x in m[:2])
            except (TypeError, ValueError):
                continue
            shots.append([round(a, 2), round(max(b, a + 1.0), 2)])
        if not shots:
            shots = [[pa, pb]]
        out.append((min(sa, pa), max(sb, pb), {"title": str(e.get("title") or who).strip()[:30], "character": who,
                                               "setup": [sa, sb], "payoff": [pa, pb], "montage": shots}))
    if not out:
        raise Skip("The story model couldn't find a setup and payoff for a hype edit.")
    return out


def hype_from_sound(total, loud, motion, score):
    """No story model: the loudest stretch is the payoff, the 20 seconds before it the setup, and the most
    action-packed seconds make the montage."""
    n = len(score)
    if n < 60:
        raise Skip("The video is too short for a hype edit.")
    lo, hi = int(n * 0.1), int(n * 0.95)
    rk = P.rank01(loud)
    best, pa = -1.0, lo
    for s in range(max(lo, 30), max(lo + 31, hi - 20)):
        v = sum(rk[s:s + 20])
        if v > best:
            best, pa = v, s
    action = sorted(range(lo, hi), key=lambda i: (motion[i] if i < len(motion) else 0) + rk[i], reverse=True)
    shots = []
    for i in action:
        if all(abs(i - a) >= 8 for a, _ in shots):
            shots.append((float(i), float(i + 2)))
        if len(shots) >= 12:
            break
    shots.sort()
    info = {"title": character(), "character": character(), "setup": [max(0.0, pa - 22.0), pa - 2.0],
            "payoff": [float(pa), float(pa + 20)], "montage": [list(s) for s in shots]}
    return [(info["setup"][0], pa + 20.0, info)]


def plan(mode, name, total, cues, loud=(), motion=(), score=()):
    """The parts for one of these modes: [(start, end, info)] (info is saved, so a stopped run carries on)."""
    who = character()
    WINDOW[:] = story_window(total, cues, getattr(P, "BRIGHT", ()))
    print(f"Story window (no intro or end credits): {int(WINDOW[0])}s to {int(WINDOW[1])}s", flush=True)
    cues = [c for c in cues if is_dialogue(c[2]) and WINDOW[0] - 2 <= c[0] <= WINDOW[1]]   # the writer never sees the credits
    ready = using_claude() or local_ready()
    if mode == "hype":
        if ready and cues:
            try:
                return clean_hype(remember_cast(name, ask(SYSTEM, hype_prompt(name, total, source(name, cues), who),
                                                          HYPE_SCHEMA)), total)
            except Exception as e:
                print(f"(the story model couldn't plan the hype edit, using loud moments instead: {e})", flush=True)
        return hype_from_sound(total, list(loud), list(motion), list(score))
    if not ready:
        why = ("add your Anthropic API key (anthropic_api_key in the laptop's config.json, or the "
               "ANTHROPIC_API_KEY secret on GitHub)" if MODEL.startswith("claude")
               else f"the story model ({MODEL}) isn't available on this run")
        raise Skip(f"No narration: {why}.")
    if not cues:
        raise Skip("No dialogue was found, so there's nothing to write the narration from.")
    return clean_story(remember_cast(name, ask(SYSTEM, story_prompt(name, total, source(name, cues), mode, who),
                                               STORY_SCHEMA)), total)


# ---------------------------------------------------------------- the narrator's voice
VOICE_ENGINE = os.environ.get("NARRATOR_ENGINE", "chatterbox")   # chatterbox (expressive, online) or edge
INTENSITY = float(os.environ.get("NARRATOR_INTENSITY", "0.9"))     # Expressiveness: 0.5 calm ... 1 wild (every voice)
TEMPO = float(os.environ.get("NARRATOR_TEMPO", "1.2"))             # narration plays this much faster (pitch kept)
CHATTERBOX_SPACE = "ResembleAI/Chatterbox"
_CHATTER = {"client": None, "ref": None, "fails": 0, "used": set(), "edge_only": False}


# How each mood is read. INTENSITY (the app's Expressiveness) scales all of it, for every voice:
#   exag/cfg/temp: Chatterbox (exaggeration added to INTENSITY, lower cfg = freer pacing, temperature)
#   rate/pitch/volume: Microsoft's voice (percent, Hz, percent at INTENSITY 1)
MOODS = {
    "calm":    {"exag": -0.15, "cfg": 0.45, "temp": 0.75, "rate": -4,  "pitch": -2, "volume": 0},
    "curious": {"exag": 0.0,   "cfg": 0.4,  "temp": 0.8,  "rate": 0,   "pitch": 4,  "volume": 0},
    "tense":   {"exag": 0.1,   "cfg": 0.35, "temp": 0.8,  "rate": -6,  "pitch": -4, "volume": 5},
    "funny":   {"exag": 0.2,   "cfg": 0.3,  "temp": 0.9,  "rate": 6,   "pitch": 6,  "volume": 5},
    "shocked": {"exag": 0.35,  "cfg": 0.25, "temp": 0.9,  "rate": 10,  "pitch": 12, "volume": 12},
    "sad":     {"exag": -0.1,  "cfg": 0.45, "temp": 0.75, "rate": -12, "pitch": -8, "volume": -5},
    "hype":    {"exag": 0.45,  "cfg": 0.25, "temp": 0.95, "rate": 12,  "pitch": 10, "volume": 15},
    "whisper": {"exag": -0.05, "cfg": 0.4,  "temp": 0.8,  "rate": -10, "pitch": -6, "volume": -20},
    "steady":  {"exag": 0.0,   "cfg": 0.45, "temp": 0.6,  "rate": 0,   "pitch": 0,  "volume": 0},
}
STEADY = "steady"   # fast style: every line in the same even delivery, so the voice never changes
DEFAULT_MOOD = "curious"


def mood_of(text, mood=None):
    """The beat's mood from the writer, or a guess from the line itself."""
    if mood in MOODS:
        return mood
    t = text.strip()
    if t.endswith("!") or t.isupper():
        return "shocked" if "?" in t else "hype"
    if t.endswith("?"):
        return "curious"
    return DEFAULT_MOOD


def chatterbox_style(mood):
    """(exaggeration, cfg weight, temperature) for Chatterbox."""
    m = MOODS[mood]
    return (round(min(1.6, max(0.3, INTENSITY + m["exag"] * INTENSITY / 0.8)), 2), m["cfg"], m["temp"])


def edge_style(mood):
    """(rate, pitch, volume) strings for Microsoft's voice: NARRATOR_SPEED plus the mood, scaled by INTENSITY."""
    m = MOODS[mood]
    k = max(0.0, min(1.5, INTENSITY)) / 0.8
    try:
        base = int(str(SPEED).strip().rstrip("%"))
    except ValueError:
        base = 0
    rate = base + round(m["rate"] * k)
    pitch, volume = round(m["pitch"] * k), max(-50, round(m["volume"] * k))
    return f"{rate:+d}%", f"{pitch:+d}Hz", f"{volume:+d}%"


def speak(text, mp3, mood=None):
    """Reads text aloud into mp3 in the beat's mood: Chatterbox's expressive delivery when it's reachable (free
    online model, in the voice of NARRATOR_VOICE), Microsoft's voice otherwise (pitch, pace and loudness follow
    the mood), sped up by TEMPO. Returns (seconds, [(start, end, word)])."""
    mood = STEADY if fast() else mood_of(text, mood)
    if VOICE_ENGINE == "chatterbox" and _CHATTER["fails"] < 2 and not _CHATTER["edge_only"]:
        try:
            out = faster(*speak_chatterbox(text, mp3, mood), mp3)
            _CHATTER["used"].add("chatterbox")
            return out
        except Exception as e:
            _CHATTER["fails"] += 1
            print(f"  (Chatterbox didn't answer{', using the standard voice from now on' if _CHATTER['fails'] >= 2 else ''}: {e})",
                  flush=True)
    _CHATTER["used"].add("edge")
    return faster(*speak_edge(text, mp3, mood), mp3)


def speak_lines(lines, workdir):
    """Reads all of a part's lines [(beat index, text, mood)] before any footage is cut, in ONE voice: if
    Chatterbox drops out partway, the whole part is read again with the standard voice, so the narrator
    never changes in the middle of a part. Returns {beat index: (mp3, seconds, words)}."""
    def read_all():
        _CHATTER["used"] = set()
        out = {}
        for k, text, mood in lines:
            P.checkpoint()
            mp3 = workdir / f"say{k:03}.mp3"
            out[k] = (mp3, *speak(text, mp3, mood))
        return out
    out = read_all()
    if len(_CHATTER["used"]) > 1:
        print("  (the voice changed partway through: reading this part again in the standard voice)", flush=True)
        _CHATTER["edge_only"] = True
        try:
            out = read_all()
        finally:
            _CHATTER["edge_only"] = False
    return out


def faster(length, words, mp3):
    """Speeds a spoken line up by TEMPO (pitch kept), with its word timings scaled to match."""
    if abs(TEMPO - 1) < 0.01:
        return length, words
    mp3 = Path(mp3).resolve()
    tmp = mp3.with_name(mp3.stem + ".tempo" + mp3.suffix)
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3), "-filter:a", f"atempo={TEMPO:.3f}",
           "-c:a", "libmp3lame", "-b:a", "160k", str(tmp)])
    os.replace(tmp, mp3)
    return P.duration(mp3), [(a / TEMPO, b / TEMPO, w) for a, b, w in words]


def speak_chatterbox(text, mp3, mood="funny"):
    """One line from Chatterbox (Resemble AI's open model, run on its free Hugging Face page)."""
    from gradio_client import Client, handle_file
    if _CHATTER["client"] is None:
        token = os.environ.get("HF_TOKEN") or None
        try:                                     # newer gradio_client renamed hf_token= to token=
            _CHATTER["client"] = Client(CHATTERBOX_SPACE, token=token, verbose=False)
        except TypeError:
            _CHATTER["client"] = Client(CHATTERBOX_SPACE, hf_token=token, verbose=False)
    if _CHATTER["ref"] is None:                  # a sample of the narrator's voice for Chatterbox to copy
        ref = Path("narrator_voice.mp3").resolve()
        speak_edge("Listen. This man is about to make the worst decision of his entire life! And honestly? I respect it.",
                   ref, "funny")
        _CHATTER["ref"] = ref
    exag, cfg, temp = chatterbox_style(mood if mood in MOODS else DEFAULT_MOOD)
    wav = _CHATTER["client"].predict(text, handle_file(str(_CHATTER["ref"])), exag, temp, 0, cfg, True,
                                     api_name="/generate_tts_audio")
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k",
           str(Path(mp3).resolve())])
    _CHATTER["fails"] = 0
    length = P.duration(mp3)
    return length, word_times(text, [], length)     # no word timings: captions are shared out evenly


def speak_edge(text, mp3, mood=None):
    """Microsoft's free neural voice (edge-tts), with exact word timings. mood: pitch, pace and loudness
    (None = the plain voice)."""
    import asyncio
    import edge_tts
    rate, pitch, volume = edge_style(mood) if mood in MOODS else (SPEED, "+0Hz", "+0%")

    async def go():
        try:
            com = edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch, volume=volume, boundary="WordBoundary")
        except TypeError:                       # older edge-tts: word timings come by default
            com = edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch, volume=volume)
        marks = []
        with open(mp3, "wb") as fh:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    fh.write(ch["data"])
                elif ch["type"] in ("WordBoundary", "SentenceBoundary"):
                    marks.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"], ch["type"]))
        return marks

    for attempt in range(4):
        try:
            marks = asyncio.run(go())
            break
        except Exception as e:
            if attempt == 3:
                raise
            print(f"  (voice not ready, trying again: {e})", flush=True)
            time.sleep(2 ** attempt)
    length = P.duration(mp3)
    return length, word_times(text, marks, length)


def word_times(text, marks, length):
    """Timed words from the voice's marks; sentence marks (or none) are shared out evenly."""
    words = [(a, b, t) for a, b, t, kind in marks if kind == "WordBoundary" and t.strip()]
    if words:
        return words
    spans = [(a, b, t) for a, b, t, kind in marks if kind == "SentenceBoundary"] or [(0.0, length, text)]
    out = []
    for a, b, t in spans:
        ws = t.split()
        step = (b - a) / max(1, len(ws))
        out += [(a + k * step, a + (k + 1) * step, w) for k, w in enumerate(ws)]
    return out


def chunks(words, max_words=3, max_chars=18):
    """Groups timed words into the short, punchy caption chunks recap videos use."""
    out, cur = [], []
    for w in words:
        text = " ".join(x[2] for x in cur + [w])
        if cur and (len(cur) >= max_words or len(text) > max_chars):
            out.append(cur)
            cur = []
        cur.append(w)
        if w[2].rstrip()[-1:] in ".?!,":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    res = []
    for k, c in enumerate(out):
        end = out[k + 1][0][0] if k + 1 < len(out) else c[-1][1] + 0.3
        res.append((c[0][0], max(end, c[0][0] + 0.2), " ".join(x[2] for x in c).strip()))
    return res


# ---------------------------------------------------------------- rendering
FRAME = ("split=2[bg][fg];"
         "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
         "scale=270:480,boxblur=10:2,scale=1080:1920,eq=brightness=-0.08[bgb];"
         "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgs];"
         "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
ZOOM_FRAME = ("split=2[bg][fg];"
              "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              "scale=270:480,boxblur=10:2,scale=1080:1920,eq=brightness=-0.12[bgb];"
              f"[fg]scale={STORY_BAND[0]}:{STORY_BAND[1]},setsar=1[fgs];"
              f"[bgb][fgs]overlay=0:{STORY_BAND_TOP},setsar=1")


def zoomed():
    """True when this movie is cut zoomed in with tracking (the default cutting style)."""
    return getattr(P, "STYLE", "classic") == "zoom"


_SUBS = {}


def old_subtitles(src):
    """(top, bottom) shares of the picture where the movie's own burned-in subtitles are, or None (once a movie)."""
    key = str(src)
    if key not in _SUBS:
        _SUBS[key] = None
        try:
            import tracking
            total = P.duration(src)
            a, b = (WINDOW[0], min(WINDOW[1], total)) if WINDOW[1] > WINDOW[0] else (0.0, total)
            _SUBS[key] = tracking.burned_subtitles(src, P.CROP["filter"], a + (b - a) * 0.05, b - (b - a) * 0.05)
        except Exception as e:
            print(f"  (couldn't check for burned-in subtitles: {e})", flush=True)
        if _SUBS[key]:
            print(f"  burned-in subtitles found ({_SUBS[key][0]:.0%} to {_SUBS[key][1]:.0%} down the picture): "
                  "blurring them, with the narration captions on top", flush=True)
    return _SUBS[key]


def hide_old_subtitles(src):
    """Filter text (ending in ",") that blurs the strip where the movie's own subtitles are, like the recap
    channels do, so they never show under or next to the narration captions. "" when there are none."""
    band = old_subtitles(src)
    if not band:
        return ""
    W, H = P.picture_size(src)
    y = int(band[0] * H) // 2 * 2
    h = max(2, int((band[1] - band[0]) * H) // 2 * 2)
    return (f"split=2[os0][os1];[os1]crop={W}:{h}:0:{y},boxblur=14:3,eq=brightness=-0.05[os2];"
            f"[os0][os2]overlay=0:{y},")


def caption_y(src, track, default):
    """Where narration captions go: on the blurred strip over the old subtitles when there is one."""
    band = old_subtitles(src)
    if not band:
        return default
    mid = (band[0] + band[1]) / 2
    if track:
        return STORY_BAND_TOP + mid * STORY_BAND[1] - 36
    W, H = P.picture_size(src)
    ph = 1080 * H / W
    return (1920 - ph) / 2 + mid * ph - 36


def tracked(src, start, length, workdir, tag):
    """The moving crop that keeps the characters in the zoomed picture ("" when not zoomed)."""
    if not zoomed():
        return ""
    import tracking
    W, H = P.picture_size(src)
    return tracking.plan(src, start, length, P.CROP["filter"], W, H, workdir, band=STORY_BAND, name=tag)
def enc():
    """Encoding for each piece: hype edits are these pieces joined as they are, so they get the HD settings."""
    return [*P.hd_video(18), *P.HD_AUDIO]
_AUDIO = {}


@contextmanager
def classic():
    """process.py's helpers (caption places, the line, the cockroach) laid out for a full-width picture: these
    modes place their own zoomed picture (STORY_BAND), not process.py's wide band."""
    old = P.STYLE
    P.STYLE = "classic"
    try:
        yield
    finally:
        P.STYLE = old


def has_audio(src):
    key = str(src)
    if key not in _AUDIO:
        _AUDIO[key] = P.has_audio(src)
    return _AUDIO[key]


def caption_filters(words, length, bottom, workdir, tag, y=None):
    """Narration captions: big chunks of 2-3 words just under the picture, or (fast style) short phrases of
    up to 3 words on the lower part of the picture, like the recap channels."""
    out, size, border = [], 72, 6
    if fast():
        size, border = 70, 6
    if y is None:
        y = (bottom - 110 if bottom < 1650 else 1380) if fast() else (bottom + 40 if bottom < 1650 else 1500)
    for k, (a, b, text) in enumerate(chunks(words, *((3, 20) if fast() else ()))):
        a, b = max(0.0, a), min(length, b)
        if b <= a:
            continue
        f = workdir / f"{tag}{k}.txt"
        f.write_text(text.upper(), encoding="utf-8")
        out.append(f",drawtext=fontfile={P.FONT}:textfile={f.name}:expansion=none:fontsize={size}:fontcolor=white:"
                   f"borderw={border}:bordercolor=black:shadowx=0:shadowy=5:shadowcolor=black@0.5:x=(w-text_w)/2:y={y:.0f}:enable='between(t,{a:.3f},{b:.3f})'")
    return "".join(out)


def title_filter(text, workdir, until=1.4):
    """Big name title that fades in at the start of the montage."""
    if not text:
        return ""
    f = workdir / "hypetitle.txt"
    f.write_text(text.upper(), encoding="utf-8")
    return (f",drawtext=fontfile={P.FONT}:textfile={f.name}:expansion=none:fontsize=130:fontcolor=white:"
            f"borderw=8:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2:alpha='min(1,t/0.12)':"
            f"enable='lt(t,{until:.2f})'")


EDGE = 0.6   # a movie cut this close to the start or end of a piece makes a quick double cut (a flicker)


def clean_start(src, start, length, total):
    """
    Moves a piece's start so no movie cut sits right next to the edit's own cut: two cuts a few frames
    apart flash the screen. A cut just after the start: start on it. A cut just before the end: end before
    it. A short piece (montage shot) is kept to one shot. Returns the new start.
    """
    try:
        cuts = [start + c for c in P.scene_cuts(Path(src), start, length)]
    except Exception:
        return start
    short = length < 1.6
    for c in cuts:
        if not (start + 0.05 < c < start + length - 0.02):
            continue
        if c - start < EDGE or (short and c - start <= start + length - c):
            start = c + 0.05                       # begin on the movie's own cut
        elif start + length - c < EDGE or short:
            start = c - length - 0.05              # finish just before it
        else:
            continue
        break
    return max(0.0, min(start, total - length - 0.1))


def render_segment(src, start, length, out, workdir, voice=None, words=None, cues=None, flash=False,
                   slow=1.0, movie_volume=1.0, title="", bridge=False, ending=False):
    """One piece of a part: the movie from `start` for `length` seconds (whole picture, graded), with the
    narrator over it (voice), the movie's own subtitles (cues), a white flash in (Hype edit only), or slow
    motion. Narrated parts never flash or dip to black (it hurts to watch): their pieces join with plain cuts.
    bridge: a jump to another scene: the movie sound eases in instead of cutting in.
    ending: the last piece of a part eases out instead of stopping dead."""
    src_len = length / slow
    track = tracked(src, start, src_len, workdir, f"track_{Path(out).stem}")
    first = P.flip_filter(src, start, start + src_len).replace(
        "[0:v]", f"[0:v]{P.CROP['filter']}{hide_old_subtitles(src)}{track}{grade()}", 1)
    v = first + (ZOOM_FRAME if track else FRAME)
    if slow != 1.0:
        v += f",setpts={slow:.3f}*PTS"
    with classic():
        bottom = ZOOM_TEXT_BOTTOM if track else P.movie_bottom(src)
        if words:
            v += caption_filters(words, length, bottom, workdir, "nar",
                                 y=caption_y(src, track, None) if old_subtitles(src) else None)
        if cues:
            part_cues = [(a - start, b - start, t) for a, b, t in cues if b > start and a < start + src_len]
            v += P.subtitle_filters(part_cues, length, bottom, workdir)
    v += title_filter(title, workdir)
    if flash:
        v += ",fade=t=in:st=0:d=0.16:color=white"
    if ending:
        v += f",fade=t=out:st={max(0.0, length - 0.6):.3f}:d=0.6"
    v += "[v]"
    inputs = ["-ss", f"{start:.3f}", "-t", f"{src_len:.3f}", "-i", str(Path(src).resolve())]
    movie_a = "[0:a]" if has_audio(src) else None
    if movie_a and slow != 1.0:
        a = f"{movie_a}atempo={1 / slow:.3f},volume={movie_volume:.2f}[ma]"
    elif movie_a:
        a = f"{movie_a}volume={movie_volume:.2f}[ma]"
    else:
        inputs += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        a = "[1:a]anull[ma]"
    if voice:
        vi = inputs.count("-i")
        inputs += ["-i", str(Path(voice).resolve())]
        a += f";[{vi}:a]volume=1.6,apad[na];[ma][na]amix=inputs=2:duration=first:normalize=0[a]"
    else:
        a += ";[ma]apad[a]"
    if bridge or ending:
        fades = (["afade=t=in:d=0.25"] if bridge else []) + ([f"afade=t=out:st={max(0.0, length - 0.6):.3f}:d=0.6"]
                                                            if ending else [])
        a = a[:-3] + "[a0];[a0]" + ",".join(fades) + "[a]"
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, *P.graph_args(f"{v};{a}", workdir),
           "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}", *enc(), str(Path(out).resolve())], cwd=workdir)
    for f in list(workdir.glob("sub*.txt")) + list(workdir.glob("nar*.txt")) + list(workdir.glob("track_*.txt")):
        f.unlink(missing_ok=True)


PANEL_H = 608
PANEL_Y = (48, 48 + PANEL_H, 48 + 2 * PANEL_H)


def panel_mask(workdir):
    """A 1080x608 mask with slanted top and bottom edges, for the diagonal mask-slide panels."""
    mask = workdir / "panel_mask.png"
    if not mask.exists():
        P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
               f"color=c=black:s=1080x{PANEL_H}:d=1", "-vf",
               f"format=gray,geq=lum='255*between(Y-0.1*(X-540),30,{PANEL_H - 30})'", "-frames:v", "1",
               str(mask.resolve())], cwd=workdir)
    return mask


def render_slide3(src, starts, length, out, workdir):
    """Three shots stacked down the screen (each the whole picture, no zoom), sliding in one after another
    from alternating sides behind slanted mask edges, over a dark blurred copy of the middle shot."""
    mask = panel_mask(workdir)
    inputs = []
    for s in starts:
        inputs += ["-ss", f"{s:.3f}", "-t", f"{length:.3f}", "-i", str(Path(src).resolve())]
    inputs += ["-loop", "1", "-t", f"{length:.3f}", "-i", str(mask.resolve())]
    g = f"{P.CROP['filter']}{grade()}"
    f = (f"[1:v]split=2[bgsrc][in1];[bgsrc]{g}scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
         "scale=270:480,boxblur=12:2,scale=1080:1920,eq=brightness=-0.3,setsar=1[bg0];[3:v]format=gray,split=3[m0][m1][m2]")
    last = "bg0"
    for i in range(3):
        sx = -1080 if i % 2 == 0 else 1080
        t0, dur = i * 0.1, 0.28
        f += (f";{'[in1]' if i == 1 else f'[{i}:v]'}{g}scale=1080:{PANEL_H}:force_original_aspect_ratio=decrease,"
              f"pad=1080:{PANEL_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=rgba[p{i}];[p{i}][m{i}]alphamerge[q{i}]"
              f";[{last}][q{i}]overlay=x='{sx}*pow(1-clip((t-{t0})/{dur},0,1),3)':y={PANEL_Y[i]}:eval=frame[o{i}]")
        last = f"o{i}"
    f += f";[{last}]fade=t=in:st=0:d=0.12:color=white[v]"
    if has_audio(src):
        f += ";[1:a]volume=0.25,apad[a]"
    else:
        inputs += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        f += ";[4:a]anull[a]"
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, *P.graph_args(f, workdir),
           "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}", *enc(), str(Path(out).resolve())], cwd=workdir)


def concat(segs, out, workdir):
    lst = workdir / "segments.txt"
    lst.write_text("".join(f"file '{Path(s).resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i",
           str(lst.resolve()), "-c", "copy", "-movflags", "+faststart", str(Path(out).resolve())], cwd=workdir)


_CREDIT = {"last": None}


def music_list():
    """The songs listed in assets/music/tracks.json: [{"file", "url", "kind", "credit"}]."""
    try:
        return json.loads((MUSIC_DIR / "tracks.json").read_text(encoding="utf-8")).get("tracks", [])
    except (OSError, ValueError):
        return []


# Your own songs go in folders named after the category, e.g. assets/music/recap-story, assets/music/hype,
# assets/music/highlight (in assets/music, in a "Music" folder next to "Ready to post", or MUSIC_FOLDER).
# A folder's name decides its kind by the words in it:
FOLDER_KINDS = [("hype", ("hype", "phonk", "edit")), ("character", ("character",)),
                ("highlight", ("highlight", "clip", "stretch")),
                ("recap", ("recap", "story", "narrat", "calm"))]
# what each kind falls back to when it has no songs of its own ("calm" = the free tracks in tracks.json)
MUSIC_FALLBACK = {"recap": ["recap", "calm", "any"], "calm": ["recap", "calm", "any"],
                  "character": ["character", "recap", "calm", "any"],
                  "hype": ["hype", "any"], "highlight": ["highlight"]}
MUSIC_ROOTS = []   # more places to look for song folders (process.py adds the laptop's output folder/Music)


def folder_kind(name):
    n = name.lower()
    for kind, words in FOLDER_KINDS:
        if any(w in n for w in words):
            return kind
    return "any"


def music_roots():
    roots = [MUSIC_DIR] + [Path(r) for r in MUSIC_ROOTS]
    if os.environ.get("MUSIC_FOLDER"):
        roots.append(Path(os.environ["MUSIC_FOLDER"]))
    out = []
    for r in roots:
        if r.is_dir() and r.resolve() not in [o.resolve() for o in out]:
            out.append(r)
    return out


def available_music():
    """[(file, credit line, kind)] for every song on this computer: the listed ones that were downloaded
    (their kind from tracks.json), songs loose in a music folder (kind "any", no credit line), and songs in
    a sub-folder named after a category (see FOLDER_KINDS), up to two folders deep."""
    listed = {t["file"]: t for t in music_list() if t.get("file")}
    out = []
    for root in music_roots():
        for f in sorted(root.rglob("*")):
            rel = f.relative_to(root).parts
            if f.suffix.lower() not in AUDIO_EXT or len(rel) > 3:
                continue
            if len(rel) == 1:
                t = listed.get(f.name, {}) if root == MUSIC_DIR else {}
                out.append((f, t.get("credit", ""), t.get("kind", "any")))
            else:
                kind = next((k for k in map(folder_kind, reversed(rel[:-1])) if k != "any"), "any")
                out.append((f, "", kind))
    return out


def pick_music(kind, seed=None):
    """A song for this part from its category's folder (see MUSIC_FALLBACK), or None.
    Remembers its credit line for the caption (see last_credit)."""
    _CREDIT["last"] = None
    if not MUSIC:
        return None
    songs = available_music()
    for k in MUSIC_FALLBACK.get(kind, [kind, "any"]):
        fits = [s for s in songs if s[2] == k]
        if fits:
            f, credit, _ = random.Random(seed).choice(fits)
            _CREDIT["last"] = credit or None
            print(f"  music ({kind}): {f.parent.name}/{f.name}", flush=True)
            return f
    if kind != "highlight":
        where = ", ".join(str(r) for r in music_roots()) or str(MUSIC_DIR)
        print(f"  (no {kind} music found: put songs in a '{kind}' folder in {where})", flush=True)
    return None


def music_under(video, music, workdir, lufs=-30):
    """Adds a song quietly under a finished clip's own sound (ducked while people talk); the picture is kept."""
    video = Path(video).resolve()
    tmp = video.with_name(video.stem + ".music" + video.suffix)
    length = P.duration(video)
    graph = (f"[1:a]loudnorm=I={lufs}:TP=-8:LRA=11,aresample=48000,afade=t=in:d=1.5,"
             f"afade=t=out:st={max(0.0, length - 2):.2f}:d=2[mu0];[0:a]asplit=2[vo][key];"
             "[mu0][key]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=450[mu];"
             "[vo][mu]amix=inputs=2:duration=first:normalize=0[a]")
    try:
        P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-stream_loop", "-1",
               "-i", str(Path(music).resolve()), "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
               "-c:v", "copy", *P.HD_AUDIO, "-t", f"{length:.3f}", "-map_chapters", "-1", "-movflags", "+faststart",
               str(tmp)], cwd=workdir)
        os.replace(tmp, video)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"  (couldn't add the music: {e})", flush=True)


def last_credit():
    """The credit line the last picked song's licence asks for (it goes in the caption), or None."""
    return _CREDIT["last"]


def download_music():
    """Downloads the songs in tracks.json that aren't here yet (the workflow runs this before cutting)."""
    for t in music_list():
        f = MUSIC_DIR / t["file"]
        if f.exists() or not t.get("url"):
            continue
        print(f"Downloading music: {t['file']}", flush=True)
        try:
            req = urllib.request.Request(t["url"], headers={"User-Agent": "movie-splitter"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            f.write_bytes(data)
        except Exception as e:
            print(f"  (couldn't download it: {e})", flush=True)


LIGHT_SWEEP = False        # off by default: a bright streak across the screen can hurt to watch
SWEEP_SECONDS = 3.0        # how long the one light sweep in each part takes to cross the screen (app: Light sweep speed)
SWEEP_AT = 0.3             # ...and where in the part it happens (0.3 = 30% of the way in)
CONTINUE_GAP = 12.0        # narration footage this close to where the last piece ended just carries on from there


def sweep_image(workdir):
    """A big transparent image with one soft, faint white streak along its diagonal (made once)."""
    f = workdir / "sweep.png"
    if not f.exists():
        P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
               "color=c=white:s=3000x3000:d=1", "-vf",
               "format=rgba,geq=r=255:g=255:b=255:a='46*exp(-pow(Y-X,2)/(2*70*70))+30*exp(-pow(Y-X-260,2)/(2*20*20))'",
               "-frames:v", "1", str(f.resolve())], cwd=workdir)
    return f.resolve()


def specks_image(workdir):
    """Tiny fixed white specks sprinkled over the frame, for the vivid look (made once)."""
    f = workdir / "specks.png"
    if not f.exists():
        P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
               "color=c=white:s=1080x1920:d=1", "-vf",
               "format=rgba,geq=r=255:g=255:b=255:a='170*lt(random(1),0.0004)'",
               "-frames:v", "1", str(f.resolve())], cwd=workdir)
    return f.resolve()


def finish_part(segs, out, workdir, src, top_text, music=None, music_volume=None, loud=False):
    """Joins the pieces, then adds the part-level touches in one last encode: the top label, the line,
    the cockroach, and quiet background music. loud: evened out to TikTok loudness (-14 LUFS), like the
    recap channels' always-full sound."""
    joined = workdir / "joined.mp4"
    concat(segs, joined, workdir)
    length = P.duration(joined)
    with classic():
        roach = P.cockroach_filter(length, src)
    extra = roach[0] if roach else []
    n = 1 + extra.count("-i")                         # the next input's number
    v = "[0:v]null"
    if LIGHT_SWEEP:                                   # a soft white streak sweeping top-right -> bottom-left
        extra += ["-loop", "1", "-framerate", str(FPS), "-t", f"{length:.3f}", "-i", str(sweep_image(workdir))]
        start = max(1.0, length * SWEEP_AT)                # once per part
        u = f"(-910+1800*clip((t-{start:.2f})/{max(0.5, SWEEP_SECONDS):.2f},0,1))"
        v += f"[pre0];[pre0][{n}:v]overlay=x='-960-{u}':y='-540+{u}':eval=frame"
        n += 1
    if FILTER == "vivid":                             # the vivid look's fixed sprinkle of tiny white specks
        extra += ["-loop", "1", "-framerate", str(FPS), "-t", f"{length:.3f}", "-i", str(specks_image(workdir))]
        v += f"[pre1];[pre1][{n}:v]overlay=0:0"
        n += 1
    if top_text:
        (workdir / "toptext.txt").write_text(top_text, encoding="utf-8")
        v += (f",drawtext=fontfile={P.FONT}:textfile=toptext.txt:expansion=none:"
              f"fontsize=96:fontcolor=white:borderw=7:bordercolor=black:x=(w-text_w)/2:y={150 if zoomed() else 230}")
    with classic():
        v += "[txt0]" + P.line_filter(src, length)
    v += roach[1] if roach else ";[txt]null[v]"
    a = "[0:a]anull[a]"
    if music:
        mi = 1 + extra.count("-i")
        extra = extra + ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
        vol = MUSIC_VOLUME if music_volume is None else music_volume
        fades = f"afade=t=in:d=1.5,afade=t=out:st={max(0.0, length - 2):.2f}:d=2"
        if loud:
            # under the narrator: every song brought to the same quiet level first (some are mastered very loud),
            # then pushed down further whenever the narrator speaks, so it never covers the words
            a = (f"[{mi}:a]loudnorm=I={MUSIC_UNDER_LUFS:.0f}:TP=-8:LRA=11,aresample=48000,volume={vol / 0.12:.3f},{fades}[mu0];"
                 "[0:a]asplit=2[vo][key];"
                 "[mu0][key]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=450:makeup=1[mu];"
                 "[vo][mu]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            a = (f"[{mi}:a]volume={vol:.3f},{fades}[mu];"
                 f"[0:a][mu]amix=inputs=2:duration=first:normalize=0[a]")
    if loud:
        a = a[:-3] + "[a1];[a1]loudnorm=I=-14:TP=-1.5:LRA=7[a]"
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", str(joined.resolve()), *extra,
           *P.graph_args(f"{v};{a}", workdir), "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}",
           *P.hd_video(), *P.HD_AUDIO, "-movflags", "+faststart",
           str(Path(out).resolve())], cwd=workdir)
    for f in [joined, *segs]:
        Path(f).unlink(missing_ok=True)


def render_story(src, number, info, cues, out, workdir, total, mode="recap"):
    """A narrated part (Movie recap / Character story). Returns the narration lines (for captions)."""
    segs, said = [], []
    last_end = None          # where the previous piece's footage ended: a big jump gets a dip from black
    beats = info["beats"]
    total = min(total, info.get("story_end") or total)
    voices = speak_lines([(k, b["say"], b.get("mood")) for k, b in enumerate(beats) if "say" in b], workdir)
    for k, beat in enumerate(beats):
        P.checkpoint()
        seg = workdir / f"seg{k:03}.mp4"
        end_of_part = k == len(beats) - 1
        if "say" in beat:
            mp3, length, words = voices[k]
            length += line_gap() + (0.5 if end_of_part else 0)      # the cliffhanger line gets a beat to land
            if beat.get("joke") and not end_of_part:
                length += JOKE_PAUSE                            # a moment of footage so the joke lands
            at = max(0.0, min(beat["at"], total - length - 0.1))
            if last_end is not None and -CONTINUE_GAP < at - last_end < CONTINUE_GAP:
                at = max(0.0, min(last_end, total - length - 0.1))   # same scene: carry on, no jump back and forth
            else:
                at = clean_start(src, at, length, total)              # a new spot: no cut right next to ours
            jump = last_end is not None and abs(at - last_end) > 20
            render_segment(src, at, length, seg, workdir, voice=mp3, words=words, movie_volume=DUCK,
                           bridge=jump, ending=end_of_part)
            mp3.unlink(missing_ok=True)
            said.append((0.0, length, beat["say"]))
            last_end = at + length
        else:
            jump = last_end is not None and abs(beat["keep"] - last_end) > 20
            render_segment(src, beat["keep"], beat["until"] - beat["keep"], seg, workdir, cues=cues,
                           bridge=jump, ending=end_of_part)
            last_end = beat["until"]
        segs.append(seg)
    finish_part(segs, out, workdir, src, f"Part {number}", music=pick_music(MUSIC_KIND.get(mode, "recap"), number), loud=fast())
    return said


def beat_grid(music, seconds):
    """Finds the song's beat. Returns (where to start in the song: its loudest stretch, seconds per beat)."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(music), "-ac", "1", "-ar", "22050", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    hop, sr = 512, 22050
    fps = sr / hop
    n = len(x) // hop
    if n < fps * 4:
        return 0.0, 0.5
    energy = np.log1p(100 * np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)))
    onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    o = onset - onset.mean()
    ac = np.correlate(o, o, "full")[n - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    lag = lo + int(np.argmax(ac[lo:hi]))
    win = int(seconds * fps)
    start = int(np.argmax(np.convolve(energy, np.ones(win) / win, "valid"))) if n > win + lag else 0
    count = max(1, int(win / lag))
    phase = max(range(lag), key=lambda p: onset[start + p:start + p + count * lag:lag].sum())
    return (start + phase) / fps, lag / fps


def montage_steps(beat, seconds):
    """The montage's shot pattern in beats: mostly single flash cuts, a three-panel mask slide every so
    often, and one slow-motion shot in the middle."""
    unit = 1 if beat >= 0.4 else 2
    pattern = ["single", "single", "single", "slide3", "single", "single", "slow", "single", "single", "slide3"]
    steps, t, k = [], 0.0, 0
    while t < seconds - 0.05:
        kind = pattern[k % len(pattern)]
        if kind == "slow" and k > len(pattern):
            kind = "single"                         # only one slow-motion shot
        n = unit * (2 if kind in ("slide3", "slow") else 1)
        steps.append((kind, n))
        t += n * beat
        k += 1
    return steps


def render_montage(src, info, out, workdir, total):
    """The beat-synced montage, with the song mixed in (or the shots' own sound without a song)."""
    music = pick_music("hype", info.get("title"))
    seconds = HYPE_MONTAGE_SECONDS
    offset, beat = beat_grid(music, seconds) if music else (0.0, 0.5)
    shots = [a for a, _ in info["montage"]] or [info["payoff"][0]]
    pick = iter(shots * 20)
    segs, t_beats = [], 0
    for k, (kind, n) in enumerate(montage_steps(beat, seconds)):
        P.checkpoint()
        f0, f1 = round(t_beats * beat * FPS), round((t_beats + n) * beat * FPS)
        length = max(2, f1 - f0) / FPS
        t_beats += n
        seg = workdir / f"mon{k:03}.mp4"
        if kind == "slide3":
            starts = [min(next(pick), total - length - 0.1) for _ in range(3)]
            render_slide3(src, starts, length, seg, workdir)
        else:
            slow = 2.0 if kind == "slow" else 1.0
            s = min(next(pick), total - length / slow - 0.1)
            s = clean_start(src, max(0.0, s), length / slow, total)       # one shot per beat, no flicker
            render_segment(src, max(0.0, s), length, seg, workdir, flash=True, slow=slow,
                           movie_volume=0.25 if music else 1.0, title=info.get("title", "") if k == 0 else "")
        segs.append(seg)
    raw = workdir / "montage_raw.mp4"
    concat(segs, raw, workdir)
    for s in segs:
        s.unlink(missing_ok=True)
    if not music:
        raw.replace(out)
        return
    length = P.duration(raw)
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw.resolve()),
           "-ss", f"{offset:.3f}", "-i", str(Path(music).resolve()), "-filter_complex",
           f"[1:a]volume=1.0,afade=t=out:st={max(0.0, length - 0.6):.2f}:d=0.6[mu];"
           "[0:a][mu]amix=inputs=2:duration=first:normalize=0[a]",
           "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
           "-t", f"{length:.3f}", "-movflags", "+faststart", str(Path(out).resolve())], cwd=workdir)
    raw.unlink(missing_ok=True)


def render_hype(src, number, info, cues, out, workdir, total):
    """Setup (they get disrespected) -> payoff (they answer it) -> beat-synced montage."""
    segs = []
    for k, key in enumerate(("setup", "payoff")):
        P.checkpoint()
        a, b = info[key]
        seg = workdir / f"seg{k:03}.mp4"
        render_segment(src, a, b - a, seg, workdir, cues=cues, flash=(key == "payoff"))
        segs.append(seg)
    montage = workdir / "seg002.mp4"
    render_montage(src, info, montage, workdir, total)
    segs.append(montage)
    finish_part(segs, out, workdir, src, "")
    return [(0.0, 1.0, info.get("title") or "")]


def make_pack(mode, src, number, info, out, workdir, total):
    """
    Instead of a finished video: an edit pack for the laptop, where FilmCraft builds the edit from the
    original movie (much better masks, keyframes and transitions than ffmpeg). The zip holds plan.json
    (the part's plan, the look, the beat) plus the narrator's voice files and the song, cut to start at
    its drop for hype montages. Returns the narration lines (for the caption quote).
    """
    import zipfile
    plan = {"version": 1, "mode": mode, "number": number, "info": info, "look": FILTER, "movie_file": Path(src).name,
            "voices": [], "music": None, "beat": 0.5, "montage_seconds": HYPE_MONTAGE_SECONDS}
    files, said = [], []
    if mode == "hype":
        music = pick_music("hype", info.get("title"))
        if music:
            offset, beat = beat_grid(music, HYPE_MONTAGE_SECONDS)
            cut = workdir / "music.m4a"
            P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{offset:.3f}", "-t",
                   f"{HYPE_MONTAGE_SECONDS + 3:.1f}", "-i", str(Path(music).resolve()), "-c:a", "aac", "-b:a", "192k",
                   str(cut.resolve())], cwd=workdir)
            plan.update(music="music.m4a", beat=beat)
            files.append(cut)
        said.append((0.0, 1.0, info.get("title") or ""))
    else:
        for k, beat in enumerate(info["beats"]):
            if "say" not in beat:
                continue
            P.checkpoint()
            mp3 = workdir / f"voice{k:03}.mp3"
            length, words = speak(beat["say"], mp3, beat.get("mood"))
            plan["voices"].append({"beat": k, "file": mp3.name, "seconds": round(length, 3),
                                   "words": [[round(a, 3), round(b, 3), w] for a, b, w in words]})
            files.append(mp3)
            said.append((0.0, length, beat["say"]))
        music = pick_music("calm", number)
        if music:
            plan["music"] = "music" + Path(music).suffix.lower()
            files.append((Path(music), plan["music"]))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.writestr("plan.json", json.dumps(plan, ensure_ascii=False, indent=1))
        for f in files:
            src_f, name = f if isinstance(f, tuple) else (f, f.name)
            z.write(src_f, name)
    for f in files:
        if not isinstance(f, tuple):
            f.unlink(missing_ok=True)
    return said


MUSIC_KIND = {"recap": "recap", "character": "character", "hype": "hype"}


def render(mode, src, number, info, cues, out, workdir, total):
    """Makes one part of a storytelling mode. Returns lines for its caption quote."""
    if mode == "hype":
        return render_hype(src, number, info, cues, out, workdir, total)
    return render_story(src, number, info, cues, out, workdir, total, mode)


def cover_moment(info, video):
    """A second in the movie that shows the part well: about a third of the way into its footage."""
    if isinstance(info, dict):
        if info.get("payoff"):
            return float(info["payoff"][0]) + 1.0
        times = [b.get("at", b.get("keep")) for b in info.get("beats") or [] if b.get("at", b.get("keep")) is not None]
        if times:
            return float(times[len(times) // 3]) + 1.0
    return None


def clean_moment(src, at, tries=8, step=1.5):
    """The first second from `at` on (every `step` s) whose frame has no subtitle text on it, or None."""
    try:
        import numpy as np
        import tracking
    except ImportError:
        return None
    for k in range(tries):
        t = at + k * step
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
               "-an", "-sn", "-vf", f"{P.CROP['filter']}scale=480:-2,format=gray", "-f", "rawvideo", "-"]
        try:
            data = subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout
        except Exception:
            return None
        h = len(data) // 480
        if h < 50:
            return None
        if tracking.text_rows(np.frombuffer(data[:h * 480], dtype=np.uint8).reshape(h, 480)) is None:
            return t
    return None


COVER_W, COVER_H = 1080, 1920
BADGE_COLOR = (0xE1, 0x1D, 0x48)        # deep red (#E11D48) behind bold white text
BADGE_X, BADGE_Y = 64, 150              # top-left, under TikTok's top bar and above where faces usually are
BADGE_SIZE, BADGE_PAD, BADGE_RADIUS = 84, (40, 26), 26


def badge_text(number, badge=None):
    """The badge's words: badge, else THUMBNAIL_BADGE with {n} as the part number."""
    text = badge or THUMBNAIL_BADGE
    try:
        text = str(text).format(n=number)
    except (KeyError, IndexError, ValueError):
        text = str(text).replace("{n}", str(number))
    return text.upper()


def subject_x(src, at, pre):
    """Where the main person is across the frame at `at` (0..1, centre of the faces), or 0.5."""
    try:
        import numpy as np
        import tracking
        tracking.get_model()
        w = tracking.SAMPLE_W
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
               "-an", "-sn", "-vf", f"{pre}scale={w}:-2,format=bgr24", "-f", "rawvideo", "-"]
        data = subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout
        h = len(data) // (w * 3)
        faces = tracking.detections(np.frombuffer(data[:h * w * 3], dtype=np.uint8).reshape(1, h, w, 3))[0][0]
    except Exception:
        return 0.5
    if not faces:
        return 0.5
    view = (COVER_W / COVER_H) / (w / max(h, 1))          # share of the width the vertical crop keeps
    lo, hi = min(f[0] - f[1] / 2 for f in faces), max(f[0] + f[1] / 2 for f in faces)
    if hi - lo <= view * 0.9:                              # everyone fits: frame them all
        return (lo + hi) / 2
    return max(faces, key=lambda f: f[1])[0]               # else the biggest face


def badge_layer(text, workdir):
    """The badge as RGBA pixels (a rounded red box with the white text centred on it)."""
    import numpy as np
    (Path(workdir) / "cover_badge.txt").write_text(text, encoding="utf-8")
    cw, ch = 1000, BADGE_SIZE * 2
    data = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=black:s={cw}x{ch}",
         "-vf", f"drawtext=fontfile={P.FONT}:textfile=cover_badge.txt:expansion=none:fontsize={BADGE_SIZE}:"
                f"fontcolor=white:x=20:y=(h-th)/2,format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True, check=True, cwd=workdir, timeout=60).stdout
    ink = np.frombuffer(data[:cw * ch], dtype=np.uint8).reshape(ch, cw)
    rows, cols = np.nonzero(ink > 8)
    if not len(rows):
        raise RuntimeError("badge text drew nothing")
    ink = ink[rows.min():rows.max() + 1, cols.min():cols.max() + 1].astype(np.float32) / 255
    th, tw = ink.shape
    px, py = BADGE_PAD
    W, H, r = tw + px * 2, th + py * 2, BADGE_RADIUS
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32) + 0.5
    dx = np.maximum(np.maximum(r - xx, xx - (W - r)), 0)
    dy = np.maximum(np.maximum(r - yy, yy - (H - r)), 0)
    box = np.clip(r - np.sqrt(dx * dx + dy * dy) + 0.5, 0, 1)    # 1 inside, smooth (anti-aliased) corners
    out = np.zeros((H, W, 4), dtype=np.float32)
    out[..., :3] = BADGE_COLOR
    out[py:py + th, px:px + tw, :3] += (255 - out[py:py + th, px:px + tw, :3]) * ink[..., None]
    out[..., 3] = box
    return out


def make_cover(video, number, title, out, workdir, src=None, info=None, badge=None):
    """
    The part's thumbnail (1080x1920, top-quality JPEG): a clean frame from the movie itself (no captions,
    its own burned-in subtitles avoided or blurred), zoomed and cropped straight to fill the whole vertical
    screen, centred on the people in it, with a red rounded badge in the top-left corner: badge, or
    THUMBNAIL_BADGE ("PART n"). Without src (or a moment to take), the frame comes from the finished part.
    """
    import numpy as np
    workdir = Path(workdir)
    at = cover_moment(info, video) if src else None
    if at is None:
        src, at, pre = video, max(0.0, P.duration(video) * 0.3), ""
    else:
        clean = clean_moment(src, at)
        at, pre = (clean, P.CROP["filter"]) if clean is not None else (at, f"{P.CROP['filter']}{hide_old_subtitles(src)}")
    src = Path(src).resolve()
    cx = min(1.0, max(0.0, subject_x(src, at, pre)))
    # the crop's left edge: centre on cx, kept inside the picture
    crop = (f"{pre}scale={COVER_W}:{COVER_H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={COVER_W}:{COVER_H}:'max(0,min(iw-{COVER_W},iw*{cx:.4f}-{COVER_W // 2}))':'(ih-{COVER_H})/2',format=rgb24")
    data = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src),
                           "-frames:v", "1", "-an", "-sn", "-vf", crop, "-f", "rawvideo", "-"],
                          capture_output=True, check=True, cwd=workdir, timeout=120).stdout
    img = np.frombuffer(data[:COVER_W * COVER_H * 3], dtype=np.uint8).reshape(COVER_H, COVER_W, 3).astype(np.float32)
    layer = badge_layer(badge_text(number, badge), workdir)
    h, w = layer.shape[:2]
    w, h = min(w, COVER_W - BADGE_X * 2), min(h, COVER_H - BADGE_Y)
    layer = layer[:h, :w]
    spot = img[BADGE_Y:BADGE_Y + h, BADGE_X:BADGE_X + w]
    a = layer[..., 3:]
    spot[:] = spot * (1 - a) + layer[..., :3] * a
    raw = workdir / "cover.rgb"
    raw.write_bytes(np.clip(img + 0.5, 0, 255).astype(np.uint8).tobytes())
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{COVER_W}x{COVER_H}", "-i", str(raw), "-frames:v", "1", "-pix_fmt", "yuvj444p",
           "-q:v", "1", "-qmin", "1", str(Path(out).resolve())], cwd=workdir)
    raw.unlink(missing_ok=True)
    return Path(out)


def describe(mode, info):
    """One line for the parts list."""
    if mode == "hype":
        return f"{info.get('title') or 'Hype edit'}: setup {P.fmt_time(info['setup'][0])}, payoff {P.fmt_time(info['payoff'][0])}, " \
               f"{len(info['montage'])} montage shots"
    says = sum(1 for b in info["beats"] if "say" in b)
    words = sum(len(b["say"].split()) for b in info["beats"] if "say" in b)
    return f"{info.get('title') or 'Narrated'} ({says} narrated beats, about {math.ceil(words / WORDS_PER_SECOND)}s of voice)"


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--get-music"]:
        download_music()
