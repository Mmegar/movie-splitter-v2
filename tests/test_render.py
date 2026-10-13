"""Real renders with ffmpeg (no Whisper): proves the cutter still works end to end."""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

from tests.helpers import ROOT, TempDirTest

HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def make_video(path, seconds):
    """Moving test picture + a beep that goes quiet for a second every 7 seconds (pauses to cut on)."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", f"aevalsrc='0.4*sin(440*2*PI*t)*gt(mod(t,7),1)':s=44100:d={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(path)], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class RenderTest(TempDirTest):
    def base_env(self, settings):
        sf = self.tmp / "settings.json"
        sf.write_text(json.dumps(settings))
        env = {k: v for k, v in os.environ.items()
               if k not in ("MODES", "GITHUB_ACTIONS", "STATUS", "DELETE_ORIGINALS", "VIDEO_QUALITY")}
        env.update(SETTINGS_FILE=str(sf), STATUS_DIR=str(self.tmp / "status"), NTFY_TOPIC="-", PYTHONPATH=str(ROOT))
        return env

    def test_process_local_renders_parts(self):
        src = self.tmp / "Clip.2001.mp4"
        make_video(src, 20)
        env = self.base_env({"modes": ["all"], "part_seconds": 10, "part_max_extra": 4, "auto_subtitles": False,
                             "video_quality": 28})
        code = ("import sys, process; from pathlib import Path; "
                "r = process.process_local(Path(sys.argv[1]), 'Clip (2001)', None, Path('out'), Path('work')); "
                "print('RESULT', {m: [str(f) for f in fs] for m, fs in r.items()})")
        res = subprocess.run([sys.executable, "-c", code, str(src)], cwd=self.tmp, env=env,
                             capture_output=True, text=True, timeout=600)
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-2000:])
        self.assertIn("-crf 28", res.stdout)
        parts = sorted((self.tmp / "out" / "Whole movie").glob("*.mp4"))
        self.assertGreaterEqual(len(parts), 1)
        for p in parts:
            self.assertEqual(probe(p), (1080, 1920))
        st = json.loads((self.tmp / "status" / "state.json").read_text())
        self.assertEqual(st["run"]["stage"], "rendering")

    def test_full_run_with_fake_drive(self):
        drive = self.tmp / "drive"
        (drive / "To edit").mkdir(parents=True)
        make_video(drive / "To edit" / "Test Movie (2001).mp4", 30)
        old = drive / "Ready to post" / "Old Movie" / "Best stretch" / "Old Movie - Part 01.mp4"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old part")
        os.utime(old, (time.time() - 3 * 86400,) * 2)
        keep = drive / "Ready to post" / "Recent" / "Highlights" / "Recent - Part 1.mp4"
        keep.parent.mkdir(parents=True)
        keep.write_bytes(b"new part")
        (self.tmp / "status").mkdir()
        (self.tmp / "status" / "laptop.json").write_text(json.dumps(
            {"uploaded": [{"file": "Test Movie (2001).mp4", "source": "wishlist"}]}))

        env = self.base_env({"modes": ["stretch", "highlights"], "stretch_minutes": 0.4, "stretch_part_seconds": 8,
                             "part_max_extra": 4, "highlight_seconds": 8, "auto_subtitles": False,
                             "delete_originals": "permanent", "cleanup_parts_after_days": 1, "cockroach": True})
        env.update(self.fake_rclone_env(drive))
        env["GITHUB_RUN_ID"] = "42"
        res = subprocess.run([sys.executable, str(ROOT / "process.py")], cwd=self.tmp, env=env,
                             capture_output=True, text=True, timeout=900)
        self.assertEqual(res.returncode, 0, res.stdout[-3000:] + res.stderr[-3000:])

        # parts made in both modes, original gone for good, finished log written, old parts cleaned
        ready = drive / "Ready to post" / "Test Movie (2001)"
        stretch = sorted((ready / "Best stretch").glob("*.mp4"))
        highlights = sorted((ready / "Highlights").glob("*.mp4"))
        self.assertGreaterEqual(len(stretch), 2)
        self.assertGreaterEqual(len(highlights), 1)
        self.assertEqual(probe(stretch[0]), (1080, 1920))
        self.assertFalse((drive / "To edit" / "Test Movie (2001).mp4").exists())
        self.assertIn("deleted for good", (drive / "To edit" / "Finished.txt").read_text())
        self.assertFalse(old.exists())
        self.assertFalse(old.parent.parent.exists())          # empty folders removed
        self.assertTrue(keep.exists())
        calls = [json.loads(l) for l in (self.tmp / "rclone-calls.log").read_text().splitlines()]
        self.assertIn(["deletefile", "--drive-use-trash=false", "gdrive:To edit/Test Movie (2001).mp4"], calls)

        st = json.loads((self.tmp / "status" / "state.json").read_text())
        self.assertEqual(st["run"]["stage"], "done")
        self.assertEqual(st["run"]["run_id"], 42)
        self.assertFalse(st["run"]["active"])
        self.assertEqual(st["queue"], [])
        m = st["movies"][0]
        self.assertEqual((m["name"], m["status"], m["source"]), ("Test Movie (2001)", "done", "wishlist"))
        self.assertAlmostEqual(m["duration"], 30, delta=1)
        sp = m["modes"]["stretch"]["parts"]
        self.assertEqual(len(sp), len(stretch))
        self.assertEqual(m["modes"]["stretch"]["planned"], len(stretch))
        self.assertEqual(sp[0]["drive_path"], f"Ready to post/Test Movie (2001)/Best stretch/{stretch[0].name}")
        self.assertTrue(sp[0]["caption"])
        self.assertGreater(sp[0]["end"], sp[0]["start"])
        types = [e["type"] for e in st["events"]]
        self.assertEqual(types[0], "finished")
        for t in ("new_file", "started", "planned", "part_ready"):
            self.assertIn(t, types)
