import json

from tests.fake_github import FakeGitHub
from tests.helpers import TempDirTest

import ghapi
import previews


class CryptoTest(TempDirTest):
    def test_round_trip_and_wrong_key(self):
        blob = previews.encrypt(b"video bytes", "my-topic")
        self.assertTrue(blob.startswith(b"MSP1"))
        self.assertNotIn(b"video bytes", blob)
        self.assertEqual(previews.decrypt(blob, "my-topic"), b"video bytes")
        with self.assertRaises(Exception):
            previews.decrypt(blob, "someone-else")

    def test_key_matches_the_app(self):
        # the app does: SHA-256("movie-splitter-preview:" + topic) with WebCrypto
        import hashlib
        self.assertEqual(previews.key_for("abc"), hashlib.sha256(b"movie-splitter-preview:abc").digest())


class BranchTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.fake = FakeGitHub()
        self.gh = ghapi.GitHub("o/r", "t", self.fake.api)

    def tearDown(self):
        self.fake.close()
        super().tearDown()

    def test_keeps_only_newest_movies(self):
        p = previews.Previews(self.gh, "topic", keep=2)
        for mid in ("a", "b", "c"):
            p.start_movie(mid)
            self.assertEqual(p.add(previews.part_path(mid, "stretch", 1), f"clip {mid}".encode()),
                             f"{mid}/stretch-001.bin")
        files = self.fake.bin_branches["previews"]
        self.assertEqual(sorted(files), ["b/stretch-001.bin", "c/stretch-001.bin", "index.json"])
        self.assertEqual(json.loads(self.fake.blobs[files["index.json"]])["movies"], ["c", "b"])
        self.assertEqual(previews.decrypt(self.fake.blobs[files["c/stretch-001.bin"]], "topic"), b"clip c")
        # a new run (fresh object) reads what's there and carries on
        q = previews.Previews(self.gh, "topic", keep=2)
        q.start_movie("d")
        q.add("d/thumbs.bin", b"x")
        self.assertEqual(sorted(self.fake.bin_branches["previews"]),
                         ["c/stretch-001.bin", "d/thumbs.bin", "index.json"])

    def test_off_without_topic(self):
        p = previews.Previews(self.gh, "", keep=2)
        self.assertFalse(p.enabled)
        self.assertIsNone(p.add("x/y.bin", b"z"))
        self.assertNotIn("previews", self.fake.bin_branches)
