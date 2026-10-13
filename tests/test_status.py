import json
import os
import subprocess

from tests.helpers import ROOT, TempDirTest

import status


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class LocalStatusTest(TempDirTest):
    def setUp(self):
        super().setUp()
        for k in ("GITHUB_ACTIONS", "STATUS"):
            os.environ.pop(k, None)
        os.environ["STATUS_DIR"] = str(self.tmp / "status")
        status.reset()

    def tearDown(self):
        status.reset()
        super().tearDown()

    def read(self):
        return json.loads((self.tmp / "status" / "state.json").read_text())

    def test_full_movie_lifecycle_matches_contract(self):
        status.run_started(123)
        status.set_queue([{"name": "b.mkv", "source": "laptop", "added": "2026-10-13T10:00:00Z", "size_mb": 5}])
        status.set_stage("downloading", movie="Movie (2001)", mode=None)
        status.movie_started("Movie (2001)", "laptop", ["stretch", "highlights"])
        status.movie_duration("Movie (2001)", 5400.04)
        status.movie_planned("Movie (2001)", "stretch", 2)
        status.set_stage("rendering", mode="stretch", part=1, parts_total=2)
        status.part_ready("Movie (2001)", "stretch", 1, 10.04, 95.5, "Ready to post/Movie (2001)/Best stretch/p1.mp4",
                          "cap #fyp")
        status.add_event("part_ready", "[Best stretch] Movie (2001): Part 1 of 2 ready", "Part 1: ...")
        status.movie_finished("Movie (2001)", "from your .srt file")
        status.run_finished()

        st = self.read()
        sample = json.loads((ROOT / "plan" / "sample-state.json").read_text())
        self.assertEqual(set(st), set(sample))
        self.assertEqual(set(st["run"]), set(sample["run"]))
        self.assertEqual(st["version"], 1)
        self.assertEqual(st["run"]["run_id"], 123)
        self.assertEqual(st["run"]["stage"], "done")
        self.assertFalse(st["run"]["active"])
        m = st["movies"][0]
        self.assertEqual(set(m), set(sample["movies"][0]) | {"duration"})   # duration: CONTRACT-CHANGES.md
        self.assertEqual(m["id"], "movie-2001")
        self.assertEqual(m["status"], "done")
        self.assertEqual(m["duration"], 5400.0)
        self.assertEqual(m["modes"]["stretch"]["label"], "Best stretch")
        self.assertEqual(m["modes"]["stretch"]["planned"], 2)
        self.assertEqual(m["modes"]["highlights"]["parts"], [])
        part = m["modes"]["stretch"]["parts"][0]
        self.assertEqual(set(part), set(sample["movies"][0]["modes"]["stretch"]["parts"][0]))
        self.assertEqual(part["seconds"], 85.5)
        self.assertEqual(part["post"]["status"], "none")
        self.assertEqual(st["events"][0]["type"], "part_ready")
        self.assertIn("+", st["updated"][19:] + "+")   # has an offset

    def test_movie_id_like_sample(self):
        self.assertEqual(status.movie_id("The Man from U.N.C.L.E. (2015)"), "the-man-from-uncle-2015")

    def test_failed_movie_and_unknown_values(self):
        status.movie_started("X", "drive", ["all"])
        status.movie_failed("X", RuntimeError("boom"))
        status.set_stage("nonsense")
        status.add_event("weird", "t", "m")
        st = self.read()
        self.assertEqual(st["movies"][0]["status"], "failed")
        self.assertEqual(st["movies"][0]["error"], "boom")
        self.assertEqual(st["run"]["stage"], "idle")
        self.assertEqual(st["events"][0]["type"], "info")

    def test_limits(self):
        for i in range(210):
            status.add_event("info", f"e{i}")
        for i in range(55):
            status.movie_started(f"Movie {i}", "drive", ["stretch"])
        st = self.read()
        self.assertEqual(len(st["events"]), 200)
        self.assertEqual(st["events"][0]["title"], "e209")      # newest first
        self.assertEqual(len(st["movies"]), 50)
        self.assertEqual(st["movies"][0]["name"], "Movie 54")

    def test_set_post_counts_attempts(self):
        status.movie_started("M", "drive", ["stretch"])
        status.part_ready("M", "stretch", 1, 0, 60, "Ready to post/M/Best stretch/1.mp4")
        status.set_post("m", "stretch", 1, "failed", error="nope")
        status.set_post("m", "stretch", 1, "posted", url="https://x")
        post = self.read()["movies"][0]["modes"]["stretch"]["parts"][0]["post"]
        self.assertEqual((post["status"], post["url"], post["error"], post["attempts"]), ("posted", "https://x", None, 2))

    def test_errors_never_raise(self):
        status.store().dir = self.tmp / "missing" / "deeper"   # writing will fail
        status.add_event("info", "x")                          # must not raise
        status.part_ready("no such movie", "stretch", 1, 0, 1, "p")

    def test_off(self):
        status.reset()
        os.environ["STATUS"] = "off"
        status.add_event("info", "x")
        self.assertFalse((self.tmp / "status" / "state.json").exists())


class GitStatusTest(TempDirTest):
    """The GitHub mode, against a local bare repo standing in for GitHub."""

    def setUp(self):
        super().setUp()
        self.remote = self.tmp / "remote.git"
        git("init", "-q", "--bare", str(self.remote))
        os.environ.update(GITHUB_ACTIONS="true", GITHUB_TOKEN="t", GITHUB_REPOSITORY="o/r",
                          STATUS_REMOTE_URL=str(self.remote), STATUS_DIR=str(self.tmp / "work"),
                          STATUS_PUSH_EVERY="0")
        os.environ.pop("STATUS", None)
        status.reset()

    def tearDown(self):
        status.reset()
        super().tearDown()

    def remote_file(self, name):
        return json.loads(git("--git-dir", str(self.remote), "show", f"status:{name}"))

    def test_creates_orphan_branch_and_replays_after_conflict(self):
        status.run_started(1)                                    # makes the branch
        self.assertTrue(self.remote_file("state.json")["run"]["active"])

        # the laptop saves laptop.json in between (from another clone)
        other = self.tmp / "other"
        git("clone", "-q", "-b", "status", str(self.remote), str(other))
        (other / "laptop.json").write_text(json.dumps({"version": 1, "online": True}))
        git("-c", "user.name=l", "-c", "user.email=l@x", "add", "laptop.json", cwd=other)
        git("-c", "user.name=l", "-c", "user.email=l@x", "commit", "-qm", "laptop", cwd=other)
        git("push", "-q", "origin", "status", cwd=other)

        status.movie_started("Film", "laptop", ["stretch"])     # push is rejected, then redone on top
        status.movie_finished("Film")
        st = self.remote_file("state.json")
        self.assertEqual(st["movies"][0]["status"], "done")
        self.assertTrue(st["run"]["active"])
        self.assertEqual(self.remote_file("laptop.json")["online"], True)   # the laptop's file survived
        log = git("--git-dir", str(self.remote), "log", "--format=%P", "status").split("\n")
        self.assertEqual(log[-2], "")                            # first commit has no parent (orphan)
        self.assertEqual(status.read_laptop()["online"], True)

    def test_coalesces_pushes(self):
        os.environ["STATUS_PUSH_EVERY"] = "3600"
        status.PUSH_EVERY = 3600
        try:
            status.run_started(1)                                # urgent: pushed
            status.set_stage("analysing", movie="A")             # not urgent: waits
            self.assertEqual(self.remote_file("state.json")["run"]["stage"], "idle")
            status.movie_failed("A", "x")                        # urgent: pushes everything
            self.assertEqual(self.remote_file("state.json")["run"]["stage"], "analysing")
            status.set_stage("rendering")
            status.flush()
            self.assertEqual(self.remote_file("state.json")["run"]["stage"], "rendering")
        finally:
            status.PUSH_EVERY = 0
