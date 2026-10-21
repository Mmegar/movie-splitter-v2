"""
Builds the storytelling edits (Movie recap, Character story, Hype edit) in FilmCraft from a plan that
story.py made, then exports them. Runs on a computer with FilmCraft installed (the laptop), using the
original movie file that's already there.

Look (from the reference edits): the whole picture, never zoomed in, on a blurred copy of itself;
a cinematic colour grade; hype montages cut on the music's beat with white flash cuts, three shots
stacked in slanted mask panels that slide in one after another, a slow-motion beat and the
character's name slammed on screen. Narrated parts put the narrator over the matching scene with
the movie's sound turned down, big word-by-word captions, and quiet music under it all.
"""
import json
from pathlib import Path

from filmcraft import FilmCraft, ticks

W, H, FPS = 1080, 1920, 30
LOOKS = {"mystery": "Steel Blue", "noir": "Silver Retention", "teal_orange": "Teal Shadows, Warm Skin",
         "warm": "Warm Negative"}
TEXT_TRACK = 5                          # V6, above every picture and the flash (V5)
PANEL_Y = (352, 960, 1568)              # three full-width pictures stacked down the screen
SLANT = [[0, 70], [1280, 0], [1280, 650], [0, 720]]   # mask in the movie's own pixels (scaled per movie)


class Timeline:
    """One vertical 1080x1920 sequence being built shot by shot, left to right."""

    def __init__(self, fc, movie, name, look="mystery", src_size=(1920, 1080)):
        self.fc, self.t, self.look = fc, 0.0, LOOKS.get(look)
        self.sw, self.sh = src_size
        fc.run("file.newSequence", name=name, width=W, height=H, fps=FPS, video=6, audio=4)
        self.movie = fc.run("file.import", paths=[str(Path(movie).resolve())])["items"][0]
        self.white = fc.run("file.newColorMatte", color="#ffffff", seconds=1.0).get("item")
        self.fit = 100.0 * W / self.sw                     # whole picture across the width: no zoom
        self.fill = 100.0 * H / self.sh                    # background: fills the screen (it's blurred)
        self.captions = False

    # -------------------------------------------------- pieces
    def _place(self, item, track, at, src_in=None, dur=None, audio="A1"):
        p = dict(item=item, track=track, seconds=round(at, 4), insert=False)
        if src_in is not None:
            p["sourceIn"] = ticks(src_in)
        if dur is not None:
            p["duration"] = ticks(dur)
        if audio:
            p["audioTrack"] = audio
        return self.fc.run("timeline.place", **p)["clips"]

    def _drop_audio(self, clips):
        """Removes the sound that came with a placed picture (unlinks them first, so the picture stays)."""
        if len(clips) > 1:
            self.fc.run("clip.link", clips=clips)          # toggles: these were linked, so this unlinks
            self.fc.run("edit.clear", clips=clips[1:])

    def _grade(self, clip):
        if self.look:
            self.fc.run("lumetri.applyPreset", name=self.look, clips=[clip])

    def background(self, src_in, dur, at=None):
        """A blurred, darker copy of the shot filling the screen behind it."""
        at = self.t if at is None else at
        clips = self._place(self.movie, "V1", at, src_in, dur, audio="A4")
        self._drop_audio(clips)
        bg = clips[0]
        self.fc.run("effects.setParam", clip=bg, effect="motion", param="scale", value=self.fill)
        self.fc.run("effects.apply", clips=[bg], effect="Gaussian Blur")
        self.fc.run("effects.setParam", clip=bg, effect="gaussian_blur", param="blurriness", value=60)
        self.fc.run("effects.setParam", clip=bg, effect="opacity", param="opacity", value=55)
        return bg

    def shot(self, src_in, dur, volume_db=0.0, slow=1.0, flash=False, keep_sound=True):
        """The movie from src_in for dur seconds of timeline, whole picture, graded, on its blurred copy."""
        at = self.t
        self.background(src_in, dur, at)                  # (the blurred copy plays at normal speed)
        clips = self._place(self.movie, "V2", at, src_in, dur / slow, audio="A1" if keep_sound else "A4")
        v = clips[0]
        if not keep_sound:
            self._drop_audio(clips)
        if slow != 1.0:
            self.fc.run("clip.speedDuration", clips=clips[:1 if not keep_sound else 2], speed=100.0 / slow,
                        ripple=False, interpolation="opticalFlow")
        self.fc.run("effects.setParam", clip=v, effect="motion", param="scale", value=self.fit)
        self._grade(v)
        if keep_sound and len(clips) > 1 and volume_db:
            self.fc.run("effects.setParam", clip=clips[1], effect="volume", param="level", value=volume_db)
        if flash:
            self.flash(at)
        self.t = at + dur
        return v

    def flash(self, at, frames=5):
        """A white flash that fades out over a few frames (the 'Flash In' cut)."""
        if not self.white:
            return
        c = self._place(self.white, "V5", at, 0, frames / FPS, audio=None)[0]
        self.fc.run("effects.toggleAnimation", clip=c, effect="opacity", param="opacity")
        self.fc.run("effects.setParam", clip=c, effect="opacity", param="opacity", value=100, time=0)
        self.fc.run("effects.setParam", clip=c, effect="opacity", param="opacity", value=0, time=ticks(frames / FPS))

    def slide3(self, starts, dur):
        """Three shots stacked in slanted panels, sliding in from alternating sides one after another."""
        at = self.t
        self.background(starts[1], dur, at)
        sx, sy = self.sw / 1280.0, self.sh / 720.0
        mask = [[x * sx, y * sy] for x, y in SLANT]
        for i, s in enumerate(starts[:3]):
            clips = self._place(self.movie, f"V{2 + i}", at, s, dur, audio="A1" if i == 1 else "A4")
            if i != 1:
                self._drop_audio(clips)
            c = clips[0]
            self.fc.run("effects.setParam", clip=c, effect="motion", param="scale", value=self.fit)
            self._grade(c)
            self.fc.run("masks.add", clip=c, effect="opacity", shape="polygon", path=mask)
            self.fc.run("masks.set", clip=c, effect="opacity", mask=0, feather=6)
            off = -W / 2 - 80 if i % 2 == 0 else W * 1.5 + 80
            t0, t1 = 0.1 * i, 0.1 * i + 0.28
            self.fc.run("effects.toggleAnimation", clip=c, effect="motion", param="position")
            self.fc.run("effects.setParam", clip=c, effect="motion", param="position", value=[off, PANEL_Y[i]], time=ticks(t0))
            self.fc.run("effects.setParam", clip=c, effect="motion", param="position", value=[W / 2, PANEL_Y[i]], time=ticks(t1))
        self.flash(at, 4)
        self.t = at + dur

    def title(self, text, at, seconds=1.4, size=150):
        """The character's name, big, punching in."""
        if not text:
            return
        r = self.fc.run("graphics.newText", text=text.upper(), position=[W / 2, H / 2], size=size,
                        seconds=seconds, time=ticks(at), newClip=True, track=TEXT_TRACK)
        clip = (r or {}).get("clip")
        if clip is not None:
            self.fc.run("graphics.set", clip=clip, props={"font_style": "Bold", "align": "center", "stroke": True,
                                                         "stroke_width": 8, "fill_color": "#ffffff", "shadow": True})

    def caption(self, text, at, seconds):
        """Narration captions: burned in at export, big and bold, low on the screen."""
        if not self.captions:
            self.fc.run("captions.newTrack", format="Subtitle", name="Narration")
            self.fc.run("captions.setStyle", track="C1", size=64, color="#ffffff", outline=6, outlineColor="#000000",
                        background=False, align="center", anchor="bottom", margin=0.2)
            self.captions = True
        self.fc.run("captions.add", track="C1", text=text.upper(), seconds=round(at, 3), durationSeconds=round(seconds, 3))

    def sound(self, path, at, track="A2", level_db=0.0, length=None):
        """Narration (A2) or music (A3)."""
        item = self.fc.run("file.import", paths=[str(Path(path).resolve())])["items"][0]
        p = dict(item=item, track=track, seconds=round(at, 4), insert=False)
        if length:
            p["duration"] = ticks(length)
        clip = self.fc.run("timeline.place", **p)["clips"][0]
        if level_db:
            self.fc.run("effects.setParam", clip=clip, effect="volume", param="level", value=level_db)
        return clip

    def text_top(self, text):
        """The 'Part 3' label at the top for the whole length."""
        if text and self.t > 0:
            r = self.fc.run("graphics.newText", text=text, position=[W / 2, 300], size=96, seconds=round(self.t, 3),
                            time=0, newClip=True, track=TEXT_TRACK)
            clip = (r or {}).get("clip")
            if clip is not None:
                self.fc.run("graphics.set", clip=clip, props={"font_style": "Bold", "align": "center", "stroke": True,
                                                             "stroke_width": 7})

    def export(self, out, captions=False):
        self.fc.export(out, burnCaptions=captions or self.captions, quality=80)


# -------------------------------------------------- the edits
def build_hype(fc, movie, info, out, music=None, beat=0.5, music_start=0.0, montage_seconds=15, look="mystery",
               src_size=(1920, 1080), steps=None):
    """Setup (they get disrespected) -> payoff (they answer it) -> beat-synced montage over the song."""
    import story
    tl = Timeline(fc, movie, f"Hype {info.get('title') or ''}".strip(), look, src_size)
    a, b = info["setup"]
    tl.shot(a, b - a)
    a, b = info["payoff"]
    tl.shot(a, b - a, flash=True)
    m0 = tl.t
    shots = [s for s, _ in info["montage"]] or [info["payoff"][0]]
    pick = iter(shots * 20)
    tl.title(info.get("title"), m0)
    for kind, n in (steps or story.montage_steps(beat, montage_seconds)):
        dur = max(2, round(n * beat * FPS)) / FPS
        if kind == "slide3":
            tl.slide3([next(pick) for _ in range(3)], dur)
        elif kind == "slow":
            tl.shot(next(pick), dur, slow=2.0, flash=True, volume_db=-18)
        else:
            tl.shot(next(pick), dur, flash=True, volume_db=-18)
    if music:
        tl.sound(music, m0, track="A3", length=tl.t - m0)
    tl.export(out)


def build_story(fc, movie, number, info, out, voices, music=None, look="mystery", src_size=(1920, 1080)):
    """A narrated part. voices: per beat index, (mp3 path, seconds, [(start, end, word)]) from story.speak."""
    import story
    tl = Timeline(fc, movie, f"Part {number}", look, src_size)
    for k, beat in enumerate(info["beats"]):
        if "say" in beat and k in voices:
            mp3, length, words = voices[k]
            at = tl.t
            tl.shot(beat["at"], length + 0.35, volume_db=-15)
            tl.sound(mp3, at, track="A2")
            for c0, c1, text in story.chunks(words):
                tl.caption(text, at + c0, max(0.2, c1 - c0))
        elif "keep" in beat:
            tl.shot(beat["keep"], beat["until"] - beat["keep"])
    tl.text_top(f"Part {number}")
    if music:
        tl.sound(music, 0, track="A3", level_db=-20, length=tl.t)
    tl.export(out)


def build_from_pack(pack, out, movie, log=print):
    """Turns an edit pack from GitHub (see story.make_pack) into the finished video `out`, cut from the
    original `movie` on this computer."""
    import tempfile
    import zipfile
    import story
    with tempfile.TemporaryDirectory(prefix="editpack-") as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(pack) as z:
            z.extractall(tmp)
        plan = json.loads((tmp / "plan.json").read_text(encoding="utf-8"))
        music = tmp / plan["music"] if plan.get("music") else None
        log(f"FilmCraft: building {Path(out).name}")
        with FilmCraft(log=log) as fc:
            size = probe_size(fc, movie)
            if plan["mode"] == "hype":
                build_hype(fc, movie, plan["info"], out, music=music, beat=float(plan.get("beat") or 0.5),
                           montage_seconds=float(plan.get("montage_seconds") or 15), look=plan.get("look"),
                           src_size=size, steps=story.montage_steps(float(plan.get("beat") or 0.5),
                                                                    float(plan.get("montage_seconds") or 15)))
            else:
                voices = {v["beat"]: (tmp / v["file"], float(v["seconds"]), [tuple(w) for w in v["words"]])
                          for v in plan.get("voices", [])}
                build_story(fc, movie, plan["number"], plan["info"], out, voices, music=music,
                            look=plan.get("look"), src_size=size)
    return out


def probe_size(fc, movie):
    """The movie's picture size (width, height), from `filmcraft-cli probe`."""
    import subprocess
    try:
        info = json.loads(subprocess.run([fc.cli, "probe", str(Path(movie).resolve())], capture_output=True,
                                         text=True, timeout=60).stdout)
        v = info["video"]
        par = v.get("par") or [1, 1]
        return int(round(v["width"] * par[0] / par[1])), int(v["height"])
    except Exception:
        return 1920, 1080
