"""Shared bits for the tests: paths and a temp-dir base class."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "laptop", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
# the fake GitHub runs on this computer: never send it through a proxy
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="msplit-test-"))
        self._env = dict(os.environ)
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)
