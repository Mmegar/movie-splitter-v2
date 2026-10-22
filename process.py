"""
TikTok part maker: runs on GitHub Actions (your laptop only sends and receives files).

How movies travel (no Google Drive):
    laptop helper  -- movie (in pieces under 2 GB) -->  draft release "incoming-<id>" on this repo
    this script    -- cuts it, sends each part as soon as it's made --> draft release "parts-<id>"
    laptop helper  -- downloads them into <output folder>/Ready to post/<movie>/<mode>/
Draft releases are only visible to people with write access to the repo (not public).
The uploaded movie is deleted once all parts are made. If cutting fails, it's kept and marked
"failed" (the laptop then moves the original into its "Failed" folder).

Every movie is made in each mode listed in MODES (default: the three storytelling modes, see story.py):
  recap       "Movie recap": a narrator tells the whole story over the matching scenes, in parts.
  character   "Character story": the same, following one character's arc.
  hype        "Hype edit": disrespected -> answers it in style -> beat-synced montage over music.
The older modes still work if you list them:
  highlights  Scores the whole video for "interesting" moments (loud audio,
              sudden sound spikes, fast scene cuts, lots of movement, busy
              dialogue) and keeps only the best stretches as Part 1, Part 2...
  all         "Whole movie": the whole video in back-to-back ~PART_SECONDS parts.
  stretch     "Best stretch": finds the most interesting STRETCH_MINUTES of the movie and cuts just
              that section into back-to-back ~STRETCH_PART_SECONDS parts.

Either way, every cut lands on a quiet moment (a pause) where possible, each part
is made vertical 9:16 (1080x1920) with a blurred background, gets "Part X" at the
top, and gets burned-in English subtitles: from a matching .srt if you added one,
otherwise made automatically from the clip's own audio with Whisper (speech
recognition), translated into English if the movie is in another language.
"""
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import previews
import settings
import status
import story
import tracking
import transport

# Settings come from config/settings.json (the iPhone app edits it). Any environment variable
# with the old v1 name (MODES, FLIP_EVERY, COCKROACH_SIZE, ...) still wins over the file.
SETTINGS = settings.apply_to_env()
story.bind(sys.modules[__name__])

READY = "Ready to post"

MODE_NAMES = {"highlights": "Highlights", "stretch": "Best stretch", "all": "Whole movie", "clips": "Custom clips",
              **story.MODE_NAMES}
MODES = [m.strip().lower() for m in os.environ.get("MODES", "recap,character,hype").split(",")
         if m.strip().lower() in MODE_NAMES and m.strip().lower() != "clips"] or ["recap"]
MODE = ""   # the mode being made right now (labels the notifications)
STRETCH_MINUTES = float(os.environ.get("STRETCH_MINUTES", "12"))           # "stretch" mode: how much of the movie
STRETCH_FROM = float(os.environ.get("STRETCH_FROM", "0.20"))   # "stretch" mode: only look from 20%...
STRETCH_TO = float(os.environ.get("STRETCH_TO", "0.75"))       # ...to 75% of the way through (no spoiling the ending)
STRETCH_PART_SECONDS = float(os.environ.get("STRETCH_PART_SECONDS", "75"))  # "stretch" mode: part length
PART_SECONDS = float(os.environ.get("PART_SECONDS", "120"))      # "all" mode: part length
EARLIEST = float(os.environ.get("CUT_EARLIEST", "25"))           # "all" mode: may cut this early...
LATEST = float(os.environ.get("CUT_LATEST", "15"))               # ...or this late, to land on a pause
HIGHLIGHT_SECONDS = float(os.environ.get("HIGHLIGHT_SECONDS", "75"))  # "highlights" mode: clip length
HIGHLIGHT_COUNT = int(os.environ.get("HIGHLIGHT_COUNT", "0"))    # 0 = automatic (about 1 per 10 min)
MIN_LAST_PART = 30
MIN_PARTS = 5        # every mode makes at least this many parts (when the movie is long enough)
MAX_EXTRA = float(os.environ.get("PART_MAX_EXTRA", "225"))  # a part may run this many seconds long to end well
GOOD_ENDING = 3.0   # an ending at least this good is taken as soon as one turns up
# cutting style, per movie: "classic" (whole picture, blurred background) or "zoom" (zoomed band that
# follows faces, longer parts, fewer of them). Chosen in the app per movie; default in Settings.
STYLE = "classic"
_CLASSIC = {}


def apply_style(style):
    """Sets the part lengths etc. for this movie's style."""
    global STYLE, STRETCH_PART_SECONDS, HIGHLIGHT_SECONDS, PART_SECONDS, MAX_EXTRA
    if not _CLASSIC:
        _CLASSIC.update(STRETCH_PART_SECONDS=STRETCH_PART_SECONDS, HIGHLIGHT_SECONDS=HIGHLIGHT_SECONDS,
                        PART_SECONDS=PART_SECONDS, MAX_EXTRA=MAX_EXTRA)
    STYLE = "zoom" if str(style or "").lower() in ("zoom", "zoomed", "tracking") else "classic"
    if STYLE == "zoom":
        part = float(SETTINGS.get("zoom_part_seconds", 210))
        STRETCH_PART_SECONDS = HIGHLIGHT_SECONDS = PART_SECONDS = part
        MAX_EXTRA = float(SETTINGS.get("zoom_part_max_extra", 90))
    else:
        STRETCH_PART_SECONDS, HIGHLIGHT_SECONDS = _CLASSIC["STRETCH_PART_SECONDS"], _CLASSIC["HIGHLIGHT_SECONDS"]
        PART_SECONDS, MAX_EXTRA = _CLASSIC["PART_SECONDS"], _CLASSIC["MAX_EXTRA"]
    return STYLE
FLIP = os.environ.get("FLIP", "none").strip().lower()             # none / mirror / upsidedown / both
FLIP_FILTERS = {"mirror": "hflip", "upsidedown": "vflip", "both": "hflip,vflip"}
# FLIP_EVERY: "25-30" = switch on a scene cut every 25 to 30 seconds (random each time); "30" = every 30s;
# "-1" = on every cut; "0" = flipped all the time
_fe = os.environ.get("FLIP_EVERY", "0").replace(" ", "")
_m = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", _fe)
FLIP_MIN, FLIP_MAX = (float(_m.group(1)), float(_m.group(2))) if _m else (float(_fe), float(_fe))
FLIP_EVERY = FLIP_MIN
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()   # phone notifications (ntfy app): GitHub secret NTFY_TOPIC
MAX_RUN_MINUTES = float(os.environ.get("MAX_RUN_MINUTES", "300"))  # don't start a new movie after this
STARTED = time.time()
COCKROACH = os.environ.get("COCKROACH", "on").strip().lower() not in ("off", "no", "0", "false")
COCKROACH_CHANCE = float(os.environ.get("COCKROACH_CHANCE", "1"))   # chance a part gets a cockroach run
COCKROACH_SIZE = int(os.environ.get("COCKROACH_SIZE", "60"))         # length in pixels (screen is 1080 wide)
LINE = os.environ.get("LINE", "on").strip().lower() not in ("off", "no", "0", "false")
LINE_OPACITY = float(os.environ.get("LINE_OPACITY", "0.45"))  # 1 = solid black, 0.2 = very faint
LINE_THICKNESS = int(os.environ.get("LINE_THICKNESS", "5"))
ASSETS = Path(__file__).resolve().parent / "assets"
AUTO_SUBS = os.environ.get("AUTO_SUBTITLES", "on").strip().lower() not in ("off", "no", "0", "false")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
FONT = os.environ.get("FONT_FILE", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
VIDEO_QUALITY = int(float(os.environ.get("VIDEO_QUALITY", "23")))   # x264 CRF: 18 = best/biggest ... 28 = smallest
TZ = ZoneInfo(SETTINGS["posting"]["timezone"])
WORK = Path("work")

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".webm", ".mpg", ".mpeg", ".ts", ".ogv"}

# ---------------------------------------------------------------- helpers
def run(cmd, **kw):
    """Runs a tool (ffmpeg), checking the app's Pause switch every half minute while it works, so a long
    step (like analysing a 2-hour movie) stops soon after Pause instead of running to the end."""
    print("+", " ".join(str(c) for c in cmd), flush=True)
    if CHECKPOINT is None:
        return subprocess.run(cmd, check=True, **kw)
    proc = subprocess.Popen(cmd, **kw)
    try:
        while True:
            try:
                code = proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                checkpoint()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if code:
        raise subprocess.CalledProcessError(code, cmd)
    return subprocess.CompletedProcess(cmd, code)


CHECKPOINT = None        # set while a movie is being cut: raises RunStopped when Pause is switched on
_LAST_CHECK = [0.0]


def checkpoint(force=False):
    """Checks for Pause at most every 30 seconds (or right away with force)."""
    if CHECKPOINT is None or (not force and time.time() - _LAST_CHECK[0] < 30):
        return
    _LAST_CHECK[0] = time.time()
    CHECKPOINT()


def ntfy_topic():
    """The ntfy name (GitHub secret NTFY_TOPIC); no notifications if it isn't set."""
    return "" if NTFY_TOPIC in ("", "-") else NTFY_TOPIC


def notify(title, message, tags=None, kind="info"):
    """
    Sends a phone notification through the ntfy app, and adds the same message to the
    iPhone app's event list (kind = started / planned / part_ready / finished / failed / ...).
    Never stops the run if it fails.
    """
    if MODE:
        title = f"[{MODE_NAMES.get(MODE, MODE)}] {title}"
    status.add_event(kind, title, message, urgent=kind in ("finished", "failed", "posted", "post_failed"))
    if not ntfy_topic():
        return
    body = json.dumps({"topic": ntfy_topic(), "title": title, "message": message, "tags": tags or []}).encode()
    try:
        req = urllib.request.Request("https://ntfy.sh/", data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"(notification not sent: {e})", flush=True)


from names import clean_name, safe  # noqa: E402  (shared with the laptop helper)


def fmt_time(s):
    s = int(round(s))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def has_audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return bool(out)


# ---------------------------------------------------------------- subtitles
def ts_to_sec(ts):
    h, m, rest = ts.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def sec_to_ts(s):
    ms = int(round(max(0.0, s) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def parse_srt(path):
    if not path:
        return []
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"\s*([\d:,.]+)\s*-->\s*([\d:,.]+)", line)
            if m:
                body = "\n".join(lines[i + 1:]).strip()
                if body:
                    cues.append((ts_to_sec(m.group(1)), ts_to_sec(m.group(2)), body))
                break
    return cues


def write_part_srt(cues, start, end, out_path):
    """Keeps only lines inside this part and shifts them to start at 0."""
    n, out = 0, []
    for a, b, body in cues:
        if b <= start or a >= end:
            continue
        n += 1
        out.append(f"{n}\n{sec_to_ts(a - start)} --> {sec_to_ts(min(b, end) - start)}\n{body}\n")
    if not out:
        return False
    Path(out_path).write_text("\n".join(out), encoding="utf-8")
    return True


# ---------------------------------------------------------------- captions (free, no AI)
CAPTIONS_ON = os.environ.get("CAPTIONS", "on").strip().lower() not in ("off", "no", "0", "false")

CAPTION_HOOKS = [
    "Wait for it… 😳",
    "Nobody talks about this part 👀",
    "I wasn't ready for this 😭",
    "Watch till the end 🤯",
    "This scene lives in my head rent free",
    "POV: you finally found something worth watching 🍿",
    "Tell me you saw that coming 👇",
    "The way I gasped 😮",
    "Bro did NOT think this through 💀",
    "Rate this scene 1-10 👇",
    "Sound on for this one 🔊",
    "I've watched this 10 times already 🔁",
    "Things you notice on the second watch 👀",
    "This is why I don't trust anyone 😶",
    "Okay but why is this so good",
    "Stop scrolling, you need to see this ✋",
    "Not me staying up till 3am for this 🌙",
    "Plot twist incoming 🌀",
    "Tag someone who needs to see this 👇",
    "The ending of this part 😳",
    "Me pretending I understood everything 🤡",
    "Who else remembers this? 🙋",
    "This deserves way more attention",
    "Comment if you know what happens next 👇",
    "Hardest scene ever? 🔥",
    "The tension in this one 😬",
    "Save this for later 📌",
    "I need answers 😩",
    "Just one more part… said everyone 😅",
    "Bet you can't watch just one 😏",
    "Late night watch 🍿🌙",
    "No way this actually happened 😮",
    "The vibes are unmatched ✨",
    "Lowkey the best part 🤫",
    "Pause at the right moment 😂",
    "Main character energy 😎",
    "Can't believe I almost skipped this",
    "This hits different at night 🌌",
    "Rewatching this hits even harder",
    "Drop a 🔥 if you're still watching",
]
CAPTION_ENDINGS = [
    "Part {next} is up next 👀",
    "Follow so you don't miss Part {next} 🔔",
    "Part {next} coming soon ⏳",
    "Should I post Part {next}? 👇",
    "Comment 'more' for Part {next} 👇",
]
CAPTION_LAST = [
    "That's the last part. Follow for more 🍿",
    "Final part! What did you think? 👇",
    "And that's a wrap 🎬 Follow for the next one",
]
HASHTAG_SETS = [
    "#fyp #foryou #viral #movie #movieclips",
    "#fyp #foryoupage #movies #film #mustwatch",
    "#viral #fyp #movietok #filmtok #scene",
    "#foryou #trending #movie #cinema #fypシ",
    "#fyp #movieclip #filmclips #viralvideo #watch",
]


CAPTION_STYLE = os.environ.get("CAPTION_STYLE", "goofy").strip().lower()
GOOFY_HOOKS = [
    "POV: you said 'one more part' 4 hours ago 🤡",
    "bro woke up and chose violence 💀",
    "the way my jaw hit the floor 🫠",
    "nobody: \nthis movie: 🤯",
    "me acting like I saw that coming (I did not) 🙈",
    "this scene owes me rent, it lives in my head 🏠",
    "my last brain cell watching this: 🧠💨",
    "plot armor? never heard of her 🛡️",
    "who wrote this and are they okay 😭",
    "sound ON. trust me. 🔊",
    "I screamed. my neighbours screamed. 📢",
    "the side eye at the end 👀",
    "Rate the chaos 1 to 🔥🔥🔥",
    "this is why I have trust issues 😶‍🌫️",
    "watching this instead of sleeping again 🌚",
    "bestie made it worse 😭",
    "the audacity. the AUDACITY. 😤",
    "okay who let them cook 🧑‍🍳",
    "that's not a plan, that's a cry for help 🆘",
    "if this happened to me I'd move countries ✈️",
    "the confidence of this man 😎",
    "the plot thickens like gravy 🍲",
    "main character energy, side character luck 🍀",
    "this movie said 'hold my popcorn' 🍿",
    "explain this to me like I'm 5 🧸",
    "brb rewatching this 47 times 🔁",
    "a masterclass in bad decisions 🎓",
    "I'm not crying, my eyes are sweating 🥲",
    "the jump I just did 🦘",
    "you're not ready for the last 10 seconds ⏳",
    "this is cinema and I'm tired of pretending it's not 🎬",
    "red flag? that's the whole parade 🚩",
    "tell me you're in trouble without telling me 🫣",
    "therapist: it can't hurt you. this scene: 😈",
    "why is nobody talking about this 🤨",
    "the vibes went from 0 to 100 real quick 📈",
    "manifesting this level of chaos 🌀",
    "I paused 3 times to process this 🧐",
]
GOOFY_QUOTE = [
    "'{q}' 💀 I can't",
    "when they said '{q}' I felt that 😭",
    "'{q}' is my new personality 😤",
    "the way they said '{q}' 🫠",
    "'{q}' and then THIS happened 😳",
    "me to my group chat: '{q}' 📱",
]
GOOFY_NEXT = [
    "Part {next} is even more unhinged 👀",
    "follow or you'll miss Part {next} (I warned you) 🔔",
    "Part {next} drops soon, stay hydrated 💧",
    "comment 'MORE' if you need Part {next} 👇",
    "Part {next}? only if this gets 100 comments 😏",
    "Part {next} is where it gets wild 🌪️",
]
GOOFY_LAST = [
    "that's the end. I need a nap 😮‍💨 follow for the next movie 🍿",
    "FINAL part. how are we feeling? 👇",
    "and that's a wrap 🎬 drop the next movie I should do 👇",
    "the ending. no words. just 🫠",
]
GOOFY_TAGS = [
    "#fyp #movieclips #foryou #filmtok #unhinged",
    "#fyp #moviescenes #viral #cinema #plottwist",
    "#foryoupage #movietok #fyp #chaos #mustwatch",
    "#fyp #movies #funny #viral #moviemoments",
    "#fyp #filmclips #foryou #whatdidijustwatch #movie",
]


def pick_quote(part_cues):
    """A short, punchy subtitle line from the part (questions and shouts first), or None."""
    lines = []
    for _, _, t in part_cues or []:
        t = re.sub(r"\s+", " ", str(t)).strip().strip("-").strip()
        if 8 <= len(t) <= 60 and not re.search(r"[\[\]♪]", t):
            lines.append(t)
    if not lines:
        return None
    loud = [t for t in lines if t.endswith(("?", "!"))]
    return random.choice(loud or lines).replace("'", "’")


def part_word(mode=None):
    """What a part is called on the video, in captions and notifications: highlights are "Highlight N",
    hype edits "Edit N"."""
    return {"highlights": "Highlight", "hype": "Edit"}.get(mode if mode is not None else MODE, "Part")


def make_captions(count, quotes=None, style=None, word="Part", movie=None):
    """One caption per part: a hook (sometimes a quote from the part), a 'next part' line, and hashtags.
    No repeated hooks within a movie. style: goofy (default) or plain."""
    style = (style or CAPTION_STYLE or "goofy").lower()
    goofy = style != "plain"
    pool = GOOFY_HOOKS if goofy else CAPTION_HOOKS
    hooks = random.sample(pool, k=min(count, len(pool)))
    while len(hooks) < count:
        hooks.append(random.choice(pool))
    quotes = list(quotes or []) + [None] * count
    out = []
    for i in range(1, count + 1):
        hook = hooks[i - 1]
        q = quotes[i - 1]
        if goofy and q and random.random() < 0.5:
            hook = random.choice(GOOFY_QUOTE).format(q=q)
        if goofy:
            end = random.choice(GOOFY_NEXT).format(next=i + 1) if i < count else random.choice(GOOFY_LAST)
            tags = random.choice(GOOFY_TAGS)
        else:
            end = (random.choice(CAPTION_ENDINGS).format(next=i + 1) if i < count else random.choice(CAPTION_LAST))
            tags = random.choice(HASHTAG_SETS)
        if word != "Part":
            end = end.replace("Part ", f"{word} ")
        out.append(f"{hook}\n{word} {i} 🎬 · {end}\n{movie or tags}")
    return out


def movie_tags(name):
    """At most 5 hashtags from the movie's title and cast (story.movie_hashtags), or None for the stock sets."""
    try:
        return story.movie_hashtags(name) or None
    except Exception as e:
        print(f"(no movie hashtags: {e})", flush=True)
        return None


def make_clip_caption(quote=None, style=None):
    """Caption for a single custom clip (no 'Part' line)."""
    goofy = (style or CAPTION_STYLE or "goofy").lower() != "plain"
    if goofy:
        hook = random.choice(GOOFY_QUOTE).format(q=quote) if quote and random.random() < 0.6 else random.choice(GOOFY_HOOKS)
        return f"{hook}\n{random.choice(GOOFY_TAGS)}"
    return f"{random.choice(CAPTION_HOOKS)}\n{random.choice(HASHTAG_SETS)}"


# ---------------------------------------------------------------- automatic subtitles (Whisper)
_WHISPER = None
SUB_INFO = {"lang": None, "note": None}   # per movie: spoken language, and what kind of subs were used


def whisper_model():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        print(f"Loading the Whisper '{WHISPER_MODEL}' speech model...", flush=True)
        _WHISPER = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _WHISPER


def words_to_lines(words, max_chars=42, max_secs=3.5, max_gap=0.7):
    """Groups Whisper's timed words into short, readable subtitle lines."""
    cues, cur = [], []
    def flush():
        if cur:
            text = "".join(w.word for w in cur).strip()
            if text:
                cues.append((cur[0].start, max(cur[-1].end, cur[0].start + 0.6), text))
            cur.clear()
    for w in words:
        if cur and (len("".join(x.word for x in cur) + w.word) > max_chars
                    or w.end - cur[0].start > max_secs or w.start - cur[-1].end > max_gap):
            flush()
        cur.append(w)
        if w.word.strip()[-1:] in ".?!":
            flush()
    flush()
    return cues


WHISPER_OPTS = dict(vad_filter=True, vad_parameters=dict(threshold=0.3, min_speech_duration_ms=150),
                    word_timestamps=True, condition_on_previous_text=False, beam_size=5)


def load_wav(src, wav, start=None, length=None):
    """Extracts 16 kHz mono audio and returns it as numbers Whisper can use."""
    import numpy as np
    import wave
    cut_opts = (["-ss", f"{start:.3f}", "-t", f"{length:.3f}"] if start is not None else [])
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *cut_opts, "-i", str(src.resolve()),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    try:
        with wave.open(str(wav)) as wf:   # read the audio ourselves (avoids a PyAV version clash)
            return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        wav.unlink(missing_ok=True)


def transcribe(audio, offset=0.0):
    """English subtitle lines for this audio (translated if needed), timed from 0 = start of audio."""
    model = whisper_model()
    lang = SUB_INFO["lang"]
    if not lang:
        sample = audio[int(len(audio) * 0.2):int(len(audio) * 0.2) + 16000 * 30]   # 30s from 20% in
        if len(sample) < 16000 * 5:
            sample = audio
        _, info = model.transcribe(sample, **WHISPER_OPTS)
        if info.language_probability >= 0.6:
            lang = SUB_INFO["lang"] = info.language
            print(f"  spoken language: {lang} ({info.language_probability:.0%} sure)", flush=True)
    if lang and lang != "en":
        segs, _ = model.transcribe(audio, language=lang, task="translate", **WHISPER_OPTS)
    else:
        segs, _ = model.transcribe(audio, language="en", **WHISPER_OPTS)
    words = []
    for seg in segs:                       # segments come one at a time, so Pause is noticed mid-way
        words += seg.words or []
        checkpoint()
    cues = words_to_lines(words)
    SUB_INFO["note"] = SUB_INFO["note"] or (
        f"translated to English from '{lang}'" if lang and lang != "en" else "made automatically (English)")
    print(f"  subtitles: {len(cues)} lines", flush=True)
    for a, b, text in cues:
        print(f"    {fmt_time(offset + a)}  {text}", flush=True)
    return cues


def auto_subs(src, start, end, workdir):
    """Subtitles for one part, timed from 0 = start of the part."""
    return transcribe(load_wav(src, workdir / "part.wav", start, end - start), offset=start)


def auto_subs_full(src, workdir):
    """Subtitles for the whole movie at once (used when the whole movie is being cut anyway)."""
    print("Making subtitles for the whole movie...", flush=True)
    cues = transcribe(load_wav(src, workdir / "full.wav"))
    SUB_INFO["full"] = True
    return cues


# ---------------------------------------------------------------- analysis
def analyse(path, workdir):
    """
    One pass over the video (at low resolution, so it's quick).
    Returns per-second lists: loudness (dB), motion (0-1), cuts (count),
    plus the middle of every pause in the audio.
    """
    total = duration(path)
    n = int(total) + 1
    scene_file, audio_file = workdir / "scene.txt", workdir / "audio.txt"
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(path.resolve()),
           "-map", "0:v:0",
           "-vf", f"fps=5,scale=192:-2,select='gte(scene,0)',metadata=print:file={scene_file.name}"]
    audio = has_audio(path)
    if audio:
        cmd += ["-map", "0:a:0",
                "-af", f"aresample=16000,asetnsamples=n=8000,astats=metadata=1:reset=1,"
                       f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={audio_file.name}"]
    cmd += ["-f", "null", "-"]
    print("Analysing the whole video for interesting moments...", flush=True)
    run(cmd, cwd=workdir)

    motion = [0.0] * n
    motion_count = [0] * n
    cuts = [0] * n
    t = None
    for line in scene_file.read_text().splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m and t is not None and int(t) < n:
            s = float(m.group(1))
            motion[int(t)] += s
            motion_count[int(t)] += 1
            if s > 0.35:
                cuts[int(t)] += 1
    motion = [motion[i] / motion_count[i] if motion_count[i] else 0.0 for i in range(n)]

    loud = [-90.0] * n
    if audio and audio_file.exists():
        sums, counts = [0.0] * n, [0] * n
        t = None
        for line in audio_file.read_text().splitlines():
            m = re.search(r"pts_time:([\d.]+)", line)
            if m:
                t = float(m.group(1))
                continue
            m = re.search(r"RMS_level=(-?[\d.]+|-inf)", line)
            if m and t is not None and int(t) < n:
                v = -90.0 if m.group(1) == "-inf" else max(-90.0, float(m.group(1)))
                sums[int(t)] += v
                counts[int(t)] += 1
        loud = [sums[i] / counts[i] if counts[i] else -90.0 for i in range(n)]

    return total, loud, motion, cuts, quiet_moments(path) if audio else []


def quiet_moments(path):
    """Middle of every pause (>= 0.4s below -35dB) in the audio, in seconds."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", res.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", res.stderr)]
    QUIET_LEN[:] = [((s + e) / 2, e - s) for s, e in zip(starts, ends)]
    return [(s + e) / 2 for s, e in zip(starts, ends)]


QUIET_LEN = []     # (middle of pause, pause length) from the last quiet_moments() call
ENDINGS = []       # (time, how good an ending it is), built per movie by build_endings()


def build_endings(cues, cuts):
    """
    Scores every possible place to end a part. A good ending is a pause, after someone
    finishes a sentence, ideally where the scene also changes.
    """
    cut_times = [i + 0.5 for i, c in enumerate(cuts) if c]
    sentence_ends = [(a[1] + b[0]) / 2 for a, b in zip(cues, cues[1:])
                     if b[0] - a[1] >= 0.3 and a[2].rstrip()[-1:] in ".?!"]
    points = {}
    for t, length in QUIET_LEN:
        points[round(t, 1)] = min(length, 2.0)                       # longer pause = better
    for t in sentence_ends:
        points[round(t, 1)] = points.get(round(t, 1), 0.5) + 2.0     # finished sentence = much better
    for t in cut_times:
        points.setdefault(round(t, 1), 0.3)                          # a plain scene change is okay
    for t in list(points):
        if any(abs(t - c) <= 1.0 for c in cut_times):
            points[t] += 1.5                                         # pause + scene change = great
    ENDINGS[:] = sorted(points.items())


def best_end(target, before, after):
    """
    Where to end a part aimed at `target`: the first really good ending (finished sentence /
    clear pause / scene change) from target-before onwards, up to target+after. If there's
    no really good one, the best one nearby.
    """
    window = [(t, q) for t, q in ENDINGS if target - before <= t <= target + after]
    if not window:
        return target
    for t, q in window:
        if q >= GOOD_ENDING:
            return t
    near = [(t, q) for t, q in window if t <= target + 30] or window
    return max(near, key=lambda tq: tq[1] - 0.04 * abs(tq[0] - target))[0]


def subtitle_gaps(cues):
    return [(a[1] + b[0]) / 2 for a, b in zip(cues, cues[1:]) if b[0] - a[1] >= 0.3]


def rank01(values):
    """Turns raw numbers into 0..1 by rank, so loud films and quiet films score fairly."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    denom = max(1, len(values) - 1)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = ((i + j) / 2) / denom
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def moving_avg(values, radius):
    out, s = [], 0.0
    pref = [0.0]
    for v in values:
        s += v
        pref.append(s)
    n = len(values)
    for i in range(n):
        a, b = max(0, i - radius), min(n, i + radius + 1)
        out.append((pref[b] - pref[a]) / (b - a))
    return out


def interest_scores(loud, motion, cuts, cues):
    n = len(loud)
    # sudden sound: how much louder this second is than the 30s around it
    background = moving_avg(loud, 15)
    spike = [max(0.0, loud[i] - background[i]) for i in range(n)]
    cut_rate = moving_avg([float(c) for c in cuts], 3)
    dialogue = [0.0] * n
    for a, b, body in cues:
        chars = len(body)
        for s in range(int(a), min(n, int(b) + 1)):
            dialogue[s] += chars / max(1.0, b - a)

    features = [(rank01(loud), 0.30), (rank01(spike), 0.20), (rank01(motion), 0.20), (rank01(cut_rate), 0.15)]
    if cues:
        features.append((rank01(dialogue), 0.15))
    total_w = sum(w for _, w in features)
    score = [sum(f[i] * w for f, w in features) / total_w for i in range(n)]
    # silence is never interesting
    score = [0.0 if loud[i] < -60 else score[i] for i in range(n)]
    return score


def snap(t, candidates, before, after):
    window = [c for c in candidates if t - before <= c <= t + after]
    return min(window, key=lambda c: abs(c - t)) if window else t


def choose_highlights(total, score, candidates):
    length = min(HIGHLIGHT_SECONDS, total)
    count = HIGHLIGHT_COUNT or (max(MIN_PARTS, min(6, round(total / 1800))) if STYLE == "zoom"
                                else max(MIN_PARTS, min(12, round(total / 600))))
    count = max(count, MIN_PARTS)
    L = int(length)
    # ignore the first 1% (logos) and the last 4% (end credits) of long videos
    lo = int(total * 0.01) if total > 1200 else 0
    hi = int(total * 0.96) if total > 1200 else int(total)
    pref = [0.0]
    for v in score:
        pref.append(pref[-1] + v)
    windows = []
    for s in range(lo, max(lo + 1, hi - L + 1)):
        e = min(len(score), s + L)
        windows.append(((pref[e] - pref[s]) / max(1, e - s), s))
    windows.sort(reverse=True)

    picked = []
    gap = 10 + MAX_EXTRA          # room for a clip to run long without overlapping the next
    for avg, s in windows:
        if len(picked) >= count:
            break
        if all(s + L + gap <= ps or s >= ps + L + gap for _, ps in picked):
            picked.append((avg, s))
    clips = []
    for avg, s in sorted(picked, key=lambda p: p[1]):
        a = snap(float(s), candidates, 8, 3)
        b = best_end(float(s + L), 5, MAX_EXTRA)
        clips.append((max(0.0, a), min(total, b), avg))
    return clips


def choose_cuts(total, candidates, part=None, begin=0.0):
    """Back-to-back parts of ~part seconds from begin to total. Returns [(start, end, None)]."""
    part = part or PART_SECONDS
    if STYLE == "zoom":               # long parts: share the time out evenly (no short leftover part)
        part = (total - begin) / max(1, round((total - begin) / part))
    early, late = min(EARLIEST, part * 0.15, 10.0), MAX_EXTRA
    candidates = sorted(candidates)
    cuts, pos = [], begin
    while total - pos > part + late:
        target = pos + part
        cut = best_end(target, early, late) if ENDINGS else snap(target, candidates, early, late)
        cuts.append((pos, cut, None))
        pos = cut
    if cuts and total - pos < (part * 0.6 if STYLE == "zoom" else min(MIN_LAST_PART, part * 0.5)):
        cuts[-1] = (cuts[-1][0], total, None)
    else:
        cuts.append((pos, total, None))
    return cuts


def choose_stretch(total, score, candidates):
    """'stretch' mode: the most interesting STRETCH_MINUTES, cut into back-to-back parts."""
    # long enough for at least MIN_PARTS parts
    L = int(min(max(STRETCH_MINUTES * 60, MIN_PARTS * STRETCH_PART_SECONDS), total))
    # only the middle of the movie: skip the start and the finale (no spoilers)
    lo, hi = int(total * STRETCH_FROM), int(total * STRETCH_TO)
    if hi - lo < L:                                   # short video: widen around the middle
        mid = total * (STRETCH_FROM + STRETCH_TO) / 2
        lo, hi = int(max(0, mid - L / 2 - 1)), int(min(total, mid + L / 2 + 1))
        if hi - lo < L:
            lo, hi = 0, int(total)
    pref = [0.0]
    for v in score:
        pref.append(pref[-1] + v)
    best, best_s = -1.0, lo
    for s in range(lo, max(lo + 1, hi - L + 1)):
        e = min(len(score), s + L)
        avg = (pref[e] - pref[s]) / max(1, e - s)
        if avg > best:
            best, best_s = avg, s
    a = max(0.0, snap(float(best_s), candidates, 20, 5))
    b = min(total, best_end(float(best_s + L), 5, MAX_EXTRA) if ENDINGS else snap(float(best_s + L), candidates, 5, 20))
    print(f"Best stretch: {fmt_time(a)} to {fmt_time(b)} (interest score {best * 100:.0f}/100)", flush=True)
    return choose_cuts(b, candidates, STRETCH_PART_SECONDS, a)


# ---------------------------------------------------------------- render
FLIP_STATE = {"on": False, "last": -1e9, "gap": 0.0}   # carried from part to part within one movie


def reset_flip():
    FLIP_STATE.update(on=False, last=-1e9, gap=0.0)


def scene_cuts(src, start, length):
    """Exact times (seconds from the part's start) where the shot changes."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", str(src.resolve()), "-an", "-sn",
         "-vf", "scale=192:-2,select='gt(scene,0.35)',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return sorted(float(x) for x in re.findall(r"showinfo.*?pts_time:([\d.]+)", res.stderr))


def flip_filter(src, start, end):
    """
    Video filter for the flip. With FLIP_EVERY > 0 the picture switches between normal and
    flipped only on a scene cut, and at most once every FLIP_EVERY seconds of movie time.
    """
    f = FLIP_FILTERS.get(FLIP)
    if not f:
        return "[0:v]"
    if FLIP_EVERY <= 0 and FLIP_EVERY != -1:
        return f"[0:v]{f},"
    length = end - start
    spans, on, since = [], FLIP_STATE["on"], 0.0
    for c in scene_cuts(src, start, length):
        if c < 0.2 or start + c - FLIP_STATE["last"] < max(FLIP_STATE["gap"], 0):
            continue
        if on:
            spans.append((since, c))
        on, since = not on, c
        FLIP_STATE.update(on=on, last=start + c, gap=random.uniform(FLIP_MIN, FLIP_MAX))
    if on:
        spans.append((since, length + 1))
    if not spans:
        return "[0:v]"
    if len(spans) == 1 and spans[0][0] == 0 and spans[0][1] > length:
        return f"[0:v]{f},"
    when = "+".join(f"between(t,{a:.3f},{b - 0.001:.3f})" for a, b in spans)
    print(f"  flipped stretches: {[(round(a, 1), round(min(b, length), 1)) for a, b in spans]}", flush=True)
    return f"[0:v]split=2[n][m];[m]{f}[mf];[n][mf]overlay=0:0:enable='{when}',"


CROP = {"filter": "", "w": None, "h": None}   # black bars to cut off, worked out once per movie


def detect_black_bars(src, total):
    """
    Finds black bars that are part of the video itself (e.g. a widescreen film inside a
    16:9 file) by sampling 9 moments. Keeps the biggest picture area seen, so dark
    scenes can't make it cut into the picture.
    """
    CROP.update(filter="", w=None, h=None)
    boxes = []
    for k in range(1, 10):
        t = total * k / 10
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{t:.1f}", "-t", "4", "-i", str(src),
             "-an", "-sn", "-vf", "fps=2,cropdetect=limit=0.1:round=2:reset=0", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", res.stderr)
        if found:
            w, h, x, y = map(int, found[-1])
            if w > 0 and h > 0:
                boxes.append((x, y, x + w, y + h))
    if not boxes:
        return
    st = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "json", str(src)], capture_output=True, text=True).stdout)["streams"][0]
    W, H = int(st["width"]), int(st["height"])
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    if w * h < 0.97 * W * H and w > W * 0.5 and h > H * 0.3:
        CROP.update(filter=f"crop={w}:{h}:{x0}:{y0},", w=w, h=h)
        print(f"Cutting off black bars: picture is {w}x{h} of {W}x{H}", flush=True)


def movie_bottom(src):
    """Where the bottom edge of the movie sits on the 1080x1920 screen."""
    if STYLE == "zoom":
        return float(tracking.BAND_TOP + tracking.BAND_H)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,sample_aspect_ratio", "-of", "json", str(src)],
        capture_output=True, text=True,
    ).stdout
    try:
        st = json.loads(out)["streams"][0]
        w, h = float(CROP["w"] or st["width"]), float(CROP["h"] or st["height"])
        sar = st.get("sample_aspect_ratio", "1:1")
        if sar and ":" in sar and not sar.startswith("0"):
            a, b = sar.split(":")
            w *= float(a) / float(b)
        fh = min(1920.0, 1080.0 * h / w)
    except Exception:
        fh = 608.0                     # assume a normal widescreen movie
    return (1920 + fh) / 2


def line_filter(src, length):
    """A thin, see-through black line right across the picture, near its bottom. It doesn't move."""
    if not LINE:
        return ";[txt0]null[txt]"
    bottom = movie_bottom(src)
    y = min(1860.0, bottom - 40)
    return (f";[txt0]drawbox=x=0:y={y:.0f}:w=iw:h={LINE_THICKNESS}:"
            f"color=black@{LINE_OPACITY:g}:t=fill[txt]")


def cockroach_filter(length, src):
    """
    A small cockroach peeks in from the left or right side: it walks a little way onto the
    edge of the picture, stops for a moment, then turns and scuttles up and off the top of
    the screen, staying near the edge. It never crosses the middle of the movie.
    Returns (extra ffmpeg inputs, filter text from [txt] to [v]) or None.
    """
    if not COCKROACH or random.random() > COCKROACH_CHANCE:
        return None
    W, size = 1080, COCKROACH_SIZE
    bottom = movie_bottom(src)
    top = 1920 - bottom
    pad = min(80.0, (bottom - top) * 0.2)
    lo, hi = top + pad, bottom - pad                       # height range on the picture
    left = random.random() < 0.5
    off = -size if left else W + size                       # just off the screen
    reach = random.uniform(70, 160)                         # how far onto the picture it gets
    tx = reach if left else W - reach
    y0 = random.uniform(lo, hi)
    ty = min(hi, max(lo, y0 + random.uniform(-110, 110)))
    ex = min(W - 40.0, max(40.0, tx + random.uniform(-40, 40)))   # exit straight up near the edge
    ey = -size
    speed = random.uniform(140, 200)
    pause = random.uniform(0.3, 0.8)
    d1 = math.hypot(tx - off, ty - y0) / speed
    d2 = math.hypot(ex - tx, ey - ty) / random.uniform(260, 340)      # a quicker dash upward
    total = d1 + pause + d2
    if length < total + 2:
        return None
    t0 = random.uniform(min(1.0, length * 0.15), length - total - 0.5)
    # (start, end, from x, from y, to x, to y, moving?)
    segs = [(t0, t0 + d1, off, y0, tx, ty, True),
            (t0 + d1, t0 + d1 + pause, tx, ty, tx, ty, False),
            (t0 + d1 + pause, t0 + total, tx, ty, ex, ey, True)]
    inputs, filt, n, last = [], "", 0, "txt"
    for k, (a, b, xa, ya, xb, yb, moving) in enumerate(segs):
        ang = math.atan2((ty - y0) if k < 2 else (ey - ty), (tx - off) if k < 2 else (ex - tx))
        sprite = (f"scale={size}:-1,format=rgba,"
                  f"rotate={ang:.4f}:c=none:ow=rotw({ang:.4f}):oh=roth({ang:.4f})")
        dur = max(b - a, 0.001)
        x = f"{xa:.1f}+{(xb - xa) / dur:.2f}*(t-{a:.3f})-w/2"
        y = f"{ya:.1f}+{(yb - ya) / dur:.2f}*(t-{a:.3f})-h/2"
        frames = [("roach_a.png", "lt(mod(t*16,2),1)"), ("roach_b.png", "gte(mod(t*16,2),1)")] if moving \
            else [("roach_a.png", "1")]
        for name, legs in frames:
            n += 1
            inputs += ["-loop", "1", "-framerate", "30", "-t", f"{length:.3f}", "-i", str(ASSETS / name)]
            out = "v" if (k == len(segs) - 1 and name == frames[-1][0]) else f"r{n}"
            filt += (f";[{n}:v]{sprite}[s{n}];[{last}][s{n}]overlay=x='{x}':y='{y}':eval=frame:"
                     f"enable='between(t,{a:.3f},{b:.3f})*{legs}'[{out}]")
            last = out
    print(f"  cockroach peeks in from the {'left' if left else 'right'}, then runs up, at {t0:.1f}s-{t0 + total:.1f}s", flush=True)
    return inputs, filt


SUB_SIZE = int(os.environ.get("SUBTITLE_SIZE", "54"))   # subtitle text size in pixels


def wrap_line(text, width=30):
    """Splits subtitle text into up to 3 evenly sized lines of about `width` characters."""
    words = text.replace("\n", " ").split()
    total = len(" ".join(words))
    n = min(3, max(1, math.ceil(total / width)))
    target = math.ceil(total / n)
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > target and len(lines) < n - 1:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return [l for l in lines if l]


ZOOM_SUB_SIZE = 46


def subtitle_filters(part_cues, length, bottom, workdir):
    """Burns subtitles in, centred just under the picture (or near the bottom for tall videos).
    Zoom style: small bold capitals on the lower part of the picture (not over the faces)."""
    out = []
    zoom = STYLE == "zoom"
    size = ZOOM_SUB_SIZE if zoom else SUB_SIZE
    if zoom:
        y0 = tracking.BAND_TOP + int(tracking.BAND_H * 0.80)
    else:
        y0 = bottom + 30 if bottom < 1650 else 1920 - 2 * (SUB_SIZE + 14) - 120
    n = 0
    for a, b, text in part_cues:
        a, b = max(0.0, a), min(length, b)
        if b <= a:
            continue
        lines = wrap_line(text.upper(), 28)[:2] if zoom else wrap_line(text)
        for k, line in enumerate(lines):
            n += 1
            f = workdir / f"sub{n}.txt"
            f.write_text(line, encoding="utf-8")
            out.append(
                f",drawtext=fontfile={FONT}:textfile={f.name}:expansion=none:fontsize={size}:"
                f"fontcolor=white:borderw={3 if zoom else 5}:bordercolor=black@{0.85 if zoom else 1}:"
                f"{'shadowx=0:shadowy=3:shadowcolor=black@0.6:' if zoom else ''}x=(w-text_w)/2:"
                f"y={y0 + k * (size + 12):.0f}:enable='between(t,{a:.3f},{b:.3f})'"
            )
    return "".join(out)


PART_CUES = {}    # (mode, part number) -> that part's subtitle lines (times from the part's start)
ALL_CUES = []     # every subtitle line used, in movie time: for the app's clip timeline


def render_part(src, start, end, number, cues, out_path, workdir, top_text=None):
    """top_text: the big label at the top ("Part 3" by default; "" = no label, for custom clips)."""
    # the flip happens first, so "Part X" and subtitles still read the right way round
    if STYLE == "zoom":
        # zoomed band that follows faces, punchier colours, black above and below
        W, H = picture_size(src)
        track = tracking.plan(src, start, end - start, CROP["filter"], W, H, workdir)
        first = flip_filter(src, start, end).replace("[0:v]", f"[0:v]{CROP['filter']}{track}{story.grade()}", 1)
        filters = (
            f"{first}scale={tracking.BAND_W}:{tracking.BAND_H},setsar=1,"
            "eq=contrast=1.08:saturation=1.3:brightness=0.01,unsharp=5:5:0.5,"
            f"pad=1080:1920:0:{tracking.BAND_TOP}:black"
        )
    else:
        first = flip_filter(src, start, end).replace("[0:v]", f"[0:v]{CROP['filter']}{story.grade()}", 1)
        filters = (
            f"{first}split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            "scale=270:480,boxblur=10:2,scale=1080:1920,eq=brightness=-0.08[bgb];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    # subtitles: your .srt if there is one, otherwise made from this part's audio
    part_cues = [(a - start, b - start, t) for a, b, t in cues if b > start and a < end]
    if not cues and AUTO_SUBS and not SUB_INFO.get("full"):
        try:
            part_cues = auto_subs(src, start, end, workdir)
        except Exception as e:
            print(f"  (no automatic subtitles for this part: {e})", flush=True)
    PART_CUES[(MODE, number)] = part_cues
    seen = {(round(a, 1), t) for a, _, t in ALL_CUES}
    ALL_CUES.extend((round(a + start, 2), round(b + start, 2), t) for a, b, t in part_cues
                    if (round(a + start, 1), t) not in seen)
    filters += subtitle_filters(part_cues, end - start, movie_bottom(src), workdir)
    label = f"{part_word()} {number}" if top_text is None else top_text
    if label:
        (workdir / "toptext.txt").write_text(label, encoding="utf-8")
        filters += (
            f",drawtext=fontfile={FONT}:textfile=toptext.txt:expansion=none:"
            "fontsize=96:fontcolor=white:borderw=7:bordercolor=black:x=(w-text_w)/2:y=230"
        )
    filters += "[txt0]"
    filters += line_filter(src, end - start)
    roach = cockroach_filter(end - start, src)
    extra_inputs = roach[0] if roach else []
    filters += roach[1] if roach else ";[txt]null[v]"
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src.resolve()),
        *extra_inputs,
        "-filter_complex", filters, "-map", "[v]", "-map", "0:a:0?",
        "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(VIDEO_QUALITY), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        "-map_chapters", "-1",            # the movie's chapter list made every part look movie-length
        "-movflags", "+faststart",
        str(out_path.resolve()),
    ], cwd=workdir)
    for f in list(workdir.glob("sub*.txt")) + [workdir / "track.txt"]:
        f.unlink(missing_ok=True)


def picture_size(src):
    """The movie's picture size in pixels, after cutting off any black bars."""
    if CROP["w"]:
        return int(CROP["w"]), int(CROP["h"])
    st = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "json", str(src)], capture_output=True, text=True).stdout)["streams"][0]
    return int(st["width"]), int(st["height"])


PART_TIMES = {}   # (mode, part number) -> (start, end) in the movie, for the status file
COVERS = {}       # (mode, part number) -> the part's thumbnail JPEG (story modes)
PART_HOOKS = {}   # (mode, part number) -> the caption the story writer (Haiku) wrote for it
PART_CREDITS = {}  # (mode, part number) -> the music credit its licence asks for (added to the caption)


def process_local(src, name, srt, outdir, workdir, on_plan=None, on_part=None, modes=None, plan=None, skip=()):
    """
    Does all the video work on files already on disk, once per mode, each mode into its own
    subfolder of outdir. Returns {mode: [files]}.
    on_plan(mode, count, list_file) runs once a mode's parts are chosen; on_part(mode, i, count,
    file, line) runs right after each part is finished, so it can be uploaded straight away.
    plan: {mode: [[start, end, score], ...]} saved by an earlier run of this movie (used as is, so the
    parts come out the same); skip: {(mode, n)} parts that earlier run already finished.
    """
    plan = plan or {}
    global MODE
    modes = modes or MODES
    workdir.mkdir(parents=True, exist_ok=True)
    detect_black_bars(src, duration(src))
    SUB_INFO.update(lang=None, note="from your .srt file" if srt else None, full=False)
    cues = parse_srt(srt)
    resumed = all(m in plan for m in modes)
    if resumed:                       # everything is planned already: no need to analyse again
        total, loud, motion, cuts, quiet = duration(src), [], [], [], []
    else:
        checkpoint(force=True)
        status.set_stage("analysing", movie=name, mode=None)
        total, loud, motion, cuts, quiet = analyse(src, workdir)
        status.movie_duration(name, total)
    if not cues and AUTO_SUBS and ("all" in modes or story.NEEDS_TRANSCRIPT & set(modes)):
        try:
            checkpoint(force=True)
            status.set_stage("subtitles")
            cues = auto_subs_full(src, workdir)
        except Exception as e:
            print(f"(no automatic subtitles: {e})", flush=True)
    candidates = quiet + subtitle_gaps(cues)
    if not resumed:
        build_endings(cues, cuts)
        score = interest_scores(loud, motion, cuts, cues)

    plans = []
    checkpoint(force=True)
    status.set_stage("planning")
    for mode in modes:
        MODE = mode
        if mode in plan:
            parts = [(float(a), float(b), sc) for a, b, sc in plan[mode]]
        elif mode in story.MODE_NAMES:
            try:
                story.set_source(src)
                parts = story.plan(mode, name, total, cues, loud, motion, score)
            except story.Skip as e:
                print(f"Skipping {MODE_NAMES[mode]}: {e}", flush=True)
                notify(f"Skipped for {name}", str(e), ["warning"], kind="info")
                continue
        elif mode == "all":
            parts = choose_cuts(total, candidates)
        elif mode == "stretch":
            parts = choose_stretch(total, score, candidates)
        else:
            parts = choose_highlights(total, score, candidates)
        label = MODE_NAMES[mode]
        print(f"\n{name}: {total/60:.1f} min -> {len(parts)} parts ({label})", flush=True)
        mdir = outdir / label
        mdir.mkdir(parents=True, exist_ok=True)
        width = max(2, len(str(len(parts))))
        lines = []
        for i, (a, b, sc) in enumerate(parts, 1):
            if isinstance(sc, dict):
                line = f"{part_word(mode)} {i}: {story.describe(mode, sc)}"
            else:
                line = f"{part_word(mode)} {i}: {fmt_time(a)} to {fmt_time(b)} ({b - a:.0f}s)"
            if isinstance(sc, (int, float)):
                line += f"  interest score {sc * 100:.0f}/100"
            print("  " + line, flush=True)
            lines.append(line)
        list_file = mdir / "_parts list.txt"
        list_file.write_text("\n".join([name, f"Mode: {label}", ""] + lines) + "\n", encoding="utf-8")
        if on_plan and mode not in plan:
            on_plan(mode, len(parts), list_file, parts)   # saves the plan; the laptop makes the folder
        plans.append((mode, parts, lines, mdir, width, label))

    results = {}
    for mode, parts, lines, mdir, width, label in plans:
        MODE = mode
        reset_flip()
        files = []
        for i, ((a, b, sc), line) in enumerate(zip(parts, lines), 1):
            if (mode, i) in skip:                         # made by an earlier run that stopped
                PART_TIMES[(mode, i)] = (a, b)
                continue
            out = mdir / f"{name} - {part_word(mode)} {str(i).zfill(width)}.mp4"
            print(f"Making {label} part {i} of {len(parts)}...", flush=True)
            status.set_stage("rendering", mode=mode, part=i, parts_total=len(parts))
            if mode in story.MODE_NAMES and story.EDITOR == "filmcraft":   # the laptop edits it in FilmCraft
                out = out.with_name(out.stem + story.PACK_SUFFIX)
                said = story.make_pack(mode, src, i, sc, out, workdir, total)
                PART_CUES[(mode, i)] = said
                PART_CREDITS[(mode, i)] = story.last_credit()
            elif mode in story.MODE_NAMES:
                said = story.render(mode, src, i, sc, cues, out, workdir, total)
                PART_CUES[(mode, i)] = said
                PART_CREDITS[(mode, i)] = story.last_credit()
                if isinstance(sc, dict) and sc.get("caption"):
                    PART_HOOKS[(mode, i)] = sc["caption"]
                try:
                    COVERS[(mode, i)] = story.make_cover(out, i, (sc or {}).get("title") or "",
                                                         out.with_name(out.stem + " - thumbnail.jpg"), workdir)
                except Exception as e:
                    print(f"(no thumbnail for part {i}: {e})", flush=True)
            else:
                render_part(src, a, b, i, cues, out, workdir)
            PART_TIMES[(mode, i)] = (a, b)
            files.append(out)
            if on_part:
                on_part(mode, i, len(parts), out, line)
        results[mode] = files
    MODE = ""
    return results


# ---------------------------------------------------------------- GitHub releases (movies in, parts out)
def github():
    import ghapi
    return ghapi.GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"],
                        os.environ.get("GITHUB_API_URL") or ghapi.API)


def incoming_movies(gh):
    """Movies the laptop has finished sending, oldest first (first come, first served)."""
    out = []
    for r in gh.releases():
        meta = transport.body_of(r)
        if (r.get("draft") and transport.kind_of(r) == "incoming"
                and meta.get("complete") and not meta.get("failed") and meta.get("file")):
            out.append(r)
    return sorted(out, key=lambda r: r.get("created_at") or "")


def update_queue(releases, current=None):
    """Tells the app what's waiting on GitHub to be cut."""
    queue = []
    for r in releases:
        meta = transport.body_of(r)
        if r["id"] != current:
            queue.append({"name": meta.get("file"), "source": meta.get("source") or "laptop",
                          "added": r.get("created_at"), "size_mb": round(int(meta.get("size", 0) or 0) / 1e6)})
    status.set_queue(queue)


def parts_release(gh, rid, name, video):
    """The draft release the finished parts go into. If an earlier run of this movie stopped half-way
    (crash, cancel, pause, time limit), its plan and finished parts are kept, so this run carries on."""
    for r in gh.releases():
        if transport.kind_of(r) == "parts" and transport.id_of(r) == rid:
            body = transport.body_of(r)
            if body.get("failed"):
                body.update(failed=False, done=False)
            body.setdefault("parts", [])
            body.setdefault("lists", {})
            body.setdefault("plan", {})
            body.update(kind="parts", id=rid, movie=name, file=video)
            keep = {n for p in body["parts"] for n in p.get("assets", [])}
            for a in gh.assets(r["id"]):
                if a["name"] not in keep:             # half-sent part from the run that stopped
                    gh.delete_asset(a["id"])
            r = gh.update_release(r["id"], body=json.dumps(body, ensure_ascii=False))
            return r, body
    body = {"kind": "parts", "id": rid, "movie": name, "file": video, "done": False, "failed": False,
            "parts": [], "lists": {}, "plan": {}}
    return gh.create_release(transport.PARTS + rid, f"Parts: {name}", json.dumps(body)), body


def install_tools():
    if os.environ.get("INSTALL_TOOLS", "on" if os.environ.get("GITHUB_ACTIONS") == "true" else "off") in OFF:
        return   # only needed on GitHub's computers
    run(["sudo", "apt-get", "update", "-qq"])
    run(["sudo", "apt-get", "install", "-y", "-qq", "ffmpeg", "fonts-dejavu-core"])
    tracking.get_model()
    for d in ["/usr/share/dotnet", "/usr/local/lib/android", "/opt/ghc", "/opt/hostedtoolcache/CodeQL"]:
        subprocess.run(["sudo", "rm", "-rf", d])
    run(["df", "-h", "/"])


OFF = ("off", "no", "0", "false")


def process_release(gh, release):
    """Gets one movie from its release, cuts it, and sends every part back as soon as it's made."""
    meta = transport.body_of(release)
    rid = transport.id_of(release)
    video = Path(meta["file"]).name
    srt_name = Path(meta["srt"]).name if meta.get("srt") else None
    name = meta.get("movie") or clean_name(video)
    style = apply_style(meta.get("style") or SETTINGS.get("cut_style"))
    story.set_character(meta.get("character"))
    print(f"Cutting style: {'zoomed + tracking' if style == 'zoom' else 'classic'}", flush=True)
    if meta.get("clips"):
        return process_clips(gh, release, meta, rid, video, name)
    global MODE
    labels = ", ".join(MODE_NAMES[m] for m in MODES)
    mid = status.movie_id(name)
    prev = new_previews(gh)
    prev.start_movie(mid)
    ALL_CUES.clear()
    PART_CUES.clear()
    print(f"\n=== {video} -> {name} ===", flush=True)
    earlier = next((r for r in gh.releases() if transport.kind_of(r) == "parts" and transport.id_of(r) == rid), None)
    earlier_parts = len(transport.body_of(earlier).get("parts", [])) if earlier else 0
    if earlier:
        status.movie_resumed(name)
        notify(f"▶️ Carrying on: {name}", f"{earlier_parts} part{'s' if earlier_parts != 1 else ''} were already "
               f"made before it stopped; making the rest.", ["arrow_forward"], kind="started")
    else:
        status.movie_started(name, meta.get("source") or "laptop", MODES)
        notify(f"Started: {name}",
               f"Making: {labels}.\nChecking the movie first, then you'll get a message as each part is made. "
               f"The laptop downloads them into '{READY}/{name}'.", ["clapper"], kind="started")
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "in").mkdir(parents=True)
    captions = {}
    out_rel, out_body = None, None

    def save_body():
        gh.update_release(out_rel["id"], body=json.dumps(out_body, ensure_ascii=False))

    def on_plan(mode, count, list_file, parts):
        out_body["lists"][mode] = list_file.read_text(encoding="utf-8")
        out_body.setdefault("plan", {})[mode] = [[round(a, 3), round(b, 3), sc] for a, b, sc in parts]
        save_body()
        status.movie_planned(name, mode, count)
        notify(f"{name}: {count} part{'s' if count != 1 else ''} planned",
               f"Parts will arrive on the laptop in {READY}/{name}/{MODE_NAMES[mode]} as they're made.",
               ["scissors"], kind="planned")

    def on_part(mode, i, count, out, line):
        label = MODE_NAMES[mode]
        status.set_stage("uploading", mode=mode, part=i, parts_total=count)
        prefix = f"{mode}-{i:02}"
        pack = out.name.endswith(story.PACK_SUFFIX)      # an edit pack: the laptop makes the video in FilmCraft
        pv = None
        if not pack:
            pv = save_preview(prev, out, previews.part_path(mid, mode, i), f"preview {name} {mode} {i}")
            prev.add_full(previews.full_path(mid, mode, i), out.read_bytes(), f"full {name} {mode} {i}")
        seconds = video_seconds(out)
        cover = save_cover(prev, mid, mode, i, name)
        assets = transport.send(gh, out_rel, out, prefix)
        video_name = out.name[:-len(story.PACK_SUFFIX)] + ".mp4" if pack else out.name
        out.unlink(missing_ok=True)  # free up disk space straight away
        if CAPTIONS_ON and mode not in captions:
            captions[mode] = make_captions(count, word=part_word(mode), movie=movie_tags(name))
        text = captions[mode][i - 1] if mode in captions else None
        q = pick_quote(PART_CUES.get((mode, i)))
        if text and PART_HOOKS.get((mode, i)):              # the story writer's own caption for this part
            text = PART_HOOKS[(mode, i)] + "\n" + text.split("\n", 1)[1]
        elif text and q and CAPTION_STYLE != "plain" and random.random() < 0.5:
            text = random.choice(GOOFY_QUOTE).format(q=q) + "\n" + text.split("\n", 1)[1]
        if text and PART_CREDITS.get((mode, i)):
            text += f"\n🎵 {PART_CREDITS[(mode, i)]}"
        a, b = PART_TIMES.get((mode, i), (0.0, 0.0))
        out_body["parts"].append({"mode": mode, "label": label, "n": i, "of": count, "file": video_name,
                                  "assets": assets, "caption": text, "start": round(a, 1), "end": round(b, 1),
                                  "seconds": round(b - a, 1), "line": line, "pack": pack})
        save_body()
        status.part_ready(name, mode, i, a, b, f"{READY}/{name}/{label}/{video_name}", text, preview=pv,
                          cover=cover, seconds=seconds)
        caption = f"\n\n📝 Caption:\n{text}" if text else ""
        notify(f"{name}: {part_word(mode)} {i} of {count} ready",
               f"{line}\nOn its way to the laptop ({READY}/{name}/{label}).{caption}",
               ["white_check_mark"] if i == count else ["film_frames"], kind="part_ready")
        stop_if_needed()

    def stop_if_needed():
        """Between parts: the app's Pause switch, or the run getting close to GitHub's 6-hour limit."""
        if paused_in_app(gh):
            raise RunStopped("paused")
        if (time.time() - STARTED) / 60 > MAX_RUN_MINUTES + 30:
            raise RunStopped("time")

    global CHECKPOINT
    CHECKPOINT = stop_if_needed
    try:
        stop_if_needed()
        status.set_stage("downloading", movie=name, mode=None)
        assets = gh.assets(release["id"])
        src = WORK / "in" / video
        print(f"Downloading {video} from the laptop's upload...", flush=True)
        transport.receive(gh, transport.assets_for(assets, "movie"), src)
        srt = None
        if srt_name and transport.assets_for(assets, "subs"):
            srt = transport.receive(gh, transport.assets_for(assets, "subs"), WORK / "in" / srt_name)
        out_rel, out_body = parts_release(gh, rid, name, video)
        if prev.enabled:
            try:
                data, info = previews.make_thumbs(src, duration(src), WORK)
                path = prev.add(f"{mid}/thumbs.bin", data, f"thumbnails {name}")
                if path:
                    status.movie_previews(name, thumbs=dict(info, path=path))
            except Exception as e:
                print(f"(no thumbnail strip: {e})", flush=True)
        checkpoint(force=True)
        done_parts = {(p["mode"], int(p["n"])) for p in out_body["parts"]}
        results = process_local(src, name, srt, WORK / "out", WORK, on_plan, on_part,
                                plan=out_body.get("plan") or {}, skip=done_parts)
        save_subs(prev, mid, name)
        made = {}
        for p in out_body["parts"]:
            made[p["mode"]] = made.get(p["mode"], 0) + 1
        summary = ", ".join(f"{MODE_NAMES[m]}: {made.get(m, 0)} part{'s' if made.get(m, 0) != 1 else ''}"
                            for m in results)
        out_body.update(done=True, finished=status.now_iso(), summary=summary,
                        subtitles=SUB_INFO["note"] or "none (no speech found)")
        save_body()
        gh.delete_release(release["id"], release["tag_name"])     # the uploaded movie isn't needed any more
        print(f"Done: {name}", flush=True)
        status.movie_finished(name, SUB_INFO["note"] or "none (no speech found)")
        notify(f"✅ Finished: {name}",
               f"{summary}.\nThe laptop downloads them into {READY}/{name}. The copy sent to GitHub "
               f"has been deleted.", ["tada"], kind="finished")
    except RunStopped as stop:
        MODE = ""
        n = len(out_body["parts"]) if out_body else 0
        print(f"Stopped ({stop}) after {n} parts of {name}; the next run carries on.", flush=True)
        status.movie_paused(name, "Paused in the app" if str(stop) == "paused" else "Continuing in a new run")
        if str(stop) == "paused":
            notify(f"⏸ Paused: {name}", f"{n} part{'s' if n != 1 else ''} made so far. It carries on from the "
                   f"next part when you switch Pause off.", ["pause_button"], kind="paused")
        raise
    except Exception as e:
        MODE = ""
        print(f"FAILED: {video}: {e}", flush=True)
        try:
            meta.update(failed=True, error=str(e)[:500])
            gh.update_release(release["id"], body=json.dumps(meta, ensure_ascii=False))
            if out_rel:
                out_body.update(done=True, failed=True)
                save_body()
        except Exception as e2:
            print(f"(couldn't mark the release as failed: {e2})", flush=True)
        status.movie_failed(name, e)
        notify(f"❌ Failed: {name}", f"Any parts already made still go to the laptop. The laptop moves the "
               f"original to its 'Failed' folder; put it back in the watch folder to try again.\n{e}", ["x"],
               kind="failed")
    finally:
        CHECKPOINT = None
        shutil.rmtree(WORK, ignore_errors=True)


def new_previews(gh):
    return previews.Previews(gh, NTFY_TOPIC, SETTINGS.get("preview_keep_movies", 3), SETTINGS.get("previews", True),
                             log=lambda m: print(m, flush=True))


def video_seconds(path):
    """How long a finished part really plays (None if it can't be read, e.g. an edit pack)."""
    try:
        return round(duration(path), 1)
    except Exception:
        return None


def save_cover(prev, mid, mode, n, name):
    """The part's thumbnail, encrypted for the app to download. Returns its path (or None)."""
    cover = COVERS.get((mode, n))
    if not cover or not Path(cover).is_file() or not prev.enabled:
        return None
    return prev.add(f"{mid}/{mode}-{n:02}-cover.bin", Path(cover).read_bytes(), f"thumbnail {name} {mode} {n}")


def save_preview(prev, video, path, message):
    """Small encrypted copy of a finished part for the app. Returns its path (or None)."""
    if not prev.enabled:
        return None
    small = WORK / "preview.mp4"
    try:
        previews.make_part_preview(video, small)
        return prev.add(path, small.read_bytes(), message)
    except Exception as e:
        print(f"(no preview for {Path(video).name}: {e})", flush=True)
        return None
    finally:
        small.unlink(missing_ok=True)


def save_subs(prev, mid, name):
    """The movie's subtitle lines (encrypted), so the clip timeline can show what's being said."""
    if prev.enabled and ALL_CUES:
        path = prev.add(f"{mid}/subs.bin", json.dumps(sorted(ALL_CUES), ensure_ascii=False).encode("utf-8"),
                        f"subtitles {name}")
        if path:
            status.movie_previews(name, subs=path)


MAX_CLIP_SECONDS = 600     # TikTok's longest upload is 10 minutes


def process_clips(gh, release, meta, rid, video, name):
    """Custom clips the owner picked in the app (start/end in the movie): each one rendered with the
    same look as the parts (flips, subtitles, line, cockroach), into the 'Custom clips' folder."""
    global MODE
    srt_name = Path(meta["srt"]).name if meta.get("srt") else None
    clips = [c for c in meta.get("clips") or [] if float(c.get("end", 0)) > float(c.get("start", 0))]
    mid = status.movie_id(name)
    prev = new_previews(gh)
    prev.start_movie(mid)
    status.clips_started(name, len(clips), meta.get("source") or "laptop")
    notify(f"✂️ Making {len(clips)} custom clip{'s' if len(clips) != 1 else ''}: {name}",
           f"They'll arrive on the laptop in {READY}/{name}/Custom clips.", ["scissors"], kind="started")
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "in").mkdir(parents=True)
    (WORK / "out").mkdir(parents=True)
    out_rel, out_body = None, None
    SUB_INFO.update(lang=None, note="from your .srt file" if srt_name else None, full=False)
    try:
        status.set_stage("downloading", movie=name, mode="clips")
        assets = gh.assets(release["id"])
        src = WORK / "in" / video
        transport.receive(gh, transport.assets_for(assets, "movie"), src)
        srt = None
        if srt_name and transport.assets_for(assets, "subs"):
            srt = transport.receive(gh, transport.assets_for(assets, "subs"), WORK / "in" / srt_name)
        out_rel, out_body = parts_release(gh, rid, name, video)
        out_body["clips"] = True
        total = duration(src)
        detect_black_bars(src, total)
        cues = parse_srt(srt)
        MODE = "clips"
        first = status.next_clip_number(name)
        label = MODE_NAMES["clips"]
        for k, c in enumerate(clips):
            n = first + k
            a = max(0.0, float(c["start"]))
            b = min(total, float(c["end"]), a + MAX_CLIP_SECONDS)
            title = safe(str(c.get("title") or "")).strip()[:60]
            fname = f"{name} - Clip {n:02}{(' - ' + title) if title else ''}.mp4"
            out = WORK / "out" / fname
            reset_flip()
            print(f"Making clip {k + 1} of {len(clips)}: {fmt_time(a)} to {fmt_time(b)}", flush=True)
            status.set_stage("rendering", movie=name, mode="clips", part=k + 1, parts_total=len(clips))
            render_part(src, a, b, n, cues, out, WORK, top_text="")
            status.set_stage("uploading", mode="clips", part=k + 1, parts_total=len(clips))
            pv = save_preview(prev, out, previews.part_path(mid, "clips", n), f"preview {name} clip {n}")
            prev.add_full(previews.full_path(mid, "clips", n), out.read_bytes(), f"full {name} clip {n}")
            sent = transport.send(gh, out_rel, out, f"clips-{n:02}")
            out.unlink(missing_ok=True)
            text = make_clip_caption(pick_quote(PART_CUES.get(("clips", n)))) if CAPTIONS_ON else None
            line = f"Clip {n}: {fmt_time(a)} to {fmt_time(b)} ({b - a:.0f}s)" + (f"  {title}" if title else "")
            out_body["parts"].append({"mode": "clips", "label": label, "n": n, "of": len(clips), "file": fname,
                                      "assets": sent, "caption": text, "start": round(a, 1), "end": round(b, 1),
                                      "seconds": round(b - a, 1), "line": line, "clip_id": c.get("id")})
            gh.update_release(out_rel["id"], body=json.dumps(out_body, ensure_ascii=False))
            status.part_ready(name, "clips", n, a, b, f"{READY}/{name}/{label}/{fname}", text, preview=pv,
                              title=title or None)
            notify(f"✂️ {name}: clip {k + 1} of {len(clips)} ready", f"{line}\nOn its way to the laptop "
                   f"({READY}/{name}/{label}).", ["film_frames"], kind="part_ready")
        out_body.update(done=True, finished=status.now_iso(), summary=f"Custom clips: {len(clips)}")
        gh.update_release(out_rel["id"], body=json.dumps(out_body, ensure_ascii=False))
        gh.delete_release(release["id"], release["tag_name"])
        status.movie_finished(name)
        notify(f"✅ Clips done: {name}", f"{len(clips)} custom clip{'s' if len(clips) != 1 else ''} made.",
               ["tada"], kind="finished")
    except Exception as e:
        print(f"FAILED clips of {video}: {e}", flush=True)
        try:
            meta.update(failed=True, error=str(e)[:500])
            gh.update_release(release["id"], body=json.dumps(meta, ensure_ascii=False))
            if out_rel:
                out_body.update(done=True, failed=True)
                gh.update_release(out_rel["id"], body=json.dumps(out_body, ensure_ascii=False))
        except Exception as e2:
            print(f"(couldn't mark the release as failed: {e2})", flush=True)
        status.movie_failed(name, e)
        notify(f"❌ Clips failed: {name}", str(e), ["x"], kind="failed")
    finally:
        MODE = ""
        shutil.rmtree(WORK, ignore_errors=True)


class RunStopped(Exception):
    """Stop after the current part: "paused" (app switch) or "time" (GitHub's 6-hour limit is near)."""


def paused_in_app(gh):
    """The app's Pause switch (settings.json on GitHub, read fresh so it works mid-run)."""
    try:
        text, _ = gh.read_file("config/settings.json")
        return bool(settings.load(text=text or "{}", env={}).get("paused"))
    except Exception as e:
        print(f"(couldn't check the pause switch: {e})", flush=True)
        return False


def main():
    """
    Keeps going until no movies are waiting on GitHub, always taking the oldest next,
    so movies sent while this is running get picked up in the same run.
    """
    status.run_started(int(os.environ["GITHUB_RUN_ID"]) if os.environ.get("GITHUB_RUN_ID", "").isdigit() else None)
    gh = github()
    tools_ready, tried = False, set()
    while True:
        entries = [r for r in incoming_movies(gh) if r["id"] not in tried]
        update_queue(entries, entries[0]["id"] if entries else None)
        if not entries:
            if not tools_ready:
                print("No movies waiting.")
            status.run_finished()
            return
        if (time.time() - STARTED) / 60 > MAX_RUN_MINUTES:
            print("Time limit reached; the rest will be done in the next run.")
            notify("Paused for now", f"{len(entries)} movie(s) still waiting. They'll be done in the next run.",
                   kind="paused")
            status.run_finished()
            return
        if paused_in_app(gh):
            print("Paused in the app (Settings > Pause). Nothing will be cut until it's switched back on.")
            status.set_stage("idle", movie=None, mode=None)
            status.run_finished()
            return
        if not tools_ready:
            install_tools()
            tools_ready = True
        nxt = entries[0]
        tried.add(nxt["id"])
        try:
            process_release(gh, nxt)
        except RunStopped as stop:
            if str(stop) == "time":
                try:
                    gh.dispatch("make-parts.yml")          # a fresh run picks up where this one stopped
                    notify("⏱ Continuing in a new run", "GitHub runs are limited to 6 hours, so a new run "
                           "carries on from the next part.", kind="paused")
                except Exception as e:
                    print(f"(couldn't start the next run: {e}; it starts by itself once a day)", flush=True)
            status.set_stage("idle", movie=None, mode=None)
            status.run_finished()
            return


def cut_here(src, srt, ready):
    """
    The laptop cuts a movie itself (setting cut_on: laptop), so nothing is uploaded: the parts go
    straight into <ready>/<movie>/<mode>/, with the same status, notifications and captions as a
    GitHub run. The laptop helper starts this as its own program: process.py --here <movie> [--srt f] --out <ready>.
    """
    global MODE
    import tempfile
    name = clean_name(src.name)
    apply_style(SETTINGS.get("cut_style"))
    labels = ", ".join(MODE_NAMES[m] for m in MODES)
    work = Path(tempfile.mkdtemp(prefix="msplit-"))
    captions = {}
    mid = status.movie_id(name)
    WORK.mkdir(parents=True, exist_ok=True)
    try:                                  # finished parts also go to GitHub, for quick saving from the phone
        prev = new_previews(github())
        prev.start_movie(mid)
    except Exception as e:
        print(f"(parts won't be uploaded to GitHub: {e})", flush=True)
        prev = None
    status.run_started(None)
    status.movie_started(name, "laptop", MODES)
    notify(f"Started: {name}", f"Cutting on the laptop: {labels}. Parts go into '{READY}/{name}'.",
           ["clapper"], kind="started")

    def on_plan(mode, count, list_file, parts):
        status.movie_planned(name, mode, count)
        notify(f"{name}: {count} part{'s' if count != 1 else ''} planned", list_file.read_text(encoding="utf-8")[:900],
               ["scissors"], kind="planned")

    def on_part(mode, i, count, out, line):
        label = MODE_NAMES[mode]
        if CAPTIONS_ON and mode not in captions:
            captions[mode] = make_captions(count, word=part_word(mode), movie=movie_tags(name))
        text = captions[mode][i - 1] if mode in captions else None
        q = pick_quote(PART_CUES.get((mode, i)))
        if text and PART_HOOKS.get((mode, i)):              # the story writer's own caption for this part
            text = PART_HOOKS[(mode, i)] + "\n" + text.split("\n", 1)[1]
        elif text and q and CAPTION_STYLE != "plain" and random.random() < 0.5:
            text = random.choice(GOOFY_QUOTE).format(q=q) + "\n" + text.split("\n", 1)[1]
        if text and PART_CREDITS.get((mode, i)):
            text += f"\n🎵 {PART_CREDITS[(mode, i)]}"
        if text:
            with open(out.parent / "captions.txt", "a", encoding="utf-8") as f:
                f.write(f"{part_word(mode)} {i}  ({out.name})\n{text}\n\n")
        a, b = PART_TIMES.get((mode, i), (0.0, 0.0))
        pv = None
        if prev:
            try:
                pv = save_preview(prev, out, previews.part_path(mid, mode, i), f"preview {name} {mode} {i}")
                prev.add_full(previews.full_path(mid, mode, i), out.read_bytes(), f"full {name} {mode} {i}")
            except Exception as e:
                print(f"(couldn't upload {out.name} to GitHub: {e})", flush=True)
        cover = save_cover(prev, mid, mode, i, name) if prev else None
        status.part_ready(name, mode, i, a, b, f"{READY}/{name}/{label}/{out.name}", text, preview=pv,
                          cover=cover, seconds=video_seconds(out))
        notify(f"{name}: {part_word(mode)} {i} of {count} ready", f"{line}\nIn {READY}/{name}/{label}.",
               ["white_check_mark"] if i == count else ["film_frames"], kind="part_ready")

    try:
        results = process_local(src, name, srt, ready / safe(name), work, on_plan, on_part)
        summary = ", ".join(f"{MODE_NAMES[m]}: {len(f)} part{'s' if len(f) != 1 else ''}" for m, f in results.items())
        status.movie_finished(name, SUB_INFO["note"] or "none (no speech found)")
        notify(f"✅ Finished: {name}", f"{summary}.\nIn {READY}/{name}.", ["tada"], kind="finished")
        status.run_finished()
    except BaseException as e:
        MODE = ""
        status.movie_failed(name, e)
        notify(f"❌ Failed: {name}", str(e)[:500], ["x"], kind="failed")
        status.run_finished(failed=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        status.flush()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--here":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--here", type=Path, required=True)
        ap.add_argument("--srt", type=Path)
        ap.add_argument("--out", type=Path, required=True)
        a = ap.parse_args()
        cut_here(a.here, a.srt, a.out)
        sys.exit(0)
    # Local test: python3 process.py --local video.mp4 [subs.srt]
    if len(sys.argv) > 2 and sys.argv[1] == "--local":
        src = Path(sys.argv[2])
        srt = Path(sys.argv[3]) if len(sys.argv) > 3 else None
        process_local(src, clean_name(src.name), srt, Path("out"), WORK)
    else:
        import signal
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # "Cancel run" on GitHub: still save status
        try:
            main()
        except BaseException:
            status.set_stage("failed")
            status.run_finished(failed=True)
            raise
