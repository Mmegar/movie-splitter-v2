"""Parts must never carry the movie's chapter list (phones showed '1 hr' and stopped after the real part)."""
import json
import shutil
import subprocess
import sys
import unittest

from tests.helpers import ROOT, TempDirTest

HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def streams(path):
    """Track kinds, and the longest track (what the iPhone shows as the length)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    j = json.loads(out)
    return [s["codec_type"] for s in j["streams"]], max(float(s.get("duration") or 0) for s in j["streams"])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class ChapterTest(TempDirTest):
    def movie_with_chapters(self):
        meta = self.tmp / "chapters.txt"
        meta.write_text(";FFMETADATA1\n" + "".join(
            f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={k * 60000}\nEND={(k + 1) * 60000}\ntitle=Chapter {k + 1}\n"
            for k in range(10)))
        src = self.tmp / "movie.mkv"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x272:rate=24:duration=600",
                        "-f", "lavfi", "-i", "sine=duration=600", "-i", str(meta), "-map_metadata", "2",
                        "-map", "0", "-map", "1", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                        str(src)], check=True)
        return src

    def test_old_style_part_is_repaired_and_new_parts_have_no_chapter_track(self):
        src = self.movie_with_chapters()
        old = self.tmp / "old.mp4"                 # how parts used to be made: chapters copied in
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "120", "-t", "75", "-i", str(src),
                        "-map", "0:v", "-map", "0:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-movflags", "+faststart",
                        str(old)], check=True)
        kinds, length = streams(old)
        self.assertIn("data", kinds)
        self.assertGreater(length, 400)            # the bug: looks as long as the rest of the movie
        sys.path.insert(0, str(ROOT))
        import mp4fix
        self.assertTrue(mp4fix.fix_file(old))
        kinds, length = streams(old)
        self.assertEqual(kinds, ["video", "audio"])
        self.assertAlmostEqual(length, 75, delta=1)
        self.assertEqual(subprocess.run(["ffmpeg", "-v", "error", "-i", str(old), "-f", "null", "-"],
                                        capture_output=True, text=True).stderr, "")   # still plays cleanly
        self.assertFalse(mp4fix.fix_file(old))     # nothing more to do

        code = ("import sys, os; os.environ['STATUS']='off'; import process; from pathlib import Path; "
                "process.COCKROACH=False; process.render_part(Path(sys.argv[1]), 120, 195, 1, [], Path(sys.argv[2]), Path(sys.argv[3]))")
        new = self.tmp / "new.mp4"
        res = subprocess.run([sys.executable, "-c", code, str(src), str(new), str(self.tmp)], cwd=ROOT,
                             capture_output=True, text=True, timeout=600)
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        kinds, length = streams(new)
        self.assertEqual(kinds, ["video", "audio"])
        self.assertAlmostEqual(length, 75, delta=1)
