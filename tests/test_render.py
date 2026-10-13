"""Real renders with ffmpeg (no Whisper): proves the cutter still works end to end."""
import json
import os
import shutil
import subprocess
import sys
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
