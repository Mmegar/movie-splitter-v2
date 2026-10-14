import sys, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import process


class PauseMidStepTest(unittest.TestCase):
    def tearDown(self):
        process.CHECKPOINT = None

    def test_long_tool_is_stopped_when_paused(self):
        calls = []
        def check():
            calls.append(1)
            raise process.RunStopped("paused")
        process.CHECKPOINT = check
        process._LAST_CHECK[0] = 0
        t = time.time()
        with self.assertRaises(process.RunStopped):
            process.run(["sleep", "60"])
        self.assertLess(time.time() - t, 15)          # didn't wait for the whole step
        self.assertEqual(calls, [1])

    def test_runs_normally_when_not_paused(self):
        process.CHECKPOINT = lambda: None
        self.assertEqual(process.run(["true"]).returncode, 0)
        with self.assertRaises(process.subprocess.CalledProcessError):
            process.run(["false"])

    def test_checks_at_most_every_30_seconds(self):
        calls = []
        process.CHECKPOINT = lambda: calls.append(1)
        process._LAST_CHECK[0] = 0
        process.checkpoint(); process.checkpoint(); process.checkpoint()
        self.assertEqual(len(calls), 1)
        process.checkpoint(force=True)
        self.assertEqual(len(calls), 2)
