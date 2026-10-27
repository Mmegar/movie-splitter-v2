"""Lining a downloaded .srt up with when people talk in the movie (no Whisper: a made-up speech timeline)."""
import random
import unittest
from unittest import mock

import numpy as np

import process as P


def movie(seconds=1800, seed=1):
    """Spoken lines at random times, and the speech timeline they make."""
    rnd = random.Random(seed)
    cues, t = [], 5.0
    while t < seconds - 10:
        length = rnd.uniform(1.0, 4.0)
        cues.append((t, t + length, "line"))
        t += length + rnd.uniform(0.5, 12.0)
    talk = np.zeros(int(seconds / P.SYNC_STEP) + 1, dtype=bool)
    for a, b, _ in cues:
        talk[int(a / P.SYNC_STEP):int(b / P.SYNC_STEP) + 1] = True
    return cues, talk


class SubSyncTest(unittest.TestCase):
    def test_in_sync_stays(self):
        cues, talk = movie()
        speed, shift, share, chance = P.fit_cues(cues, talk)
        self.assertEqual(speed, 1.0)
        self.assertAlmostEqual(shift, 0.0, delta=0.15)
        self.assertGreater(share, 0.95)

    def test_finds_offset(self):
        cues, talk = movie()
        late = [(a + 7.3, b + 7.3, t) for a, b, t in cues]        # another release: 7.3 s late
        speed, shift, _, _ = P.fit_cues(late, talk)
        self.assertEqual(speed, 1.0)
        self.assertAlmostEqual(shift, -7.3, delta=0.15)

    def test_finds_frame_rate(self):
        cues, talk = movie()
        f = 23.976 / 25                                               # a 25 fps release's subtitles
        other = [(a * f - 2.0, b * f - 2.0, t) for a, b, t in cues]
        speed, shift, _, _ = P.fit_cues(other, talk)
        self.assertAlmostEqual(speed, 25 / 23.976, places=4)
        fixed = [(a * speed + shift, t) for a, _, t in other]
        self.assertTrue(all(abs(x - c[0]) < 0.3 for (x, _), c in zip(fixed, cues)))

    def test_wrong_movie_is_dropped(self):
        cues, talk = movie(seed=1)
        wrong, _ = movie(seed=2)
        with mock.patch.object(P, "speech_timeline", return_value=talk):
            self.assertEqual(P.sync_cues(wrong, "movie.mkv", "."), [])
            moved = P.sync_cues([(a + 3, b + 3, t) for a, b, t in cues], "movie.mkv", ".")
        self.assertAlmostEqual(moved[0][0], cues[0][0], delta=0.15)


if __name__ == "__main__":
    unittest.main()
