import http.server
import json
import os
import threading
import urllib.parse
from unittest import mock

from tests.fake_github import FakeGitHub
from tests.helpers import TempDirTest

import agent
import archive
import ghapi
import posting
import status
import transport

PD = archive.DEFAULT_PD_COLLECTIONS
NOSFERATU = {"identifier": "nosferatu_1922", "title": "Nosferatu: A Symphony of Horror", "year": "1922",
             "collection": ["feature_films", "moviesandfilms"], "downloads": 500000}


class FakeWeb:
    """Stands in for archive.org and ntfy.sh (archive.http_request). Records every request."""

    def __init__(self, search=None, meta=None):
        self.search, self.meta, self.calls, self.ntfy = search or {}, meta or {}, [], []

    def __call__(self, method, url, headers=None, body=None, timeout=60):
        self.calls.append((method, url))
        u = urllib.parse.urlparse(url)
        if u.netloc == "ntfy.sh":
            self.ntfy.append(json.loads(body))
            return 200, b"{}"
        if u.netloc == "archive.org" and u.path == "/advancedsearch.php":
            q = urllib.parse.parse_qs(u.query)["q"][0]
            docs = next((d for k, d in self.search.items() if k in q), [])
            return 200, json.dumps({"response": {"docs": docs}}).encode()
        if u.netloc == "archive.org" and u.path.startswith("/metadata/"):
            m = self.meta.get(u.path.split("/")[-1])
            return (200, json.dumps(m).encode()) if m else (404, b"{}")
        return 500, b"unexpected"


class WishlistMatchingTest(TempDirTest):
    def test_parse(self):
        text = "# comment\n\nNosferatu (1922)\n  The General [1926] \nPlan 9 from Outer Space\n"
        self.assertEqual(archive.parse_wishlist(text), [("Nosferatu (1922)", "Nosferatu", 1922),
                                                        ("The General [1926]", "The General", 1926),
                                                        ("Plan 9 from Outer Space", "Plan 9 from Outer Space", None)])

    def test_title_matching(self):
        self.assertTrue(archive.title_matches("Nosferatu", "Nosferatu: A Symphony of Horror"))
        self.assertTrue(archive.title_matches("the general", "The General (1926)"))
        self.assertFalse(archive.title_matches("Nosferatu", "Nosferatu the Vampyre"))
        self.assertFalse(archive.title_matches("Alien", "Aliens"))

    def test_public_domain_rules(self):
        self.assertTrue(archive.public_domain({"licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"}, PD))
        self.assertFalse(archive.public_domain({"licenseurl": "http://creativecommons.org/licenses/by-nc/3.0/",
                                                "collection": ["feature_films"]}, PD))
        self.assertTrue(archive.public_domain({"collection": ["Feature_Films"]}, PD))
        self.assertFalse(archive.public_domain({"collection": ["opensource_movies"]}, PD))
        self.assertFalse(archive.public_domain({"rights": "Copyright 2020 Studio", "collection": ["feature_films"]}, PD))

    def test_search_picks_public_domain_and_year(self):
        docs = [{"identifier": "fan_upload", "title": "Nosferatu", "year": "1922", "collection": ["opensource_movies"],
                 "downloads": 9e6},
                {"identifier": "remake", "title": "Nosferatu", "year": "1979", "collection": ["feature_films"]},
                NOSFERATU]
        with mock.patch.object(archive, "http_request", FakeWeb(search={"Nosferatu": docs})):
            self.assertEqual(archive.search_archive("Nosferatu", 1922, PD)["identifier"], "nosferatu_1922")
            self.assertEqual(archive.search_archive("Nosferatu", 1979, PD)["identifier"], "remake")
            self.assertIsNone(archive.search_archive("Some New Movie", 2024, PD))

    def test_best_video_prefers_mp4(self):
        meta = {"metadata": {"title": "N"}, "files": [
            {"name": "n.avi", "size": "900000000"}, {"name": "n_512kb.mp4", "size": "300000000"},
            {"name": "n.mp4", "size": "500000000"}, {"name": "huge.mp4", "size": "9000000000"}]}
        with mock.patch.object(archive, "http_request", FakeWeb(meta={"x": meta})):
            url, name, size = archive.best_video_file("x", 4000, PD)
        self.assertEqual((name, size, url), ("n.mp4", 500000000, "https://archive.org/download/x/n.mp4"))


class StabilityTest(TempDirTest):
    def test_waits_until_size_is_stable(self):
        w = agent.Watcher(self.tmp, stable=60)
        f = self.tmp / "Movie.mkv"
        f.write_bytes(b"a" * 10)
        for junk in ("notes.docx", "Other.mp4.crdownload", ".hidden.mp4", "Movie.srt"):
            (self.tmp / junk).write_bytes(b"x")
        self.assertEqual(w.ready(now=1000), [])
        with open(f, "ab") as fh:                     # still copying: grows
            fh.write(b"b" * 10)
        self.assertEqual(w.ready(now=1070), [])       # timer restarts
        self.assertEqual(w.ready(now=1100), [])
        self.assertEqual(w.ready(now=1131), [f])      # .srt is never sent on its own
        self.assertEqual(agent.matching_srt(f).name, "Movie.srt")


class RangeHandler(http.server.BaseHTTPRequestHandler):
    payload = b"0123456789" * 1000

    def do_GET(self):
        start = int(self.headers["Range"].split("=")[1].split("-")[0]) if self.headers.get("Range") else 0
        self.send_response(206 if start else 200)
        body = self.payload[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class AgentTestBase(TempDirTest):
    def setUp(self):
        super().setUp()
        self.fake = FakeGitHub()
        self.watch, self.out = self.tmp / "watch", self.tmp / "out"
        self.watch.mkdir()
        self.cfg = {"watch_folder": str(self.watch), "output_folder": str(self.out),
                    "download_folder": str(self.tmp / "dl"), "github_owner": "o", "github_repo": "r",
                    "github_token": "t", "github_api_url": self.fake.api,
                    "ntfy_topic": "test-topic", "stable_seconds": 0, "chunk_mb": 0.004}
        self.web = FakeWeb()
        self.web_patch = mock.patch.object(archive, "http_request", self.web)
        self.web_patch.start()
        os.environ.pop("STATUS", None)

    def tearDown(self):
        self.web_patch.stop()
        status.reset()
        self.fake.close()
        super().tearDown()

    def make_agent(self, **extra):
        a = agent.Agent(dict(self.cfg, **extra), self.tmp)
        a.remote_settings["cut_on"] = "github"      # these tests cover sending movies to GitHub
        return a

    def send_ready(self, a):
        a.watcher.ready(now=0)
        a.watcher.stable = 0
        a.check_folder()


class WatcherLockTest(AgentTestBase):
    def test_file_held_open_by_another_app_is_sent_after_5_minutes(self):
        f = self.watch / "Seeding.mkv"
        f.write_bytes(b"x" * 10)
        w = agent.Watcher(self.watch, stable=60)
        with mock.patch.object(agent.Watcher, "_not_locked", staticmethod(lambda p: False)):
            self.assertEqual(w.ready(now=0), [])
            self.assertEqual(w.ready(now=120), [])          # stable, but "locked": wait
            self.assertEqual(w.ready(now=310), [f])         # 5 minutes unchanged and readable: go


class SendTest(AgentTestBase):
    def test_sends_movie_and_srt_in_pieces_then_starts_cutter(self):
        movie = self.watch / "My.Film.2015.mkv"
        movie.write_bytes(os.urandom(20_000))
        (self.watch / "My.Film.2015.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        a = self.make_agent()
        self.send_ready(a)
        rels = [r for r in self.fake.releases.values() if transport.kind_of(r) == "incoming"]
        self.assertEqual(len(rels), 1)
        rel = rels[0]
        self.assertTrue(rel["draft"])
        meta = json.loads(rel["body"])
        self.assertEqual((meta["file"], meta["srt"], meta["source"], meta["complete"], meta["size"]),
                         ("My.Film.2015.mkv", "My.Film.2015.srt", "laptop", True, 20_000))
        assets = ghapi.GitHub("o/r", "t", self.fake.api).assets(rel["id"])
        movie_chunks = transport.assets_for(assets, "movie")
        self.assertEqual(len(movie_chunks), 5)                       # 20 kB in 4 kB pieces
        check = self.tmp / "check.mkv"
        transport.receive(ghapi.GitHub("o/r", "t", self.fake.api), movie_chunks, check)
        self.assertEqual(check.read_bytes(), movie.read_bytes())
        self.assertEqual(len(transport.assets_for(assets, "subs")), 1)
        self.assertEqual(self.fake.dispatches, [("make-parts.yml", {"ref": "main"})])
        self.assertTrue(movie.exists())                              # stays until the parts are back
        lj = self.fake.json_file("laptop.json")
        self.assertEqual(lj["uploaded"][0]["file"], "My.Film.2015.mkv")
        self.assertEqual(lj["transfers"], [])
        self.assertIsNone(lj["uploading"])
        self.assertTrue(lj["ready_folder"].endswith("out/Ready to post"))
        titles = [n["title"] for n in self.web.ntfy]
        self.assertIn("📤 Sending to GitHub", titles)
        self.assertIn("📥 Sent to GitHub", titles)
        self.assertTrue(all(n["topic"] == "test-topic" for n in self.web.ntfy))
        self.assertEqual(self.fake.json_file("state.json")["events"][0]["type"], "new_file")
        # not sent twice, and no new run while one is busy
        a.check_folder()
        self.assertEqual(len([r for r in self.fake.releases.values() if transport.kind_of(r) == "incoming"]), 1)

    def test_interrupted_send_resumes(self):
        movie = self.watch / "Big.mkv"
        movie.write_bytes(os.urandom(15_000))
        a = self.make_agent()
        self.fake.fail_upload_names = {"movie.002"}
        self.send_ready(a)                                           # fails part-way (logged, not raised)
        rel = next(r for r in self.fake.releases.values() if transport.kind_of(r) == "incoming")
        self.assertFalse(json.loads(rel["body"])["complete"])
        b = self.make_agent()                                        # e.g. after a restart
        b.watcher.ready(now=0)
        b.watcher.stable = 0
        b.check_folder()
        rels = [r for r in self.fake.releases.values() if transport.kind_of(r) == "incoming"]
        self.assertEqual(len(rels), 1)
        self.assertTrue(json.loads(rels[0]["body"])["complete"])
        out = self.tmp / "o.mkv"
        gh = ghapi.GitHub("o/r", "t", self.fake.api)
        transport.receive(gh, transport.assets_for(gh.assets(rels[0]["id"]), "movie"), out)
        self.assertEqual(out.read_bytes(), movie.read_bytes())

    def test_wishlist_download_then_send(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        self.fake.put_file("config/wishlist.txt", "# hi\nNosferatu (1922)\nSome New Movie (2024)\n")
        self.web.search = {"Nosferatu": [NOSFERATU]}
        self.web.meta = {"nosferatu_1922": {"metadata": {}, "files": [
            {"name": "nos.mp4", "size": str(len(RangeHandler.payload))}]}}
        real = archive.best_video_file
        with mock.patch.object(archive, "best_video_file",
                               lambda *a: (base + "/nos.mp4",) + real(*a)[1:]):
            a = self.make_agent()
            a.check_wishlist()
            a.check_folder()
        rel = next(r for r in self.fake.releases.values() if transport.kind_of(r) == "incoming")
        meta = json.loads(rel["body"])
        self.assertEqual((meta["file"], meta["source"]), ("Nosferatu (1922).mp4", "wishlist"))
        wl = {w["title"]: w for w in self.fake.json_file("laptop.json")["wishlist"]}
        self.assertEqual(wl["Nosferatu (1922)"]["status"], "uploaded")
        self.assertEqual(wl["Some New Movie (2024)"]["status"], "not_available")
        self.assertEqual(wl["Some New Movie (2024)"]["note"], archive.NOT_AVAILABLE)
        hosts = {urllib.parse.urlparse(u).netloc for _, u in self.web.calls}
        self.assertEqual(hosts, {"archive.org", "ntfy.sh"})            # nothing else, ever


class ReceiveTest(AgentTestBase):
    def fake_cutter(self, rid, movie, parts, done=True, failed=False):
        """Does what process.py does on GitHub: a parts-<id> draft release with encrypted parts."""
        gh = ghapi.GitHub("o/r", "t", self.fake.api)
        body = {"kind": "parts", "id": rid, "movie": movie, "file": "x.mkv", "done": done, "failed": failed,
                "parts": [], "lists": {"stretch": f"{movie}\nMode: Best stretch\n\nPart 1: 0:00 to 1:00\n"},
                "summary": "Best stretch: 2 parts"}
        rel = gh.create_release("parts-" + rid, "p", "{}")
        for n, data in parts:
            f = self.tmp / f"{movie} - Part {n:02}.mp4"
            f.write_bytes(data)
            names = transport.send(gh, rel, f, f"stretch-{n:02}", chunk_size=3000)
            body["parts"].append({"mode": "stretch", "label": "Best stretch", "n": n, "of": len(parts),
                                  "file": f.name, "assets": names, "caption": f"cap {n} #fyp"})
            f.unlink()
        gh.update_release(rel["id"], body=json.dumps(body))
        return rel

    def sent_movie(self, a, name="Film.mkv"):
        movie = self.watch / name
        movie.write_bytes(b"movie")
        self.send_ready(a)
        return movie, next(r for r in self.fake.releases.values() if transport.kind_of(r) == "incoming")

    def test_receives_parts_then_moves_original(self):
        a = self.make_agent()
        movie, inc = self.sent_movie(a)
        rid = transport.id_of(inc)
        self.fake.releases.pop(inc["id"])                             # the cutter deletes it when done
        p1, p2 = os.urandom(7000), os.urandom(5000)
        self.fake_cutter(rid, "Film", [(1, p1), (2, p2)])
        a.receive_parts()
        folder = self.out / "Ready to post" / "Film" / "Best stretch"
        self.assertEqual((folder / "Film - Part 01.mp4").read_bytes(), p1)
        self.assertEqual((folder / "Film - Part 02.mp4").read_bytes(), p2)
        self.assertIn("cap 2 #fyp", (folder / "captions.txt").read_text())
        self.assertIn("Part 1: 0:00", (folder / "_parts list.txt").read_text())
        self.assertFalse(self.fake.releases)                          # everything deleted on GitHub
        self.assertFalse(movie.exists())
        self.assertTrue((self.out / "Done" / "Film.mkv").exists())
        self.assertIn("Film", (self.out / "Finished.txt").read_text())
        self.assertIn("📥 Parts downloaded", [n["title"] for n in self.web.ntfy])
        self.assertEqual(a.memory["sends"], {})

    def test_parts_arrive_while_still_cutting(self):
        a = self.make_agent(original_after="keep")
        movie, inc = self.sent_movie(a)
        rid = transport.id_of(inc)
        rel = self.fake_cutter(rid, "Film", [(1, b"one")], done=False)
        a.receive_parts()
        self.assertTrue((self.out / "Ready to post/Film/Best stretch/Film - Part 01.mp4").exists())
        self.assertIn(rel["id"], self.fake.releases)                  # kept: more parts to come
        self.assertTrue(movie.exists())
        self.fake.releases.pop(inc["id"])
        body = json.loads(self.fake.releases[rel["id"]]["body"])
        body["done"] = True
        self.fake.releases[rel["id"]]["body"] = json.dumps(body)
        a.receive_parts()
        self.assertNotIn(rel["id"], self.fake.releases)
        self.assertTrue(movie.exists())                               # original_after = keep
        a.check_folder()                                              # ...and it isn't sent again
        self.assertFalse([r for r in self.fake.releases.values() if transport.kind_of(r) == "incoming"])

    def test_failed_movie_goes_to_failed_folder(self):
        a = self.make_agent()
        movie, inc = self.sent_movie(a)
        meta = json.loads(inc["body"])
        meta.update(failed=True, error="ffmpeg broke")
        inc["body"] = json.dumps(meta)
        a.receive_parts()
        self.assertFalse(self.fake.releases)
        self.assertTrue((self.out / "Failed" / "Film.mkv").exists())
        self.assertIn("❌ Movie failed", [n["title"] for n in self.web.ntfy])
        self.assertEqual(a.memory["sends"], {})                       # moving it back tries again

    def test_cleanup_old_parts(self):
        a = self.make_agent()
        old = self.out / "Ready to post" / "Old" / "Highlights" / "p.mp4"
        new = self.out / "Ready to post" / "New" / "Highlights" / "p.mp4"
        for f in (old, new):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
        os.utime(old, (1, 1))
        a.remote_settings["cleanup_parts_after_days"] = 3
        a.cleanup_old_parts()
        self.assertFalse(old.parent.parent.exists())
        self.assertTrue(new.exists())


class PostingTest(AgentTestBase):
    def setUp(self):
        super().setUp()
        st = status.empty_state()
        parts = [{"n": n, "drive_path": f"Ready to post/M/Best stretch/M - Part {n:02}.mp4", "caption": f"c{n}",
                  "post": {"status": "none", "at": None, "url": None, "error": None}} for n in (1, 2)]
        st["movies"] = [{"id": "m", "name": "M", "started": "2026-10-10T10:00:00+13:00", "status": "done",
                         "modes": {"stretch": {"label": "Best stretch", "planned": 2, "parts": parts}}}]
        self.fake.put_file("state.json", json.dumps(st), "status")
        self.fake.put_file("config/settings.json", json.dumps({"posting": {"enabled": True, "hashtags": "#movie"}}))
        f = self.out / parts[0]["drive_path"]
        f.parent.mkdir(parents=True)
        f.write_bytes(b"part one")

    def test_dry_run_changes_nothing(self):
        a = self.make_agent()
        before = self.fake.file("state.json")
        with mock.patch.object(posting, "post_video", side_effect=AssertionError("must not post")):
            pick = a.post_if_due(force=True, dry_run=True)
        self.assertEqual(pick[2]["n"], 1)
        self.assertEqual(self.fake.file("state.json"), before)

    def test_draft_mode_sends_to_tiktok_inbox(self):
        a = self.make_agent(composio_api_key="k", composio_user_id="u")
        calls = []

        def fake_post(path, caption, privacy, creds, publish=True):
            calls.append((path.read_bytes(), publish))
            return True, "v_inbox_file~1", None
        with mock.patch.object(posting, "post_video", fake_post):
            a.post_if_due(force=True, dry_run=False)
        self.assertEqual(calls, [(b"part one", False)])
        st = self.fake.json_file("state.json")
        self.assertEqual(st["movies"][0]["modes"]["stretch"]["parts"][0]["post"]["status"], "inbox")
        self.assertEqual(st["posting"]["posted_today"], 1)
        self.assertIn("📲 In your TikTok drafts: M: Part 1", [n["title"] for n in self.web.ntfy])
        (self.out / "Ready to post/M/Best stretch/M - Part 02.mp4").write_bytes(b"part two")
        with mock.patch.object(posting, "post_video", fake_post):     # next time: Part 2, not Part 1 again
            a.post_if_due(force=True, dry_run=False)
        self.assertEqual(calls[-1], (b"part two", False))

    def test_posts_local_file_and_records_result(self):
        self.fake.put_file("config/settings.json", json.dumps({"posting": {"enabled": True, "hashtags": "#movie",
                                                                           "method": "direct"}}))
        a = self.make_agent(composio_api_key="k", composio_user_id="u", tiktok_username="me")
        calls = []

        def fake_post(path, caption, privacy, creds, publish=True):
            calls.append((path.read_bytes(), caption, privacy, creds["api_key"], creds["user_id"]))
            return True, "https://www.tiktok.com/@me/video/9", None
        # the cutter saves state.json at the same moment: our post result must be redone on top
        self.fake.conflict_puts = 1
        self.fake.conflict_writer = lambda text: json.dumps(dict(json.loads(text), queue=[{"name": "next.mkv"}]))
        with mock.patch.object(posting, "post_video", fake_post):
            a.post_if_due(force=True, dry_run=False)
        self.assertEqual(calls, [(b"part one", "c1\n#movie", "SELF_ONLY", "k", "u")])
        st = self.fake.json_file("state.json")
        post = st["movies"][0]["modes"]["stretch"]["parts"][0]["post"]
        self.assertEqual((post["status"], post["url"], post["attempts"]), ("posted", "https://www.tiktok.com/@me/video/9", 1))
        self.assertEqual(st["queue"], [{"name": "next.mkv"}])         # the other save survived
        self.assertEqual(st["events"][0]["type"], "posted")
        self.assertEqual(st["posting"]["posted_today"], 1)
        self.assertIn("📤 Posted to TikTok: M: Part 1", [n["title"] for n in self.web.ntfy])

    def test_waits_for_part_not_downloaded_yet(self):
        (self.out / "Ready to post/M/Best stretch/M - Part 01.mp4").unlink()
        a = self.make_agent()
        self.assertIsNone(a.post_if_due(force=True, dry_run=True))


class SetupTest(AgentTestBase):
    def test_setup_makes_ntfy_name_and_shows_next_steps(self):
        cfg = dict(self.cfg, ntfy_topic="", _help="note")
        path = self.tmp / "config.json"
        path.write_text(json.dumps(cfg))
        text = agent.setup(path)
        saved = json.loads(path.read_text())
        self.assertTrue(saved["ntfy_topic"].startswith("movies-"))
        self.assertEqual(saved["_help"], "note")                      # notes kept
        self.assertIn(f"Name:   NTFY_TOPIC\n       Secret: {saved['ntfy_topic']}", text)
        self.assertIn("OK  GitHub token works", text)
        self.assertNotIn("MOVIE_KEY", text)
        self.assertEqual((self.tmp / "github-secrets.txt").read_text(), text)
        agent.setup(path)                                             # second time: same name
        self.assertEqual(json.loads(path.read_text())["ntfy_topic"], saved["ntfy_topic"])

    def test_agent_imports_only_the_standard_library(self):
        import subprocess
        import sys
        code = ("import sys; sys.modules['cryptography'] = None; sys.modules['composio'] = None; "
                "sys.argv = ['x']; import runpy; runpy.run_path('agent.py', run_name='not_main'); print('ok')")
        out = subprocess.run([sys.executable, "-c", code], cwd=agent.HERE, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "ok", out.stderr)


class AsReadyTest(PostingTest):
    def test_posts_without_waiting_for_a_time_but_paced(self):
        self.fake.put_file("config/settings.json", json.dumps({"posting": {"enabled": True, "when": "as_ready",
                                                                           "times": [], "per_day": 1}}))
        (self.out / "Ready to post/M/Best stretch/M - Part 02.mp4").write_bytes(b"part two")
        a = self.make_agent(composio_api_key="k", composio_user_id="u")
        sent = []
        with mock.patch.object(posting, "post_video",
                               lambda path, cap, priv, creds, publish=True: sent.append(path.name) or (True, "v~1", None)):
            a.post_if_due()                       # no post time set: goes anyway
            a.post_if_due()                       # straight after: waits (one every 5 minutes)
            a.memory["last_post"] -= 301
            a.post_if_due()                       # 5 minutes later: the next part (per_day doesn't apply)
        self.assertEqual(sent, ["M - Part 01.mp4", "M - Part 02.mp4"])

    def test_pause_holds_manual_send(self):
        self.fake.put_file("config/settings.json", json.dumps({"paused": True}))
        self.fake.put_file("config/commands.json", json.dumps({"commands": [
            {"id": "x1", "type": "post_part", "movie_id": "m", "mode": "stretch", "n": 1}]}))
        a = self.make_agent(composio_api_key="k", composio_user_id="u")
        a.refresh_settings(force=True)
        with mock.patch.object(posting, "post_video", side_effect=AssertionError("must wait while paused")):
            a.check_commands()
        self.assertEqual(a.cmd_status["x1"]["status"], "waiting")
        self.fake.put_file("config/settings.json", json.dumps({"paused": False}))
        a.refresh_settings(force=True)
        with mock.patch.object(posting, "post_video", lambda *x, **k: (True, "v~1", None)):
            a.check_commands()
        self.assertEqual(a.cmd_status["x1"]["status"], "done")
