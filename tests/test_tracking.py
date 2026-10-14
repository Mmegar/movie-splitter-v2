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
        self.assertGreater(path[15], 0.74)                                      # gets there
        self.assertLess(path[16], 0.3)                                          # new shot: jumps
        self.assertTrue(all(0.1875 - 1e-9 <= p <= 0.8125 + 1e-9 for p in path))  # crop stays in the picture

    def test_crop_has_the_band_shape(self):
        import tracking
        self.assertEqual(tracking.crop_size(1920, 800), (720, 800))
        self.assertEqual(tracking.crop_size(640, 1000), (640, 710))


class StylePartsTest(unittest.TestCase):
    def test_zoom_style_makes_fewer_3_to_5_minute_parts(self):
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
            self.assertTrue(3 <= len(zoom) <= 4, len(zoom))
            self.assertTrue(all(170 <= b - a <= 305 for a, b, _ in zoom), [(b - a) for a, b, _ in zoom])
            self.assertTrue(3 <= len(hl_zoom) <= 5 and len(hl_zoom) < len(hl_classic))
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
        self.assertIn("crop@track=w=360:h=400", filt)
        moves = [l.split() for l in (self.tmp / "track.txt").read_text().splitlines() if l.strip()]
        xs = [(float(m[0]), int(m[-1].rstrip(";"))) for m in moves]
        early = [x for t, x in xs if t < 1]
        late = [x for t, x in xs if t > 5]
        self.assertLess(early[0], 100)                       # starts on the left, where the face is
        self.assertGreater(late[-1], 450)                    # ends on the right, following it
        for t, x in xs:                                       # the face stays in the picture the whole time
            fx = 30 + (W - 270) * t / 6 + 105
            self.assertTrue(x - 40 <= fx <= x + 360 + 40, (t, x, fx))
