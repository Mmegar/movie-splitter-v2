import base64
import http.server
import json
import os
import threading
import urllib.parse
from pathlib import Path
from unittest import mock

from tests.helpers import FAKE_RCLONE_DIR, TempDirTest

import agent

PD = agent.DEFAULT_PD_COLLECTIONS
NOSFERATU = {"identifier": "nosferatu_1922", "title": "Nosferatu: A Symphony of Horror", "year": "1922",
             "collection": ["feature_films", "moviesandfilms"], "downloads": 500000}


class FakeHTTP:
    """Stands in for GitHub and archive.org. Records every request."""

    def __init__(self, wishlist="", search=None, meta=None):
        self.wishlist, self.search, self.meta = wishlist, search or {}, meta or {}
        self.files, self.calls, self.branch = {}, [], False
        self.put_conflicts = 0

    def __call__(self, method, url, headers=None, body=None, timeout=60):
        self.calls.append((method, url))
        u = urllib.parse.urlparse(url)
        if u.netloc == "archive.org" and u.path == "/advancedsearch.php":
            q = urllib.parse.parse_qs(u.query)["q"][0]
            docs = next((d for k, d in self.search.items() if k in q), [])
            return 200, json.dumps({"response": {"docs": docs}}).encode()
        if u.netloc == "archive.org" and u.path.startswith("/metadata/"):
            m = self.meta.get(u.path.split("/")[-1])
            return (200, json.dumps(m).encode()) if m else (404, b"{}")
        path = u.path.split("/repos/o/r", 1)[-1]
        if path == "/contents/config/wishlist.txt":
            return 200, self.wishlist.encode()
        if path == "/branches/status":
            return (200, b"{}") if self.branch else (404, b"{}")
        if path == "/contents/laptop.json" and method == "GET":
            if "laptop.json" in self.files:
                return 200, json.dumps({"sha": "sha1"}).encode()
            return 404, b"{}"
        if path == "/contents/laptop.json" and method == "PUT":
            if not self.branch:
                return 404, b'{"message":"Branch status not found"}'
            if self.put_conflicts:
                self.put_conflicts -= 1
                return 409, b'{"message":"sha mismatch"}'
            data = json.loads(body)
            self.files["laptop.json"] = json.loads(base64.b64decode(data["content"]))
            return 200, b"{}"
        if path == "/git/trees":
            self.tree = json.loads(body)
            return 201, b'{"sha":"tree1"}'
        if path == "/git/commits":
            self.commit = json.loads(body)
            return 201, b'{"sha":"c1"}'
        if path == "/git/refs":
            self.branch = True
            self.files["laptop.json"] = json.loads(self.tree["tree"][0]["content"])
            return 201, b"{}"
        return 500, b"unexpected"


class WishlistMatchingTest(TempDirTest):
    def test_parse(self):
        text = "# comment\n\nNosferatu (1922)\n  The General [1926] \nPlan 9 from Outer Space\n"
        self.assertEqual(agent.parse_wishlist(text), [("Nosferatu (1922)", "Nosferatu", 1922),
                                                      ("The General [1926]", "The General", 1926),
                                                      ("Plan 9 from Outer Space", "Plan 9 from Outer Space", None)])

    def test_title_matching(self):
        self.assertTrue(agent.title_matches("Nosferatu", "Nosferatu: A Symphony of Horror"))
        self.assertTrue(agent.title_matches("the general", "The General (1926)"))
        self.assertTrue(agent.title_matches("Night of the Living Dead", ["Night Of The Living Dead"]))
        self.assertFalse(agent.title_matches("Nosferatu", "Nosferatu the Vampyre"))
        self.assertFalse(agent.title_matches("Alien", "Aliens"))

    def test_public_domain_rules(self):
        self.assertTrue(agent.public_domain({"licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"}, PD))
        self.assertFalse(agent.public_domain({"licenseurl": "http://creativecommons.org/licenses/by-nc/3.0/",
                                              "collection": ["feature_films"]}, PD))
        self.assertTrue(agent.public_domain({"collection": ["Feature_Films"]}, PD))
        self.assertFalse(agent.public_domain({"collection": ["opensource_movies"]}, PD))
        self.assertFalse(agent.public_domain({"rights": "Copyright 2020 Studio", "collection": ["feature_films"]}, PD))
        self.assertTrue(agent.public_domain({"rights": "This film is in the Public Domain"}, PD))

    def test_search_picks_public_domain_and_year(self):
        docs = [{"identifier": "fan_upload", "title": "Nosferatu", "year": "1922", "collection": ["opensource_movies"],
                 "downloads": 9e6},
                {"identifier": "remake", "title": "Nosferatu", "year": "1979", "collection": ["feature_films"]},
                NOSFERATU]
        with mock.patch.object(agent, "http_request", FakeHTTP(search={"Nosferatu": docs})):
            self.assertEqual(agent.search_archive("Nosferatu", 1922, PD)["identifier"], "nosferatu_1922")
            self.assertEqual(agent.search_archive("Nosferatu", 1979, PD)["identifier"], "remake")
            self.assertIsNone(agent.search_archive("Some New Movie", 2024, PD))

    def test_best_video_prefers_mp4(self):
        meta = {"metadata": {"title": "N"}, "files": [
            {"name": "n.avi", "size": "900000000"}, {"name": "n.ogv", "size": "100"},
            {"name": "n_512kb.mp4", "size": "300000000", "format": "h.264"}, {"name": "n.mp4", "size": "500000000"},
            {"name": "huge.mp4", "size": "9000000000"}, {"name": "n.jpg", "size": "1"}]}
        with mock.patch.object(agent, "http_request", FakeHTTP(meta={"x": meta})):
            url, name, size = agent.best_video_file("x", 4000, PD)
        self.assertEqual((name, size), ("n.mp4", 500000000))
        self.assertEqual(url, "https://archive.org/download/x/n.mp4")
        bad = {"metadata": {"licenseurl": "http://creativecommons.org/licenses/by/4.0/"}, "files": meta["files"]}
        with mock.patch.object(agent, "http_request", FakeHTTP(meta={"x": bad})):
            with self.assertRaises(LookupError):
                agent.best_video_file("x", 4000, PD)


class StabilityTest(TempDirTest):
    def test_waits_until_size_is_stable(self):
        w = agent.Watcher(self.tmp, stable=60)
        f = self.tmp / "Movie.mkv"
        f.write_bytes(b"a" * 10)
        (self.tmp / "notes.docx").write_bytes(b"x")
        (self.tmp / "Other.mp4.crdownload").write_bytes(b"x")
        (self.tmp / ".hidden.mp4").write_bytes(b"x")
        self.assertEqual(w.ready(now=1000), [])
        self.assertEqual(w.ready(now=1030), [])
        with open(f, "ab") as fh:                     # still copying: grows
            fh.write(b"b" * 10)
        self.assertEqual(w.ready(now=1070), [])       # timer restarts
        self.assertEqual(w.ready(now=1100), [])
        self.assertEqual(w.ready(now=1131), [f])
        w.forget(f)
        self.assertEqual(w.ready(now=1200), [])       # seen again from scratch

    def test_empty_file_never_ready_and_srt_after_movie(self):
        w = agent.Watcher(self.tmp, stable=1)
        (self.tmp / "empty.mp4").write_bytes(b"")
        (self.tmp / "A.srt").write_text("1")
        (self.tmp / "A.mp4").write_bytes(b"v")
        w.ready(now=0)
        self.assertEqual([p.name for p in w.ready(now=5)], ["A.mp4", "A.srt"])


class GitHubWriteTest(TempDirTest):
    def test_makes_orphan_branch_then_handles_conflicts(self):
        fake = FakeHTTP()
        gh = agent.GitHub("o", "r", "tok")
        with mock.patch.object(agent, "http_request", fake), mock.patch.object(agent.time, "sleep"):
            self.assertTrue(gh.write_text("laptop.json", '{"a": 1}', "status", "m"))
            self.assertEqual(fake.commit["parents"], [])
            self.assertEqual(fake.files["laptop.json"], {"a": 1})
            fake.put_conflicts = 2
            self.assertTrue(gh.write_text("laptop.json", '{"a": 2}', "status", "m"))
            self.assertEqual(fake.files["laptop.json"], {"a": 2})


class RangeHandler(http.server.BaseHTTPRequestHandler):
    payload = b"0123456789" * 1000

    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        body = self.payload[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class AgentRunTest(TempDirTest):
    """A whole --once run: watch folder upload + wish list download, with fake rclone and fake GitHub."""

    def setUp(self):
        super().setUp()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        os.environ.update(self.fake_rclone_env(self.tmp / "drive"))
        os.environ.pop("http_proxy", None)
        os.environ.pop("HTTP_PROXY", None)
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        self.watch = self.tmp / "watch"
        self.cfg = {"watch_folder": str(self.watch), "download_folder": str(self.tmp / "dl"),
                    "rclone_path": str(FAKE_RCLONE_DIR / "rclone"), "rclone_remote": "gdrive",
                    "drive_folder": "TikTok automation v2", "github_owner": "o", "github_repo": "r",
                    "github_token": "t", "stable_seconds": 0.3}
        (self.tmp / "config.json").write_text(json.dumps(dict(self.cfg, _help="note")))

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def test_download_resumes(self):
        dest = self.tmp / "film.mp4"
        dest.with_name("film.mp4.part").write_bytes(RangeHandler.payload[:4000])
        seen = []
        agent.download(self.base + "/f", dest, len(RangeHandler.payload), seen.append)
        self.assertEqual(dest.read_bytes(), RangeHandler.payload)
        self.assertEqual(seen[-1], 100)
        self.assertFalse(dest.with_name("film.mp4.part").exists())

    def test_once_run(self):
        self.watch.mkdir()
        (self.watch / "MyMovie.2015.mkv").write_bytes(b"m" * 2000)
        meta = {"metadata": {"title": "Nosferatu"}, "files": [{"name": "nos.mp4", "size": str(len(RangeHandler.payload))}]}
        fake = FakeHTTP(wishlist="# hi\nNosferatu (1922)\nSome New Movie (2024)\n",
                        search={"Nosferatu": [NOSFERATU]}, meta={"nosferatu_1922": meta})
        real_best = agent.best_video_file

        def local_best(ident, max_mb, pd):          # serve the archive.org file from the local test server
            url, name, size = real_best(ident, max_mb, pd)
            return self.base + "/" + name, name, size
        with mock.patch.object(agent, "http_request", fake), mock.patch.object(agent, "best_video_file", local_best):
            self.assertEqual(agent.main(["--config", str(self.tmp / "config.json"), "--once"]), 0)

        to_edit = self.tmp / "drive" / "TikTok automation v2" / "To edit"
        self.assertEqual(sorted(p.name for p in to_edit.iterdir()), ["MyMovie.2015.mkv", "Nosferatu (1922).mp4"])
        self.assertEqual((to_edit / "Nosferatu (1922).mp4").read_bytes(), RangeHandler.payload)
        self.assertEqual(list(self.watch.iterdir()), [])          # moved, not copied
        lj = fake.files["laptop.json"]
        self.assertEqual(lj["version"], 1)
        self.assertFalse(lj["online"])                            # --once ends with a "going offline" save
        self.assertIsNone(lj["uploading"])
        self.assertEqual({u["file"]: u["source"] for u in lj["uploaded"]},
                         {"MyMovie.2015.mkv": "laptop", "Nosferatu (1922).mp4": "wishlist"})
        wl = {w["title"]: w for w in lj["wishlist"]}
        self.assertEqual(wl["Nosferatu (1922)"]["status"], "uploaded")
        self.assertEqual(wl["Nosferatu (1922)"]["source_url"], "https://archive.org/details/nosferatu_1922")
        self.assertEqual(wl["Some New Movie (2024)"]["status"], "not_available")
        self.assertEqual(wl["Some New Movie (2024)"]["note"], agent.NOT_AVAILABLE)
        self.assertTrue((self.tmp / "agent.log").read_text())
        # nothing but archive.org and the GitHub API was contacted
        hosts = {urllib.parse.urlparse(u).netloc for _, u in fake.calls}
        self.assertEqual(hosts, {"archive.org", "api.github.com"})

        # a second run doesn't download or search the uploaded film again
        fake.calls.clear()
        with mock.patch.object(agent, "http_request", fake):
            agent.main(["--config", str(self.tmp / "config.json"), "--once"])
        self.assertFalse(any("advancedsearch" in u for _, u in fake.calls))
