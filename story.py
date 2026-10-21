"""
The storytelling modes, made from the movie's subtitles:

  recap      "Movie recap": a narrator tells the whole story over the matching scenes, in Part 1, 2, 3...
  character  "Character story": the same, but following one character's arc (CHARACTER, or the most
             important character when that's blank), staying faithful to what they actually do.
  hype       "Hype edit": the character gets disrespected or doubted, answers it in style, then a
             beat-synced montage (white flash cuts, three-panel diagonal mask slides, slow motion)
             over music from assets/music.

A small AI model running on GitHub's computer (Ollama, STORY_MODEL, no API key or account) reads the
timed subtitles and plans the parts: which scenes, and what the narrator says. A small model can't
take a whole movie in at once, so it first writes short scene notes for each stretch of subtitles,
then plans the story from those notes. Set STORY_MODEL to a Claude model (and add the secret
ANTHROPIC_API_KEY) for better writing. The voice is Microsoft's free neural text-to-speech
(edge-tts); everything else is ffmpeg. The whole picture is always shown (never zoomed in), with
the film look (FILTER) on top. If the model can't be reached, the narrated modes are skipped (with
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

FILTER, FILTER_STRENGTH, CHARACTER, VOICE, SPEED = "vivid", 0.4, "", "en-US-GuyNeural", "+8%"
RECAP_PART_SECONDS, RECAP_PARTS, MUSIC, MUSIC_VOLUME = 90.0, 0, True, 0.12
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
    SPEED = env.get("NARRATOR_SPEED", "+8%")
    RECAP_PART_SECONDS = float(env.get("RECAP_PART_SECONDS", "90"))
    RECAP_PARTS = int(float(env.get("RECAP_PARTS", "0")))            # 0 = automatic (at least 3)
    MUSIC = env.get("MUSIC", "on").strip().lower() not in OFF
    MUSIC_VOLUME = float(env.get("MUSIC_VOLUME", "0.12"))            # under the narration
    HYPE_COUNT = int(float(env.get("HYPE_COUNT", "1")))
    HYPE_MONTAGE_SECONDS = float(env.get("HYPE_MONTAGE_SECONDS", "15"))
    MODEL = env.get("STORY_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"
    global VOICE_ENGINE, INTENSITY
    VOICE_ENGINE = env.get("NARRATOR_ENGINE", "chatterbox").strip().lower()
    INTENSITY = float(env.get("NARRATOR_INTENSITY", "0.8"))   # local (Ollama) or a claude-... model
    global EDITOR
    EDITOR = env.get("STORY_EDITOR", "ffmpeg").strip().lower()       # ffmpeg (cut + narrate here) or filmcraft (laptop)


EDITOR = "ffmpeg"
PACK_SUFFIX = ".editpack.zip"


MUSIC_DIR = Path(__file__).resolve().parent / "assets" / "music"
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus"}
DUCK = 0.18          # movie sound under the narrator
WORDS_PER_SECOND = 2.6
FPS = 30

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
        step = max(30.0, total / MAX_FRAMES)
        t = step / 2
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
STORY_SCHEMA = {"type": "object", "required": ["character", "parts"], "properties": {
    "character": {"type": "string"},
    "parts": {"type": "array", "items": {"type": "object", "required": ["title", "beats"], "properties": {
        "title": {"type": "string"},
        "beats": {"type": "array", "items": {"type": "object", "required": ["say", "at"], "properties": {
            "say": {"type": "string"}, "at": {"type": "number"}, "until": {"type": "number"}}}}}}}}}
SPAN = {"type": "array", "items": {"type": "number"}}
HYPE_SCHEMA = {"type": "object", "required": ["character", "edits"], "properties": {
    "character": {"type": "string"},
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
    return _PLOTS[name]


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


def part_count(total):
    """The fewest parts to aim for: at least 5, more for a long movie (the writer may use more still)."""
    return max(MIN_STORY_PARTS, RECAP_PARTS or round(total / 900))


def story_prompt(name, total, text, mode, who):
    n = part_count(total)
    words = int(RECAP_PART_SECONDS * WORDS_PER_SECOND * 0.8)
    focus = ("Tell the whole movie's story from start to finish." if mode == "recap" else
             f"Follow {'the character ' + who if who else 'the single most important character (name them in the character field)'} "
             "only: use scenes they are in or that are directly about them, and tell their arc faithfully (what they "
             "want, what they do, how they change). Mention other plot only as brief context, and don't drift into "
             "other characters' storylines.")
    return f"""Movie: {name}
Length: {int(total)} seconds

{focus}

Make at least {n} parts, each about {int(RECAP_PART_SECONDS)} seconds when read aloud (about {words} words of
narration per part), in story order. Use as many more parts as the story needs: never squeeze or skip important
story to fit a number of parts.

Voice: a funny, intense recap narrator: punchy, dramatic, a little sarcastic, with jokes about the characters'
choices, but every fact true to the movie. Present tense, third person, plain spoken English.

Flow, which matters most:
- Part 1 opens with a hook.
- Every part except the last ends on a real cliffhanger: stop right before a reveal or a decision, and end with a
  line that makes people need the next part (a question, a threat, "and that's when..."). Never end a part on a
  calm moment or mid-thought.
- Each new part picks up exactly where the last one stopped. Never recap or repeat what earlier parts said.
- When the story jumps in time or place, the first sentence after the jump bridges it ("Meanwhile, across
  town...", "Three days later...", "But back at the house..."), so the viewer never feels a sudden cut.

Each part is a list of beats. A narration beat has "say" (one or two sentences) and "at" (the second in the movie
where matching footage starts); that footage plays, with its own sound turned down, while the sentence is read,
so pick a moment that shows what the sentence says. One to three times per part, use an original-moment beat
instead: "say" is empty, and "at" and "until" mark a 3 to 8 second stretch where a key line is spoken, played
with its own sound. Only use seconds that appear below.

Answer with this JSON shape:
{{"character": "<who it follows; empty for a whole-movie recap>",
  "parts": [{{"title": "<3 to 6 words>",
             "beats": [{{"say": "<narration>", "at": <second>}},
                       {{"say": "", "at": <start second>, "until": <end second>}}]}}]}}

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
Use only seconds that appear below. The title is their name or nickname, 1 to 3 words, for big on-screen text.

Answer with this JSON shape:
{{"character": "<name>",
  "edits": [{{"title": "<name>", "setup": [<start>, <end>], "payoff": [<start>, <end>],
             "montage": [[<start>, <end>], ...]}}]}}

{text}
"""


def _num(v, total, default=0.0):
    try:
        return max(0.0, min(float(v), max(0.0, total - 1.0)))
    except (TypeError, ValueError):
        return default


def clean_story(data, total):
    """Checks the writer's plan: valid times, at least 2 parts. Returns [(start, end, part info)]."""
    parts = []
    for p in data.get("parts") or []:
        beats = []
        for b in p.get("beats") or []:
            if not isinstance(b, dict):
                continue
            if str(b.get("say") or "").strip() and b.get("at") is not None:
                beats.append({"say": str(b["say"]).strip(), "at": round(_num(b["at"], total), 2)})
            elif b.get("keep") is not None or (b.get("at") is not None and b.get("until") is not None):
                a = _num(b["keep"] if b.get("keep") is not None else b["at"], total)
                e = _num(b.get("until", a + 5), total, a + 5)
                e = min(max(e, a + 2.0), a + 10.0, total)
                if e - a >= 1.0:
                    beats.append({"keep": round(a, 2), "until": round(e, 2)})
        if any("say" in b for b in beats):
            parts.append({"title": str(p.get("title") or "").strip()[:60], "beats": beats})
    if len(parts) == 1 and len(parts[0]["beats"]) >= 6:          # one long part: share it out into two
        beats = parts[0]["beats"]
        half = len(beats) // 2
        parts = [{"title": parts[0]["title"], "beats": beats[:half]}, {"title": "", "beats": beats[half:]}]
    if len(parts) < 2:
        raise Skip("The story plan didn't have enough to make at least 2 parts.")
    who = str(data.get("character") or "").strip()
    out = []
    for p in parts:
        times = [b.get("at", b.get("keep")) for b in p["beats"]]
        p["character"] = who
        out.append((min(times), max(times) + 8.0, p))
    return out


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
    ready = using_claude() or local_ready()
    if mode == "hype":
        if ready and cues:
            try:
                return clean_hype(ask(SYSTEM, hype_prompt(name, total, source(name, cues), who), HYPE_SCHEMA), total)
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
    return clean_story(ask(SYSTEM, story_prompt(name, total, source(name, cues), mode, who), STORY_SCHEMA), total)


# ---------------------------------------------------------------- the narrator's voice
VOICE_ENGINE = os.environ.get("NARRATOR_ENGINE", "chatterbox")   # chatterbox (expressive, online) or edge
INTENSITY = float(os.environ.get("NARRATOR_INTENSITY", "0.8"))     # Chatterbox exaggeration: 0.5 calm ... 1 wild
CHATTERBOX_SPACE = "ResembleAI/Chatterbox"
_CHATTER = {"client": None, "ref": None, "fails": 0}


def speak(text, mp3):
    """Reads text aloud into mp3: Chatterbox's funny, intense delivery when it's reachable (free online model,
    in the voice of NARRATOR_VOICE), Microsoft's voice otherwise. Returns (seconds, [(start, end, word)])."""
    if VOICE_ENGINE == "chatterbox" and _CHATTER["fails"] < 2:
        try:
            return speak_chatterbox(text, mp3)
        except Exception as e:
            _CHATTER["fails"] += 1
            print(f"  (Chatterbox didn't answer{', using the standard voice from now on' if _CHATTER['fails'] >= 2 else ''}: {e})",
                  flush=True)
    return speak_edge(text, mp3)


def speak_chatterbox(text, mp3):
    """One line from Chatterbox (Resemble AI's open model, run on its free Hugging Face page)."""
    from gradio_client import Client, handle_file
    if _CHATTER["client"] is None:
        _CHATTER["client"] = Client(CHATTERBOX_SPACE, hf_token=os.environ.get("HF_TOKEN") or None, verbose=False)
    if _CHATTER["ref"] is None:                  # a sample of the narrator's voice for Chatterbox to copy
        ref = Path("narrator_voice.mp3").resolve()
        speak_edge("Listen. This man is about to make the worst decision of his entire life, and honestly? I respect it.",
                   ref)
        _CHATTER["ref"] = ref
    wav = _CHATTER["client"].predict(text, handle_file(str(_CHATTER["ref"])), INTENSITY, 0.8, 0, 0.3, True,
                                     api_name="/generate_tts_audio")
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k",
           str(Path(mp3).resolve())])
    _CHATTER["fails"] = 0
    length = P.duration(mp3)
    return length, word_times(text, [], length)     # no word timings: captions are shared out evenly


def speak_edge(text, mp3):
    """Microsoft's free neural voice (edge-tts), with exact word timings."""
    import asyncio
    import edge_tts

    async def go():
        try:
            com = edge_tts.Communicate(text, VOICE, rate=SPEED, boundary="WordBoundary")
        except TypeError:                       # older edge-tts: word timings come by default
            com = edge_tts.Communicate(text, VOICE, rate=SPEED)
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
ENC = ["-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k"]
_AUDIO = {}


@contextmanager
def classic():
    """These modes always show the whole picture (never the zoomed band)."""
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


def caption_filters(words, length, bottom, workdir, tag):
    """Narration captions: big chunks of 2-3 words, just under the picture."""
    out, y = [], (bottom + 40 if bottom < 1650 else 1500)
    for k, (a, b, text) in enumerate(chunks(words)):
        a, b = max(0.0, a), min(length, b)
        if b <= a:
            continue
        f = workdir / f"{tag}{k}.txt"
        f.write_text(text.upper(), encoding="utf-8")
        out.append(f",drawtext=fontfile={P.FONT}:textfile={f.name}:expansion=none:fontsize=72:fontcolor=white:"
                   f"borderw=6:bordercolor=black:x=(w-text_w)/2:y={y:.0f}:enable='between(t,{a:.3f},{b:.3f})'")
    return "".join(out)


def title_filter(text, workdir, until=1.4):
    """Big name title that flashes in at the start of the montage."""
    if not text:
        return ""
    f = workdir / "hypetitle.txt"
    f.write_text(text.upper(), encoding="utf-8")
    return (f",drawtext=fontfile={P.FONT}:textfile={f.name}:expansion=none:fontsize=130:fontcolor=white:"
            f"borderw=8:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2:alpha='min(1,t/0.12)':"
            f"enable='lt(t,{until:.2f})'")


def render_segment(src, start, length, out, workdir, voice=None, words=None, cues=None, flash=False,
                   slow=1.0, movie_volume=1.0, title="", bridge=False, ending=False):
    """One piece of a part: the movie from `start` for `length` seconds (whole picture, graded), with the
    narrator over it (voice), the movie's own subtitles (cues), a white flash in, or slow motion.
    bridge: a quick dip from black, so a jump to another scene doesn't feel like a sudden cut.
    ending: the last piece of a part eases out instead of stopping dead."""
    src_len = length / slow
    first = P.flip_filter(src, start, start + src_len).replace("[0:v]", f"[0:v]{P.CROP['filter']}{grade()}", 1)
    v = first + FRAME
    if slow != 1.0:
        v += f",setpts={slow:.3f}*PTS"
    with classic():
        bottom = P.movie_bottom(src)
        if words:
            v += caption_filters(words, length, bottom, workdir, "nar")
        if cues:
            part_cues = [(a - start, b - start, t) for a, b, t in cues if b > start and a < start + src_len]
            v += P.subtitle_filters(part_cues, length, bottom, workdir)
    v += title_filter(title, workdir)
    if flash:
        v += ",fade=t=in:st=0:d=0.16:color=white"
    if bridge:
        v += ",fade=t=in:st=0:d=0.3"
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
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", f"{v};{a}",
           "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}", *ENC, str(Path(out).resolve())], cwd=workdir)
    for f in list(workdir.glob("sub*.txt")) + list(workdir.glob("nar*.txt")):
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
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", f,
           "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}", *ENC, str(Path(out).resolve())], cwd=workdir)


def concat(segs, out, workdir):
    lst = workdir / "segments.txt"
    lst.write_text("".join(f"file '{Path(s).resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i",
           str(lst.resolve()), "-c", "copy", str(Path(out).resolve())], cwd=workdir)


_CREDIT = {"last": None}


def music_list():
    """The songs listed in assets/music/tracks.json: [{"file", "url", "kind", "credit"}]."""
    try:
        return json.loads((MUSIC_DIR / "tracks.json").read_text(encoding="utf-8")).get("tracks", [])
    except (OSError, ValueError):
        return []


def available_music():
    """[(file, credit line, kind)] for every song on this computer: the listed ones that were downloaded,
    plus any you dropped into assets/music yourself (kind "any", no credit line)."""
    if not MUSIC_DIR.is_dir():
        return []
    listed = {t["file"]: t for t in music_list() if t.get("file")}
    out = []
    for f in sorted(MUSIC_DIR.iterdir()):
        if f.suffix.lower() in AUDIO_EXT:
            t = listed.get(f.name, {})
            out.append((f, t.get("credit", ""), t.get("kind", "any")))
    return out


def pick_music(kind, seed=None):
    """A song for this part: "calm" under narration, "hype" for montages (or one of yours), or None.
    Remembers its credit line for the caption (see last_credit)."""
    _CREDIT["last"] = None
    if not MUSIC:
        return None
    songs = available_music()
    fits = [s for s in songs if s[2] in (kind, "any")] or songs
    if not fits:
        return None
    f, credit, _ = random.Random(seed).choice(fits)
    _CREDIT["last"] = credit or None
    return f


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


LIGHT_SWEEP = True
SWEEP_EVERY = 6.0          # seconds between light sweeps


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


def finish_part(segs, out, workdir, src, top_text, music=None, music_volume=None):
    """Joins the pieces, then adds the part-level touches in one last encode: the top label, the line,
    the cockroach, and quiet background music."""
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
        u = f"(-910+1800*clip((mod(t,{SWEEP_EVERY})-0.6)/1.2,0,1))"
        v += f"[pre0];[pre0][{n}:v]overlay=x='-960-{u}':y='-540+{u}':eval=frame"
        n += 1
    if FILTER == "vivid":                             # the vivid look's fixed sprinkle of tiny white specks
        extra += ["-loop", "1", "-framerate", str(FPS), "-t", f"{length:.3f}", "-i", str(specks_image(workdir))]
        v += f"[pre1];[pre1][{n}:v]overlay=0:0"
        n += 1
    if top_text:
        (workdir / "toptext.txt").write_text(top_text, encoding="utf-8")
        v += (f",drawtext=fontfile={P.FONT}:textfile=toptext.txt:expansion=none:"
              "fontsize=96:fontcolor=white:borderw=7:bordercolor=black:x=(w-text_w)/2:y=230")
    with classic():
        v += "[txt0]" + P.line_filter(src, length)
    v += roach[1] if roach else ";[txt]null[v]"
    a = "[0:a]anull[a]"
    if music:
        mi = 1 + extra.count("-i")
        extra = extra + ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
        a = (f"[{mi}:a]volume={MUSIC_VOLUME if music_volume is None else music_volume:.3f},afade=t=in:d=1.5,afade=t=out:st={max(0.0, length - 2):.2f}:d=2[mu];"
             f"[0:a][mu]amix=inputs=2:duration=first:normalize=0[a]")
    P.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", str(joined.resolve()), *extra,
           "-filter_complex", f"{v};{a}", "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}",
           "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", str(P.VIDEO_QUALITY),
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart",
           str(Path(out).resolve())], cwd=workdir)
    for f in [joined, *segs]:
        Path(f).unlink(missing_ok=True)


def render_story(src, number, info, cues, out, workdir, total):
    """A narrated part (Movie recap / Character story). Returns the narration lines (for captions)."""
    segs, said = [], []
    last_end = None          # where the previous piece's footage ended: a big jump gets a dip from black
    beats = info["beats"]
    for k, beat in enumerate(beats):
        P.checkpoint()
        seg = workdir / f"seg{k:03}.mp4"
        end_of_part = k == len(beats) - 1
        if "say" in beat:
            mp3 = workdir / f"say{k:03}.mp3"
            length, words = speak(beat["say"], mp3)
            length += 0.35 + (0.5 if end_of_part else 0)      # the cliffhanger line gets a beat to land
            at = max(0.0, min(beat["at"], total - length - 0.1))
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
    finish_part(segs, out, workdir, src, f"Part {number}", music=pick_music("calm", number))
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
           "-t", f"{length:.3f}", str(Path(out).resolve())], cwd=workdir)
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
            length, words = speak(beat["say"], mp3)
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


def render(mode, src, number, info, cues, out, workdir, total):
    """Makes one part of a storytelling mode. Returns lines for its caption quote."""
    if mode == "hype":
        return render_hype(src, number, info, cues, out, workdir, total)
    return render_story(src, number, info, cues, out, workdir, total)


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
