"""Zoomed + tracking style: the camera path, part lengths, and a real render that follows a face."""
import json
import os
import shutil
import subprocess
import sys
import unittest

from tests.helpers import ROOT, TempDirTest

try:
    import cv2  # noqa: F401
    import numpy as np
    HAVE_CV = True
except ImportError:
    HAVE_CV = False
try:
    import skimage.data
    HAVE_FACE = True
except ImportError:
    HAVE_FACE = False
HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


@unittest.skipUnless(HAVE_CV, "OpenCV not installed")
class CameraPathTest(unittest.TestCase):
    def test_holds_still_for_small_moves_glides_after_big_ones_and_jumps_at_a_new_shot(self):
        import tracking
        pts = [0.50, 0.52, 0.49, 0.51] + [0.8] * 12 + [0.2] * 6
        cuts = [True] + [False] * 15 + [True] + [False] * 5
        path = tracking.camera_path(pts, cuts, view=0.375)
        self.assertTrue(all(abs(p - path[0]) < 0.02 for p in path[:3]))      # wobble ignored
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(path[3:16], path[4:16])))   # glides right
        self.assertGreater(path[15], 0.66)                                      # 0.8 is back in the frame
        self.assertLess(path[16], 0.3)                                          # new shot: jumps
        self.assertTrue(all(0.1875 - 1e-9 <= p <= 0.8125 + 1e-9 for p in path))  # crop stays in the picture

    def test_two_people_talking_who_fit_in_the_frame_get_one_still_shot(self):
        import tracking
        faces = []
        for i in range(30):                      # they take turns talking: mouths move alternately
            a_talks = (i // 5) % 2 == 0
            faces.append([(0.42, 0.07, 0.2 if a_talks else 0.0), (0.60, 0.07, 0.0 if a_talks else 0.2)])
        pts = tracking.plan_shot(faces, [None] * 30, view=0.375)
        self.assertEqual(len(set(round(p, 4) for p in pts)), 1)     # one framing, held
        self.assertAlmostEqual(pts[0], 0.51, places=2)                # both in it

    def test_people_too_far_apart_the_camera_stays_on_one_of_them(self):
        import tracking
        faces = []
        for i in range(30):
            a_talks = (i // 5) % 2 == 0
            faces.append([(0.15, 0.07, 0.25 if a_talks else 0.0), (0.85, 0.07, 0.0 if a_talks else 0.1)])
        pts = tracking.plan_shot(faces, [None] * 30, view=0.375)
        self.assertEqual(set(pts), {0.15})                            # never swings over to the other one
        path = tracking.camera_path(pts, [True] + [False] * 29, 0.375)
        self.assertTrue(max(path) - min(path) < 1e-9)

    def test_one_person_walking_is_followed(self):
        import tracking
        faces = [[(0.2 + 0.02 * i, 0.07, 0.0)] for i in range(30)]
        pts = tracking.plan_shot(faces, [None] * 30, view=0.375)
        self.assertAlmostEqual(pts[0], 0.2)
        self.assertAlmostEqual(pts[-1], 0.78)

    def test_no_panning_when_the_subject_stays_in_frame(self):
        import tracking
        pts = [0.45, 0.5, 0.55, 0.52, 0.48, 0.58, 0.44] * 4         # someone shifting about
        path = tracking.camera_path(pts, [True] + [False] * 27, view=0.375)
        self.assertEqual(len(set(path)), 1)

    def test_no_jump_at_a_new_shot_when_the_framing_barely_changes(self):
        import tracking
        pts = [0.50] * 10 + [0.55] * 10                     # new shot, subject almost in the same place
        path = tracking.camera_path(pts, [True] + [False] * 9 + [True] + [False] * 9, view=0.375)
        self.assertEqual(len(set(path)), 1)

    def test_carries_on_from_the_last_piece_and_pans_instead_of_jumping(self):
        import tracking
        pts = [0.62] * 20                                     # same scene, subject a bit to the right
        path = tracking.camera_path(pts, [True] + [False] * 19, view=0.375, prev=0.5)
        self.assertAlmostEqual(path[0], 0.5, delta=0.02)     # starts where the last piece stopped
        steps = [b - a for a, b in zip(path, path[1:])]
        self.assertTrue(all(0 <= d <= 0.375 * tracking.PAN_MAX + 1e-9 for d in steps))   # a slow, steady pan
        self.assertGreater(path[-1], 0.58)

    def test_finds_burned_in_subtitles(self):
        import cv2
        import tracking
        frame = np.full((270, 480), 40, np.uint8)
        cv2.putText(frame, "Where were you last night?", (70, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 6)
        cv2.putText(frame, "Where were you last night?", (70, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 2)
        top, bottom = tracking.text_rows(frame)
        self.assertTrue(215 <= top < bottom <= 250)
        self.assertIsNone(tracking.text_rows(np.full((270, 480), 40, np.uint8)))

    def test_a_flash_is_not_a_new_shot(self):
        import cv2
        import tracking
        rng = np.random.default_rng(1)
        scene = cv2.GaussianBlur(rng.integers(0, 120, (90, 160)).astype(np.uint8), (9, 9), 0)
        flash = cv2.convertScaleAbs(scene, alpha=1.6, beta=90)      # same scene, lights flash brighter
        h = lambda g: cv2.normalize(cv2.calcHist([g], [0], None, [32], [0, 256]), None)
        self.assertFalse(tracking.is_cut(h(scene), h(flash), scene, flash))
        other = cv2.GaussianBlur(rng.integers(100, 255, (90, 160)).astype(np.uint8), (9, 9), 0)
        self.assertTrue(tracking.is_cut(h(scene), h(other), scene, other))   # a different picture

    def test_crop_has_the_band_shape(self):
        import tracking
        self.assertEqual(tracking.crop_size(1920, 800), (1000, 800))
        self.assertEqual(tracking.crop_size(640, 1000), (640, 512))
        self.assertEqual(tracking.crop_size(1920, 1080, (1080, 1350)), (864, 1080))   # the story modes' 4:5 zoom

    def test_the_main_character_wins_over_a_face_in_the_background(self):
        import tracking
        faces = [[(0.30, 0.20, 0.0), (0.80, 0.05, 0.1)] for _ in range(20)]   # big lead, small extra behind
        pts = tracking.plan_shot(faces, [None] * 20, view=0.45)
        self.assertEqual(set(pts), {0.30})


class StylePartsTest(unittest.TestCase):
    def test_zoom_style_makes_at_least_5_parts_of_3_to_5_minutes(self):
        sys.path.insert(0, str(ROOT))
        import process
        try:
            process.ENDINGS[:] = []
            total = 7200.0
            cands = [float(t) for t in range(0, 7200, 7)]
            score = [0.5] * 7200
            process.apply_style("classic")
            classic = process.choose_stretch(total, score, cands)
            hl_classic = process.choose_highlights(total, score, cands)
            process.apply_style("zoom")
            zoom = process.choose_stretch(total, score, cands)
            hl_zoom = process.choose_highlights(total, score, cands)
            self.assertLess(len(zoom), len(classic))
            self.assertTrue(5 <= len(zoom) <= 6, len(zoom))             # at least 5 parts
            self.assertTrue(all(170 <= b - a <= 305 for a, b, _ in zoom), [(b - a) for a, b, _ in zoom])
            self.assertTrue(5 <= len(hl_zoom) <= 6 and len(hl_zoom) < len(hl_classic))
            self.assertTrue(all(170 <= b - a <= 305 for a, b, _ in hl_zoom))
        finally:
            process.apply_style("classic")


@unittest.skipUnless(HAVE_CV and HAVE_FACE and HAVE_FFMPEG, "needs OpenCV, scikit-image and ffmpeg")
class FollowsFaceTest(TempDirTest):
    def test_crop_follows_a_face_walking_across(self):
        import tracking
        face = cv2.resize(cv2.cvtColor(skimage.data.astronaut(), cv2.COLOR_RGB2BGR)[0:400, 100:420], (210, 262))
        W, H = 960, 400
        src = self.tmp / "walk.mp4"
        p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                              "-s", f"{W}x{H}", "-r", "25", "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                              str(src)], stdin=subprocess.PIPE)
        for i in range(25 * 6):
            img = np.full((H, W, 3), (60, 90, 40), np.uint8)
            x = int(30 + (W - 270) * i / (25 * 6))
            img[70:70 + 262, x:x + 210] = face
            p.stdin.write(img.tobytes())
        p.stdin.close()
        p.wait()
        filt = tracking.plan(src, 0, 6, "", W, H, self.tmp)
        self.assertIn("crop@track=w=500:h=400", filt)
        moves = [l.split() for l in (self.tmp / "track.txt").read_text().splitlines() if l.strip()]
        xs = [(float(m[0]), int(m[-1].rstrip(";"))) for m in moves]
        early = [x for t, x in xs if t < 1]
        late = [x for t, x in xs if t > 5]
        self.assertLess(early[0], 100)                       # starts on the left, where the face is
        self.assertGreater(late[-1], 380)                    # ends on the right, following it
        for t, x in xs:                                       # the face stays in the picture the whole time
            fx = 30 + (W - 270) * t / 6 + 105
            self.assertTrue(x - 40 <= fx <= x + 500 + 40, (t, x, fx))
