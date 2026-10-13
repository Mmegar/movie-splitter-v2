"""Shared bits for the tests: paths, a temp-dir base class, and the fake rclone."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE_RCLONE_DIR = Path(__file__).resolve().parent / "fake_rclone"
for p in (ROOT, ROOT / "laptop"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
os.chmod(FAKE_RCLONE_DIR / "rclone", 0o755)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="msplit-test-"))
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_rclone_env(self, drive):
        """Environment where 'rclone' is the fake one, with Drive at `drive`."""
        env = dict(os.environ)
        env["PATH"] = f"{FAKE_RCLONE_DIR}{os.pathsep}{env.get('PATH', '')}"
        env["FAKE_DRIVE"] = str(drive)
        env["FAKE_RCLONE_LOG"] = str(self.tmp / "rclone-calls.log")
        return env
