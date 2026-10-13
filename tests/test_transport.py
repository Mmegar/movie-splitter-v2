import json
import os

from tests.fake_github import FakeGitHub
from tests.helpers import TempDirTest

import ghapi
import status
import transport

class TransportTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.fake = FakeGitHub()
        self.gh = ghapi.GitHub("o/r", "secret-token", self.fake.api)

    def tearDown(self):
        self.fake.close()
        super().tearDown()

    def test_send_and_receive_in_chunks(self):
        src = self.tmp / "movie.mkv"
        src.write_bytes(os.urandom(50_000))
        rel = self.gh.create_release("incoming-1", "x", "{}")
        seen = []
        names = transport.send(self.gh, rel, src, "movie", chunk_size=12_000, progress=seen.append)
        self.assertEqual(names, [f"movie.{i:03}" for i in range(5)])
        self.assertEqual(seen[-1], 100)
        self.assertEqual(seen, sorted(seen))
        assets = self.gh.assets(rel["id"])
        self.assertTrue(all(a["size"] <= 12_000 for a in assets))
        got = []
        out = transport.receive(self.gh, transport.assets_for(assets, "movie"), self.tmp / "back.mkv", got.append)
        self.assertEqual(out.read_bytes(), src.read_bytes())
        self.assertEqual(got[-1], 100)
        self.assertFalse((self.tmp / "back.mkv.part").exists())
        # the download redirect goes to another host, which must not see the token
        self.assertTrue(self.fake.auth_seen_on_download)
        self.assertEqual(set(self.fake.auth_seen_on_download), {None})

    def test_resume_after_failed_chunk(self):
        src = self.tmp / "movie.mkv"
        src.write_bytes(os.urandom(30_000))
        rel = self.gh.create_release("incoming-2", "x", "{}")
        self.fake.fail_upload_names = {"movie.001"}
        with self.assertRaises(ghapi.GitHubError):
            transport.send(self.gh, rel, src, "movie", chunk_size=10_000)
        self.assertEqual([a["name"] for a in self.gh.assets(rel["id"])], ["movie.000"])
        first_id = self.gh.assets(rel["id"])[0]["id"]
        uploads_before = sum(1 for m, p in self.fake.requests if p.startswith("/uploads/"))
        transport.send(self.gh, rel, src, "movie", self.gh.assets(rel["id"]), chunk_size=10_000)
        assets = self.gh.assets(rel["id"])
        self.assertEqual([a["name"] for a in assets], ["movie.000", "movie.001", "movie.002"])
        self.assertEqual(assets[0]["id"], first_id)                       # not uploaded again
        self.assertEqual(sum(1 for m, p in self.fake.requests if p.startswith("/uploads/")) - uploads_before, 2)
        transport.receive(self.gh, transport.assets_for(assets, "movie"), self.tmp / "b")
        self.assertEqual((self.tmp / "b").read_bytes(), src.read_bytes())

    def test_empty_file_and_missing_piece(self):
        empty = self.tmp / "empty.srt"
        empty.write_bytes(b"")
        rel = self.gh.create_release("incoming-3", "x", "{}")
        self.assertEqual(transport.send(self.gh, rel, empty, "subs"), ["subs.000"])
        out = transport.receive(self.gh, self.gh.assets(rel["id"]), self.tmp / "e.srt")
        self.assertEqual(out.read_bytes(), b"")
        # a piece that arrives short is an error, and leaves no half file behind
        piece = self.gh.assets(rel["id"])[0]
        with self.assertRaises(IOError):
            transport.receive(self.gh, [dict(piece, size=5)], self.tmp / "bad")
        self.assertFalse((self.tmp / "bad").exists())
        self.assertFalse((self.tmp / "bad.part").exists())

    def test_release_listing_pages_and_body(self):
        for i in range(105):
            self.gh.create_release(f"t{i}", "x", json.dumps({"i": i}))
        rels = self.gh.releases()
        self.assertEqual(len(rels), 105)
        self.assertEqual(transport.body_of(rels[0]), {"i": 104})
        self.assertEqual(transport.body_of({"body": "not json"}), {})

    def test_save_file_makes_orphan_branch_then_handles_conflicts(self):
        sha = self.gh.save_file("laptop.json", lambda old: '{"a": 1}', "status", "m")
        self.assertTrue(sha)
        commit = next(iter(self.fake.commits.values()))
        self.assertEqual(commit["parents"], [])
        self.assertEqual(self.fake.json_file("laptop.json"), {"a": 1})
        self.fake.conflict_puts = 2
        self.gh.save_file("laptop.json", lambda old: '{"a": 2}', "status", "m")
        self.assertEqual(self.fake.json_file("laptop.json"), {"a": 2})

    def test_dispatch_and_active_runs(self):
        self.assertFalse(self.gh.run_active("make-parts.yml"))
        self.gh.dispatch("make-parts.yml")
        self.assertEqual(self.fake.dispatches, [("make-parts.yml", {"ref": "main"})])
        self.fake.runs = [{"status": "in_progress"}]
        self.assertTrue(self.gh.run_active("make-parts.yml"))


class StatusApiTest(TempDirTest):
    """state.json through the contents API (the laptop): conflicts are redone on top of the other save."""

    def setUp(self):
        super().setUp()
        self.fake = FakeGitHub()
        os.environ.pop("STATUS", None)

    def tearDown(self):
        status.reset()
        self.fake.close()
        super().tearDown()

    def test_creates_branch_and_replays_after_conflict(self):
        status.use_github("o/r", "t", self.fake.api)
        status.movie_started("Film", "laptop", ["stretch"])
        status.part_ready("Film", "stretch", 1, 0, 60, "Ready to post/Film/Best stretch/F - Part 1.mp4", "c")
        status.flush()
        self.assertIn("status", self.fake.branches)
        st = self.fake.json_file("state.json")
        self.assertEqual(st["movies"][0]["modes"]["stretch"]["parts"][0]["post"]["status"], "none")

        def pipeline_saves(text):                 # the workflow adds part 2 at the same moment
            s = json.loads(text)
            s["movies"][0]["modes"]["stretch"]["parts"].append(
                {"n": 2, "drive_path": "x", "post": {"status": "none"}})
            s["run"]["stage"] = "rendering"
            return json.dumps(s)
        self.fake.conflict_puts, self.fake.conflict_writer = 1, pipeline_saves
        status.set_post("film", "stretch", 1, "posted", url="https://t/1")
        st = self.fake.json_file("state.json")
        parts = st["movies"][0]["modes"]["stretch"]["parts"]
        self.assertEqual([p["n"] for p in parts], [1, 2])          # theirs kept
        self.assertEqual(parts[0]["post"]["status"], "posted")      # ours redone
        self.assertEqual(st["run"]["stage"], "rendering")

    def test_refresh_sees_other_writers(self):
        self.fake.put_file("state.json", json.dumps(status.empty_state()), "status")
        status.use_github("o/r", "t", self.fake.api)
        s = status.empty_state()
        s["run"]["stage"] = "analysing"
        self.fake.put_file("state.json", json.dumps(s), "status")
        status.refresh()
        self.assertEqual(status.load_state()["run"]["stage"], "analysing")
