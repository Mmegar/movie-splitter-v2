"""The storytelling modes' planning and timing logic (no ffmpeg, no network)."""
import os
import unittest
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

    def test_defaults_switch_to_the_new_modes(self):
        s = settings.load(path="nope.json", env={})
        self.assertEqual(s["modes"], ["recap", "character", "hype"])
        self.assertEqual(s["cut_style"], "classic")       # whole picture, no zoom
        self.assertEqual(s["filter"], "vivid")

    def test_settings_reach_the_story_module(self):
        env = {}
        settings.apply_to_env(settings.load(path="nope.json", env={}), env)
        story.load_settings(env)
        self.assertEqual(story.FILTER, "vivid")
        self.assertEqual(story.RECAP_PART_SECONDS, 90)
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

    def test_clean_story_checks_times(self):
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
        self.assertEqual(beats[2]["at"], 599.0)
        self.assertEqual(parts[1][0], 200.0)

    def test_original_moment_beats_from_the_small_model(self):
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


if __name__ == "__main__":
    unittest.main()
