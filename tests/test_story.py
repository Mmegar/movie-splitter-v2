"""The storytelling modes' planning and timing logic (no ffmpeg, no network)."""
import os
import unittest
from pathlib import Path
from unittest import mock

import settings
import story


class FakeP:
    """Just the process.py helpers the planning code uses."""
    @staticmethod
    def rank01(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        for r, i in enumerate(order):
            out[i] = r / max(1, len(values) - 1)
        return out

    @staticmethod
    def fmt_time(s):
        return f"{int(s) // 60}:{int(s) % 60:02}"


class StoryTest(unittest.TestCase):
    def setUp(self):
        story.P = FakeP
        story.load_settings({})
        story.set_character(None)
        story.WINDOW[:] = [0.0, 1e9]

    def test_defaults_switch_to_the_new_modes(self):
        s = settings.load(path="nope.json", env={})
        self.assertEqual(s["modes"], ["recap", "character", "hype"])
        self.assertEqual(s["cut_style"], "zoom")          # zoomed in, following the characters
        self.assertEqual(s["filter"], "vivid")

    def test_settings_reach_the_story_module(self):
        env = {}
        settings.apply_to_env(settings.load(path="nope.json", env={}), env)
        story.load_settings(env)
        self.assertEqual(story.FILTER, "vivid")
        self.assertEqual(story.RECAP_PART_SECONDS, 180)
        self.assertTrue(story.MUSIC)
        env.update(MUSIC="off", CHARACTER="Poirot")
        story.load_settings(env)
        self.assertFalse(story.MUSIC)
        self.assertEqual(story.character(), "Poirot")
        story.set_character("Hastings")                  # picked for this movie in the app
        self.assertEqual(story.character(), "Hastings")

    def test_grade_is_gentle_and_can_be_off(self):
        g = story.grade("mystery", 0.5)
        self.assertTrue(g.endswith(","))
        self.assertIn("colorbalance", g)
        self.assertIn("vignette", g)
        self.assertNotIn("crop", g)                       # never zooms
        self.assertNotIn("scale", g)
        self.assertEqual(story.grade("none", 0.5), "")
        self.assertEqual(story.grade("mystery", 0), "")
        self.assertIn("saturation=0.625", story.grade("noir", 0.5))

    def test_transcript_joins_close_lines(self):
        cues = [(10.2, 11, "Who was here"), (11.5, 12, "last night?"), (40, 41, "Nobody.")]
        self.assertEqual(story.transcript(cues), "[10] Who was here last night?\n[40] Nobody.")

    def test_parse_json_ignores_fences(self):
        self.assertEqual(story.parse_json('Here:\n```json\n{"a": [1, 2]}\n```'), {"a": [1, 2]})
        with self.assertRaises(ValueError):
            story.parse_json("no json")

    def test_fast_style_is_the_default_storyteller(self):
        p = story.story_prompt("Film (2020)", 3600, "subs", "recap", "")
        self.assertTrue(story.fast())
        self.assertIn("Third person, past tense", p)
        self.assertIn('"intro" is empty', p)
        self.assertNotIn('with "you" and "we"', p)
        self.assertNotIn('"joke": true', p)
        data = {"character": "", "parts": [
            {"title": "a", "beats": [{"say": "The boy ran.", "at": 5, "joke": True}, {"say": "", "at": 20, "until": 26}]},
            {"title": "b", "beats": [{"say": "It ended.", "at": 300}]}]}
        beats = story.clean_story(data, 600)[0][2]["beats"]
        self.assertEqual(len(beats), 1)                                     # no silent original moment
        self.assertNotIn("joke", beats[0])                                  # and no joke pause

    def test_no_double_cut_next_to_a_movie_cut(self):
        old = getattr(FakeP, "scene_cuts", None)
        try:
            FakeP.scene_cuts = staticmethod(lambda src, start, length: [c - start for c in (100.3, 205.7, 300.5)
                                                                      if start < c < start + length])
            self.assertAlmostEqual(story.clean_start("m.mp4", 100.0, 5.0, 3600), 100.35)  # starts on the cut
            self.assertAlmostEqual(story.clean_start("m.mp4", 201.0, 5.0, 3600), 200.65)  # ends before it
            self.assertEqual(story.clean_start("m.mp4", 50.0, 5.0, 3600), 50.0)           # nothing near
            self.assertAlmostEqual(story.clean_start("m.mp4", 300.0, 1.0, 3600), 300.55)  # a montage shot: one shot
        finally:
            if old is None:
                del FakeP.scene_cuts
            else:
                FakeP.scene_cuts = old

    def test_badge_text_uses_the_setting(self):
        self.assertEqual(story.badge_text(3), "PART 3")
        self.assertEqual(story.badge_text(2, "Ep {n}"), "EP 2")
        self.assertEqual(story.badge_text(4, "Finale {x}"), "FINALE {X}")

    def test_cover_times_follow_the_beats(self):
        self.assertEqual(story.cover_times({"beats": [{"say": "a", "at": 10}]}, None), [10.6, 11.8, 13.2])
        self.assertEqual(len(story.cover_times({"beats": [{"at": t} for t in range(100)]}, None)), story.COVER_TRIES)
        self.assertEqual(story.cover_times({}, None), [])

    def test_cover_moment_is_a_third_into_the_part(self):
        info = {"beats": [{"say": "a", "at": 10}, {"say": "b", "at": 20}, {"keep": 30, "until": 35}]}
        self.assertEqual(story.cover_moment(info, None), 21.0)
        self.assertEqual(story.cover_moment({"payoff": [50, 60]}, None), 51.0)
        self.assertIsNone(story.cover_moment({}, None))

    def test_chunks_of_one_word(self):
        self.assertEqual([c[2] for c in story.chunks([(0, .2, "The"), (.2, .4, "boy"), (.4, .7, "ran.")], 1, 40)],
                         ["The", "boy", "ran."])

    def test_a_part_is_read_in_one_voice(self):
        import tempfile
        from pathlib import Path
        calls = []
        old = story.speak_chatterbox, story.speak_edge, story.faster, story.VOICE_ENGINE, dict(story._CHATTER)
        FakeP.checkpoint = staticmethod(lambda: None)

        def chatter(text, mp3, mood):
            if text == "two":
                raise RuntimeError("busy")
            calls.append(("chatterbox", text, mood))
            return 1.0, []

        def edge(text, mp3, mood):
            calls.append(("edge", text, mood))
            return 1.0, []
        try:
            story.speak_chatterbox, story.speak_edge = chatter, edge
            story.faster = lambda length, words, mp3: (length, words)
            story.VOICE_ENGINE = "chatterbox"
            story._CHATTER.update(fails=0, used=set(), edge_only=False)
            out = story.speak_lines([(0, "one", "hype"), (1, "two", "sad")], Path(tempfile.mkdtemp()))
        finally:
            story.speak_chatterbox, story.speak_edge, story.faster, story.VOICE_ENGINE = old[:4]
            story._CHATTER.clear()
            story._CHATTER.update(old[4])
        self.assertEqual(sorted(out), [0, 1])
        self.assertEqual(calls[-2:], [("edge", "one", "steady"), ("edge", "two", "steady")])  # read again, one voice

    def test_your_own_music_folders(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        for folder, song in [("hype", "RAVE.mp3"), ("recap-story", "pa.mp3"), ("Highlight", "clip.mp3")]:
            (d / folder).mkdir()
            (d / folder / song).write_bytes(b"x")
        old = story.MUSIC_DIR
        try:
            story.MUSIC_DIR = d
            self.assertEqual(story.pick_music("hype", 1).name, "RAVE.mp3")
            self.assertEqual(story.pick_music("recap", 1).name, "pa.mp3")
            self.assertEqual(story.pick_music("character", 1).name, "pa.mp3")    # no character folder: recap's
            self.assertEqual(story.pick_music("highlight", 1).name, "clip.mp3")
            (d / "Highlight" / "clip.mp3").unlink()
            self.assertIsNone(story.pick_music("highlight", 1))                 # clips only get their own folder
        finally:
            story.MUSIC_DIR = old

    def test_clean_story_checks_times(self):
        story.load_settings({"RECAP_STYLE": "chatty"})
        data = {"character": "", "parts": [
            {"title": "The body", "beats": [{"say": "A man is found dead.", "at": 30},
                                            {"keep": 50, "until": 90},           # too long: trimmed to 10 s
                                            {"say": "", "at": 60},               # empty: dropped
                                            {"say": "Late.", "at": 99999}]},     # past the end: clamped
            {"title": "The clue", "beats": [{"say": "Then a letter arrives.", "at": 200}]}]}
        parts = story.clean_story(data, 600)
        self.assertEqual(len(parts), 2)
        beats = parts[0][2]["beats"]
        self.assertEqual(beats[1], {"keep": 50.0, "until": 60.0})
        self.assertEqual(len(beats), 3)
        self.assertEqual(story.clean_beats([{"say": "Late.", "at": 99999}], 600)[0]["at"], 599.0)
        self.assertTrue(50 < beats[2]["at"] < 200)                         # it went back: placed between its neighbours
        self.assertEqual(parts[1][0], 200.0)

    def test_original_moment_beats_from_the_small_model(self):
        story.load_settings({"RECAP_STYLE": "chatty"})
        data = {"character": "", "parts": [
            {"title": "a", "beats": [{"say": "It begins.", "at": 5}, {"say": "", "at": 20, "until": 26}]},
            {"title": "b", "beats": [{"say": "It ends.", "at": 300}]}]}
        parts = story.clean_story(data, 600)
        self.assertEqual(parts[0][2]["beats"][1], {"keep": 20.0, "until": 26.0})

    def test_long_subtitles_are_read_in_stretches(self):
        cues = [(i * 5.0, i * 5.0 + 2, "x" * 100) for i in range(400)]
        blocks = story.blocks_of(cues, chunk_chars=9000)
        self.assertGreater(len(blocks), 3)
        self.assertEqual(sum(len(b) for b in blocks), len(story.transcript(cues).splitlines()))

    def test_haiku_is_the_default_writer(self):
        self.assertEqual(settings.load(path="nope.json", env={})["story_model"], "claude-haiku-5-5")
        story.MODEL = "claude-haiku-5-5"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), mock.patch.object(story, "claude_app", lambda: None):
            self.assertFalse(story.using_claude())         # no Claude app and no key: nothing to ask
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), mock.patch.object(story, "claude_app", lambda: "claude"):
            self.assertTrue(story.using_claude())          # the Claude app is enough

    def test_music_picks_by_kind_and_keeps_the_credit(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "tracks.json").write_text(json.dumps({"tracks": [
                {"file": "calm.mp3", "kind": "calm", "credit": "Calm by Someone"},
                {"file": "hype.mp3", "kind": "hype", "credit": "Hype by Someone"},
                {"file": "missing.mp3", "kind": "hype", "credit": "never downloaded"}]}))
            for f in ("calm.mp3", "hype.mp3", "mine.mp3"):
                (d / f).write_bytes(b"x")
            with mock.patch.object(story, "MUSIC_DIR", d):
                for seed in range(10):
                    f = story.pick_music("hype", seed)
                    self.assertIn(f.name, ("hype.mp3", "mine.mp3"))
                    self.assertEqual(story.last_credit(), "Hype by Someone" if f.name == "hype.mp3" else None)
                story.MUSIC = False
                self.assertIsNone(story.pick_music("calm"))
                self.assertIsNone(story.last_credit())

    def test_story_window_skips_intro_and_credits(self):
        cues = ([(2.0, 5.0, "Advertise your product or brand here contact www.OpenSubtitles.org today")] +
                [(95.0 + i * 500, 97.0 + i * 500, f"Line {i}") for i in range(10)] +
                [(5000.0, 5003.0, "Last line"), (5100.0, 5104.0, "♪ the end credits song ♪"), (5200.0, 5203.0, "[music]"),
                 (5900.0, 5903.0, "Subtitles by explosiveskull")])
        self.assertEqual(story.story_window(6000, cues), [90.0, 5023.0])    # no dark picture: 20s after the last line
        bright = [120.0] * 6000
        bright[5010:] = [10.0] * 990                                        # fades to the dark end credits
        self.assertEqual(story.story_window(6000, cues, bright), [90.0, 5010.0])
        self.assertEqual(story.story_window(6000, []), [120.0, 5520.0])
        story.WINDOW[:] = [90.0, 5280.0]
        parts = story.clean_story({"parts": [{"title": "a", "beats": [{"say": "Logos.", "at": 10}]},
                                             {"title": "b", "beats": [{"say": "Credits.", "at": 5900}]}]}, 6000)
        self.assertEqual(parts[0][2]["beats"][0]["at"], 90.0)
        self.assertEqual(parts[1][2]["beats"][0]["at"], 5280.0)
        self.assertEqual(parts[1][2]["story_end"], 5280.0)

    def test_dialogue_filter(self):
        self.assertTrue(story.is_dialogue("Where were you last night?"))
        self.assertTrue(story.is_dialogue("<i>(whispers) Run.</i>"))
        for t in ["♪ la la ♪", "[door slams]", "(SIGHS)", "Synced & corrected by -robtor-", "www.addic7ed.com", ""]:
            self.assertFalse(story.is_dialogue(t), t)

    def test_narrated_intro_and_outro(self):
        data = {"intro": [{"say": "You need to see this one.", "at": 3000}, {"say": "Let's get into it.", "at": 3010}],
                "outro": [{"say": "And that's the movie.", "at": 5900}, {"say": "Follow for the next one.", "at": 5905}],
                "parts": [{"title": "a", "beats": [{"say": "It starts here.", "at": 100}]},
                          {"title": "b", "beats": [{"say": "The ending.", "at": 5000}]}]}
        story.WINDOW[:] = [90.0, 5280.0]
        parts = story.clean_story(data, 6000)
        first, last = parts[0][2]["beats"], parts[-1][2]["beats"]
        self.assertEqual([b["say"] for b in first[:3]], ["You need to see this one.", "Let's get into it.", "It starts here."])
        self.assertEqual([b["say"] for b in last[-2:]], ["And that's the movie.", "Follow for the next one."])
        self.assertEqual(last[-1]["at"], 5280.0)                           # the outro's footage stops before the credits
        self.assertIn('"intro"', story.story_prompt("M", 6000, "", "recap", ""))
        self.assertIn("outro", story.STORY_SCHEMA["properties"])

    def test_footage_only_moves_forward(self):
        story.load_settings({"RECAP_PART_SECONDS": "60", "RECAP_STYLE": "fast"})
        story.WINDOW[:] = [0.0, 1e9]
        # the writer keeps going back to the scene at 600 s (the part kept cutting back to it)
        ats = [100, 300, 600, 605, 600, 610, 602, 900, 600, 950]
        data = {"parts": [{"title": "a", "beats": [{"say": " ".join(["word"] * 60), "at": t} for t in ats[:5]]},
                          {"title": "b", "beats": [{"say": " ".join(["word"] * 60), "at": t} for t in ats[5:]]}]}
        parts = story.clean_story(data, 3600)
        got = [b["at"] for _, _, p in parts for b in p["beats"]]
        self.assertEqual(len(got), 10)
        self.assertEqual(got, sorted(got))                                 # never back to an earlier moment
        self.assertEqual([got[0], got[1], got[-1]], [100, 300, 950])

    def test_short_parts_join_up_to_the_chosen_length(self):
        story.load_settings({"RECAP_PART_SECONDS": "180", "NARRATOR_TEMPO": "1.2", "RECAP_STYLE": "fast"})
        self.assertEqual(story.short_part(), 108)
        # ~75 s parts (like the 1:14 one) are now joined in pairs, staying under 5 minutes
        data = {"parts": [{"title": f"p{i}", "beats": [{"say": " ".join(["word"] * 40), "at": i * 100 + k * 10}
                                                        for k in range(8)]} for i in range(6)]}
        parts = story.clean_story(data, 3600)
        self.assertEqual(len(parts), 3)
        for _, _, p in parts:
            self.assertGreaterEqual(story.part_seconds(p["beats"]), story.short_part())
            self.assertLessEqual(story.part_seconds(p["beats"]), story.PART_MAX_SECONDS)

    def test_never_more_than_8_parts(self):
        story.load_settings({"RECAP_PART_SECONDS": "60"})
        data = {"parts": [{"title": f"p{i}", "beats": [{"say": f"s{i} " + "word " * 200, "at": i * 10}]}
                          for i in range(11)]}                  # each part over a minute, so none are joined
        parts = story.clean_story(data, 600)
        self.assertEqual(len(parts), 8)
        self.assertEqual(len(parts[-1][2]["beats"]), 4)            # the extra parts' beats join the last one
        self.assertEqual(story.part_count(20000), 8)
        self.assertEqual(story.part_count(1200), 5)

    def test_clean_story_needs_two_parts(self):
        one = {"parts": [{"title": "x", "beats": [{"say": "s", "at": 1}]}]}
        with self.assertRaises(story.Skip):
            story.clean_story(one, 600)
        long = {"parts": [{"title": "x", "beats": [{"say": f"s{i}", "at": i * 10} for i in range(8)]}]}
        self.assertEqual(len(story.clean_story(long, 600)), 2)          # shared out into two

    def test_clean_hype(self):
        data = {"character": "Sherlock", "edits": [
            {"title": "Sherlock", "setup": [100, 101], "payoff": [400, 420], "montage": [[10, 12], [500, "x"]]}]}
        (a, b, info), = story.clean_hype(data, 3600)
        self.assertEqual(info["setup"], [100.0, 104.0])               # at least 4 s
        self.assertEqual(info["montage"], [[10.0, 12.0], [500.0, 501.0]])   # a bad end gets a 1 s shot
        self.assertEqual((a, b), (100.0, 420.0))

    def test_hype_from_sound_uses_loud_moments(self):
        n = 600
        loud = [-40.0] * n
        for i in range(300, 320):
            loud[i] = -5.0
        motion = [0.0] * n
        (a, b, info), = story.hype_from_sound(n, loud, motion, [0.5] * n)
        self.assertEqual(info["payoff"][0], 300.0)
        self.assertLess(info["setup"][1], info["payoff"][0])
        self.assertTrue(info["montage"])

    def test_narrated_modes_skip_without_a_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(story.Skip):
                story.plan("recap", "Film", 600, [(1, 2, "hi")])

    def test_word_timings(self):
        marks = [(0.0, 0.4, "The", "WordBoundary"), (0.5, 0.9, "butler", "WordBoundary")]
        self.assertEqual(story.word_times("The butler", marks, 1.0), [(0.0, 0.4, "The"), (0.5, 0.9, "butler")])
        spread = story.word_times("did it", [(0.0, 1.0, "did it", "SentenceBoundary")], 1.0)
        self.assertEqual([w for _, _, w in spread], ["did", "it"])
        self.assertAlmostEqual(spread[1][0], 0.5)

    def test_caption_chunks(self):
        words = [(i * 0.3, i * 0.3 + 0.25, w) for i, w in enumerate("the butler did it. or did he".split())]
        c = story.chunks(words)
        self.assertEqual([t for _, _, t in c], ["the butler did", "it.", "or did he"])
        self.assertAlmostEqual(c[0][1], c[1][0])                        # each chunk runs until the next

    def test_montage_pattern_fills_the_time(self):
        steps = story.montage_steps(0.5, 15)
        self.assertGreaterEqual(sum(n for _, n in steps) * 0.5, 15 - 0.05)
        kinds = [k for k, _ in steps]
        self.assertIn("slide3", kinds)
        self.assertEqual(kinds.count("slow"), 1)
        fast = story.montage_steps(0.3, 6)                              # fast songs cut every 2 beats
        self.assertTrue(all(n >= 2 for _, n in fast))

    def test_plot_comes_from_wikipedia(self):
        article = ("Knives Out is a 2019 American mystery film.\n\n== Plot ==\n"
                   + "Harlan is found dead. Blanc investigates. " * 20 + "\n\n== Cast ==\nDaniel Craig")

        def wiki(params):
            if params.get("list") == "search":
                return {"query": {"search": [{"title": "Knives Out (film series)"}, {"title": "Knives Out"}]}}
            text = article if params["titles"] == "Knives Out" else "A film series.\n== Films ==\nx"
            return {"query": {"pages": [{"extract": text}]}}
        with mock.patch.object(story, "_wiki", wiki):
            story._PLOTS.clear()
            plot = story.movie_plot("Knives Out (2019)")
        self.assertTrue(plot.startswith("Harlan is found dead."))
        self.assertNotIn("Daniel Craig", plot)
        self.assertIn("This is what really happens", story.plot_text("Knives Out (2019)"))

    def test_hashtags_use_title_and_cast(self):
        article = ("Runner is a 2026 thriller film.\n\n== Plot ==\n" + "Maia runs across London. " * 20
                   + "\n\n== Cast ==\nGal Gadot as Maia Marten\nDamian Lewis as Victor\nAlfred Enoch\nX\n\n== Production ==\nx")

        def wiki(params):
            if params.get("list") == "search":
                return {"query": {"search": [{"title": "Runner (2026 film)"}]}}
            return {"query": {"pages": [{"extract": article}]}}
        with mock.patch.object(story, "_wiki", wiki):
            story._PLOTS.clear()
            tags = story.movie_hashtags("Runner (2026)")
        self.assertEqual(tags, "#Runner #GalGadot #DamianLewis #AlfredEnoch #movie")
        self.assertLessEqual(len(tags.split()), 5)

    def test_hashtags_fall_back_to_the_writers_cast(self):
        def down(params):
            raise OSError("offline")
        with mock.patch.object(story, "_wiki", down):
            story._PLOTS.clear()
            story.remember_cast("Some Film (2020)", {"cast": ["Ana de Armas", "Chris Evans"]})
            self.assertEqual(story.movie_hashtags("Some Film (2020)"), "#SomeFilm #AnaDeArmas #ChrisEvans #movie #fyp")

    def test_no_plot_when_wikipedia_is_unreachable(self):
        def down(params):
            raise OSError("offline")
        with mock.patch.object(story, "_wiki", down):
            story._PLOTS.clear()
            self.assertIsNone(story.movie_plot("Some Film (2020)"))
            self.assertEqual(story.plot_text("Some Film (2020)"), "")

    def test_narration_is_sped_up_with_its_word_timings(self):
        import subprocess, tempfile
        import process
        story.P = process
        story.load_settings({"NARRATOR_TEMPO": "1.2"})
        with tempfile.TemporaryDirectory() as d:
            mp3 = Path(d) / "line.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=6", str(mp3)], check=True)
            length, words = story.faster(6.0, [(0.0, 1.2, "The"), (3.0, 6.0, "butler.")], mp3)
        self.assertAlmostEqual(length, 5.0, delta=0.15)
        self.assertEqual(words, [(0.0, 1.0, "The"), (2.5, 5.0, "butler.")])
        story.load_settings({"NARRATOR_TEMPO": "1"})
        self.assertEqual(story.faster(6.0, [], "unused.mp3"), (6.0, []))

    def test_funny_dial_direction_and_personal_voice_reach_the_prompt(self):
        story.load_settings({"NARRATOR_FUNNY": "0.9", "SCRIPT_DIRECTION": "roast the villain", "RECAP_STYLE": "chatty"})
        p = story.story_prompt("Film (2020)", 3600, "subs", "recap", "")
        self.assertIn("Full comedy", p)
        self.assertIn("roast the villain", p)
        self.assertIn('with "you" and "we"', p)
        self.assertIn("Talk to the characters directly", p)
        self.assertNotIn("third person", p)
        story.load_settings({"NARRATOR_FUNNY": "0.1", "RECAP_STYLE": "chatty"})
        self.assertIn("Mostly serious", story.story_prompt("Film (2020)", 3600, "subs", "recap", ""))

    def test_jokes_are_kept_and_long_parts_are_split(self):
        story.load_settings({"RECAP_PART_SECONDS": "60", "NARRATOR_TEMPO": "1", "RECAP_STYLE": "chatty"})
        long_part = {"title": "Way too long", "beats": [{"say": " ".join(["word"] * 30), "at": 100 + k * 10,
                                                          **({"joke": True} if k == 0 else {})} for k in range(48)]}
        short = {"title": "Fine", "beats": [{"say": "A clue.", "at": 900}]}
        parts = story.clean_story({"character": "", "parts": [long_part, short]}, 3600)
        self.assertTrue(parts[0][2]["beats"][0].get("joke"))
        self.assertGreaterEqual(len(parts), 2)
        for _, _, p in parts:
            self.assertLessEqual(story.part_seconds(p["beats"]), story.PART_MAX_SECONDS * story.PART_OVERRUN)
        self.assertEqual(sum(len(p["beats"]) for _, _, p in parts), 49)

    def test_parts_run_one_to_five_minutes(self):
        story.load_settings({"RECAP_PART_SECONDS": "30", "NARRATOR_TEMPO": "1", "RECAP_STYLE": "fast"})
        self.assertEqual(story.RECAP_PART_SECONDS, 60)               # the setting can't go under a minute
        story.load_settings({"RECAP_PART_SECONDS": "120", "NARRATOR_TEMPO": "1.2", "RECAP_STYLE": "fast"})
        prompt = story.story_prompt("Film (2020)", 5400, "subs", "recap", "")
        self.assertIn("at least 1 minute", prompt)
        self.assertIn(f"never fewer than {story.part_words(75)}", prompt)

        def part(n_lines, at):
            return {"title": f"P{at}", "beats": [{"say": " ".join(["word"] * 20), "at": at + k * 5} for k in range(n_lines)]}
        # 20 words at 2.6 w/s x1.2 is about 6.5 s a line: 3 lines ~ 20 s, 12 ~ 78 s, 40 ~ 260 s, 70 ~ 455 s
        plan = {"parts": [part(12, 100), part(3, 400), part(12, 700), part(40, 1000), part(3, 2500), part(70, 3000)]}
        parts = story.clean_story(plan, 5400)
        for _, _, p in parts:
            self.assertGreaterEqual(story.part_seconds(p["beats"]), story.PART_MIN_SECONDS)
            self.assertLessEqual(story.part_seconds(p["beats"]), story.PART_MAX_SECONDS * story.PART_OVERRUN)
        self.assertEqual(len(parts), 5)                                # the two 20 s parts joined, the 455 s one split
        self.assertEqual(sum(len(p["beats"]) for _, _, p in parts), 140)
        self.assertIn("cliffhanger", prompt)

    def test_every_line_gets_a_mood_for_its_delivery(self):
        story.load_settings({"NARRATOR_INTENSITY": "0.9", "NARRATOR_SPEED": "+5%"})
        beats = story.clean_beats([{"say": "He opens the door.", "at": 10, "mood": "Tense"},
                                   {"say": "Bad idea.", "at": 20, "joke": True},
                                   {"say": "Wait.", "at": 30, "mood": "nonsense"}], 3600)
        self.assertEqual([b.get("mood") for b in beats], ["tense", "funny", None])
        self.assertEqual(story.mood_of("No way!"), "hype")
        self.assertEqual(story.mood_of("Who did it?"), "curious")
        self.assertEqual(story.mood_of("Wait.", None), story.DEFAULT_MOOD)
        exag, cfg, temp = story.chatterbox_style("shocked")
        self.assertGreater(exag, story.chatterbox_style("calm")[0])
        self.assertTrue(0.3 <= exag <= 1.6 and 0 < cfg < 1)
        rate, pitch, volume = story.edge_style("hype")          # Microsoft voices get it too
        self.assertTrue(rate.endswith("%") and int(rate[:-1]) > 5 and pitch.startswith("+") and pitch.endswith("Hz"))
        self.assertEqual(story.edge_style("sad")[1][0], "-")
        story.load_settings({"NARRATOR_INTENSITY": "0.5"})
        self.assertLess(int(story.edge_style("hype")[1][:-2]), int(pitch[:-2]))   # less expressive when turned down

    def test_the_writer_writes_each_parts_caption(self):
        story.load_settings({"CAPTION_DIRECTION": "ask a question"})
        p = story.story_prompt("Film (2020)", 3600, "subs", "recap", "")
        self.assertIn('"caption"', p)
        self.assertIn("ask a question", p)
        parts = story.clean_story({"parts": [{"title": "A", "caption": "You won't guess who #fyp", "beats": [{"say": "Hi.", "at": 10}]},
                                             {"title": "B", "beats": [{"say": "Bye.", "at": 20}]}]}, 3600)
        self.assertEqual(parts[0][2]["caption"], "You won't guess who")
        story.load_settings({"CAPTION_WRITER": "templates"})
        self.assertNotIn('"caption"', story.story_prompt("Film (2020)", 3600, "subs", "recap", ""))


class PlainSubtitlesTest(unittest.TestCase):
    def test_tags_are_removed(self):
        import process
        self.assertEqual(process.plain('{\\an8}<i>Run!</i>\n<font color="#ffff00">Now.</font>'), "Run!\nNow.")

    def test_long_filter_graphs_go_in_a_file(self):
        import tempfile
        import process
        d = tempfile.mkdtemp()
        self.assertEqual(process.graph_args("null", d), ["-filter_complex", "null"])
        long = process.graph_args("null," * 3000 + "null", d)
        self.assertIn(long[0], ("-/filter_complex", "-filter_complex_script"))
        self.assertTrue(open(long[1]).read().startswith("null,null"))


if __name__ == "__main__":
    unittest.main()
