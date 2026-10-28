import time
import unittest

import experiments as ex

NOW = 1_800_000_000.0
OLD = NOW - 3 * 86400          # old enough to count


def vids(movie_arms, views, mode="recap", created=OLD):
    """One video per (movie, views) pair."""
    return [{"id": f"{m}{i}", "movie": m, "mode": mode, "part": i, "views": v, "avg_watch": 10.0,
             "created": created} for i, (m, v) in enumerate(zip(movie_arms, views))]


class ValuesTest(unittest.TestCase):
    def test_clean_value(self):
        self.assertEqual(ex.clean_value("narrator_tempo", "1.3x"), "1.3")
        self.assertIsNone(ex.clean_value("narrator_tempo", "2"))
        self.assertEqual(ex.clean_value("recap_part_seconds", 999), 300)
        self.assertIsNone(ex.clean_value("thumbnail_badge", "NO NUMBER"))
        self.assertEqual(ex.clean_value("posting.hours", "17:00-23:30"), "17:00-23:30")
        self.assertIsNone(ex.clean_value("posting.hours", "22:00-23:00"))     # under 4 hours
        self.assertEqual(ex.clean_value("posting.hashtags", "#a #b nope #c"), "#a #b #c")

    def test_apply_changes(self):
        s = {"posting": {"modes": ["recap", "stretch", "hype"]}}
        ex.apply_changes(s, {"posting.hours": "12:00-20:00", "mode_first": "stretch", "narrator_tempo": "1.3"})
        self.assertEqual(s["posting"]["active_from"], "12:00")
        self.assertEqual(s["posting"]["modes"], ["stretch", "recap", "hype"])
        self.assertEqual(s["narrator_tempo"], "1.3")


class GuardTest(unittest.TestCase):
    def test_viral_mode_is_protected_and_kept(self):
        v = vids(["A"] * 9, [100] * 9) + vids(["S", "S"], [250000, 90000], mode="stretch")
        prot = ex.protected_modes(v, keep=())
        self.assertIn("stretch", prot)
        self.assertIn("stretch", ex.protected_modes([], keep=("stretch",)))
        # Claude asks to drop stretch: it stays
        self.assertIsNone(ex.guard_modes(["recap"], ["recap", "stretch"], v, prot))

    def test_drop_only_with_proof(self):
        v = vids(["A"] * 9, [1000] * 9) + vids(["H"] * 3, [10] * 3, mode="highlights")
        # only 3 highlights videos: not enough proof, kept
        self.assertIsNone(ex.guard_modes(["recap"], ["recap", "highlights"], v, set()))
        v += vids(["H"] * 6, [10] * 6, mode="highlights")
        self.assertEqual(ex.guard_modes(["recap"], ["recap", "highlights"], v, set()), ["recap"])
        # adding a mode is fine
        self.assertEqual(ex.guard_modes(["recap", "stretch"], ["recap"], v, set()), ["recap", "stretch"])

    def test_holds(self):
        movies = {"Flop": {"videos": 3, "modes": {"recap": 30}}, "Hit": {"videos": 1, "modes": {"stretch": 9}}}
        h = ex.clean_holds([{"movie": "Flop", "mode": "recap", "why": "low"},
                            {"movie": "Hit", "mode": "stretch"}, {"movie": "Nope", "mode": "recap"}],
                           [], movies, {"stretch"}, now=NOW)
        self.assertEqual([(x["movie"], x["mode"]) for x in h], [("Flop", "recap")])
        self.assertTrue(ex.is_held(h, "Flop", "recap", now=NOW))
        self.assertFalse(ex.is_held(h, "Flop", "recap", now=NOW + 8 * 86400))


class ExperimentTest(unittest.TestCase):
    settings = {"narrator_tempo": 1.2, "posting": {"modes": ["recap", "stretch"], "hashtags": "#movie"}}

    def start(self, prop):
        st, ch, note = ex.step({}, self.settings, [], prop, now=OLD - 86400)
        return st

    def test_arm_a_is_current_and_movies_alternate(self):
        st = self.start({"factor": "narrator_tempo", "arms": ["1.3", "1.2"], "why": "faster?"})
        e = st["current"]
        self.assertEqual([a["value"] for a in e["arms"]], ["1.2", "1.3"])
        got = [ex.movie_overrides(e, m)["narrator_tempo"] for m in ("M1", "M2", "M3", "M4", "M1")]
        self.assertEqual(got, ["1.2", "1.3", "1.2", "1.3", "1.2"])

    def test_winner_becomes_the_setting_and_next_starts(self):
        st = self.start({"factor": "narrator_tempo", "arms": ["1.3"]})
        e = st["current"]
        movies = [f"M{i}" for i in range(12)]
        for m in movies:
            ex.movie_overrides(e, m)
        views = [1000 if e["assign"][m] == 1 else 400 for m in movies]
        st, changes, note = ex.step(st, self.settings, vids(movies, views), None, now=NOW)
        self.assertEqual(changes, {"narrator_tempo": "1.3"})
        self.assertIn("won", note)
        self.assertEqual(st["history"][-1]["winner"], "B")
        self.assertIsNotNone(st["current"])                  # the next experiment started
        self.assertNotEqual(st["current"]["factor"], "narrator_tempo")

    def test_too_close_keeps_setting(self):
        st = self.start({"factor": "narrator_tempo", "arms": ["1.3"]})
        e = st["current"]
        movies = [f"M{i}" for i in range(24)]
        for m in movies:
            ex.movie_overrides(e, m)
        st, changes, note = ex.step(st, self.settings, vids(movies, [500 + i for i in range(24)]), None, now=NOW)
        self.assertEqual(changes, {})
        self.assertEqual(st["history"][-1]["status"], "no_difference")

    def test_young_videos_dont_count(self):
        st = self.start({"factor": "narrator_tempo", "arms": ["1.3"]})
        e = st["current"]
        movies = [f"M{i}" for i in range(12)]
        for m in movies:
            ex.movie_overrides(e, m)
        st, changes, _ = ex.step(st, self.settings, vids(movies, [9] * 12, created=NOW - 3600), None, now=NOW)
        self.assertEqual(st["current"]["status"], "running")
        self.assertEqual(st["current"]["progress"], 0)

    def test_mode_first_never_removes(self):
        st = self.start({"factor": "mode_first", "arms": []})
        e = st["current"]
        self.assertEqual([a["value"] for a in e["arms"]], ["recap", "stretch"])
        v = vids(["A"] * 6, [100] * 6, created=OLD) + vids(["S"] * 6, [5000] * 6, mode="stretch", created=OLD)
        st, changes, _ = ex.step(st, self.settings, v, None, now=NOW)
        self.assertEqual(changes, {"mode_first": "stretch"})
        s = ex.apply_changes(dict(self.settings, posting=dict(self.settings["posting"])), changes)
        self.assertEqual(s["posting"]["modes"], ["stretch", "recap"])

    def test_day_level_and_post_level(self):
        st = self.start({"factor": "posting.hours", "arms": ["17:00-23:30"]})
        e = st["current"]
        self.assertEqual(ex.day_overrides(e, "2026-10-28")["posting.hours"], "09:00-23:30")
        self.assertEqual(ex.day_overrides(e, "2026-10-29")["posting.hours"], "17:00-23:30")
        ex.post_overrides(e, ex.post_key("M", "recap", 1), "2026-10-29")
        self.assertEqual(e["assign"]["M|recap|1"], 1)
        self.assertEqual(ex.testing_keys(st), {"posting.active_from", "posting.active_to"})

    def test_switched_off_stops(self):
        st = self.start({"factor": "narrator_tempo", "arms": ["1.3"]})
        st, changes, note = ex.step(st, self.settings, [], None, enabled=False, now=NOW)
        self.assertIsNone(st.get("current"))
        self.assertEqual(st["history"][-1]["status"], "stopped")

    def test_bad_proposal_falls_back_to_plan(self):
        st = self.start({"factor": "delete_everything", "arms": [1]})
        self.assertEqual(st["current"]["factor"], ex.DEFAULT_PLAN[0]["factor"])
        self.assertIsNotNone(ex.public(st)["current"])


if __name__ == "__main__":
    unittest.main()
