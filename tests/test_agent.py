import http.server
import json
import unittest
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from unittest import mock

from tests.fake_github import FakeGitHub
from tests.helpers import TempDirTest

import agent
import archive
import ghapi
import posting
import status
import transport


_OLD_POSTING = {}


def setUpModule():      # these tests cover the Composio poster at set times (the browser poster has its own)
    _s = agent.settings
    _OLD_POSTING.update(_s.DEFAULTS["posting"])
    _s.DEFAULTS["posting"].update(method="draft", when="times", per_day=3)
    import os as _os                     # (importing process.py copies config/settings.json into POSTING_* env vars)
    for k in [k for k in _os.environ if k.startswith("POSTING_")]:
        _OLD_POSTING.setdefault("_env", {})[k] = _os.environ.pop(k)


def tearDownModule():
    _s = agent.settings
    import os as _os
    _os.environ.update(_OLD_POSTING.pop("_env", {}))
    _s.DEFAULTS["posting"].clear()
    _s.DEFAULTS["posting"].update(_OLD_POSTING)


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
        no_claude = mock.patch.object(agent.Agent, "ask_ai", side_effect=RuntimeError("no Claude in tests"))
        no_claude.start()                     # (the To edit ordering would otherwise call the real one)
        self.addCleanup(no_claude.stop)
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


class StuckCutTest(AgentTestBase):
    def test_interrupted_laptop_cut_goes_to_done_or_is_cut_again(self):
        a = self.make_agent()
        made, lost = self.watch / "Made It (2020).mp4", self.watch / "Lost It (2021).mp4"
        for f in (made, lost):
            f.write_bytes(b"movie")
        parts = a.ready_dir / "Made It (2020)" / "Movie recap"
        parts.mkdir(parents=True)
        (parts / "Made It (2020) - Part 1.mp4").write_bytes(b"part")
        a.memory["sends"] = {
            "r1": {"path": str(made), "file": made.name, "source": "laptop", "status": "cutting"},
            "r2": {"path": str(lost), "file": lost.name, "source": "laptop", "status": "sent", "local": True},
            "r3": {"path": str(self.watch / "Github.mp4"), "file": "Github.mp4", "source": "laptop", "status": "sent"}}
        a.recover_stuck_cuts()
        self.assertFalse(made.exists())
        self.assertTrue((a.output / "Done" / made.name).exists())
        self.assertTrue(lost.exists())                       # nothing was made: it gets cut again
        self.assertNotIn("r2", a.memory["sends"])
        self.assertIn("r3", a.memory["sends"])               # waiting for GitHub: left alone

    def test_start_button_on_the_laptop(self):
        a = self.make_agent()
        self.assertEqual(a.run_command("cut_now", {}), "Nothing in To edit to cut")
        a.cutting = str(self.watch / "Busy.mp4")
        self.assertEqual(a.run_command("cut_now", {}), "Already cutting Busy.mp4")


    def test_stuck_cut_with_its_file_gone_is_cleared(self):
        a = self.make_agent()
        gone = self.watch / "Gone Girl (2014).mp4"
        a.memory["sends"] = {"r1": {"path": str(gone), "file": gone.name, "source": "laptop", "status": "cutting"}}
        a.state_ready()
        status.run_started(None)
        status.movie_started("Gone Girl (2014)", "laptop", ["recap"])
        status.flush()
        a.recover_stuck_cuts()
        self.assertEqual(a.memory["sends"], {})
        st = status.load_state()
        self.assertFalse(st["run"]["active"])
        self.assertEqual(st["movies"][0]["status"], "failed")
        self.assertIn("⚠️ Movie file gone", [n["title"] for n in self.web.ntfy])

    def test_stuck_cut_with_its_file_still_there_waits_in_the_queue(self):
        a = self.make_agent()
        (self.watch / "Here (2020).mp4").write_bytes(b"movie")
        a.state_ready()
        status.run_started(None)
        status.movie_started("Here (2020)", "laptop", ["recap"])
        status.flush()
        self.assertTrue(a.clear_stuck_run())
        st = status.load_state()
        self.assertFalse(st["run"]["active"])
        self.assertEqual(st["movies"][0]["status"], "queued")
        self.assertFalse(a.clear_stuck_run())                # nothing left to clear

    def test_stuck_cut_with_parts_made_shows_as_done(self):
        a = self.make_agent()
        parts = a.ready_dir / "Half (2020)" / "Movie recap"
        parts.mkdir(parents=True)
        (parts / "Half (2020) - Part 1.mp4").write_bytes(b"part")
        a.state_ready()
        status.run_started(None)
        status.movie_started("Half (2020)", "laptop", ["recap"])
        status.flush()
        self.assertTrue(a.clear_stuck_run())
        self.assertEqual(status.load_state()["movies"][0]["status"], "done")

    def test_github_runs_are_left_alone(self):
        a = self.make_agent()
        a.state_ready()
        status.run_started(12345)
        status.movie_started("Remote (2020)", "laptop", ["recap"])
        status.flush()
        self.assertFalse(a.clear_stuck_run())
        self.assertTrue(status.load_state()["run"]["active"])

    def test_stop_button_on_the_laptop(self):
        a = self.make_agent()
        self.assertEqual(a.run_command("stop_cut", {}), "Nothing is cutting on the laptop")
        movie = self.watch / "Long (2020).mp4"
        movie.write_bytes(b"movie")
        a.cutting = str(movie)
        a.cut_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        self.assertEqual(a.run_command("stop_cut", {}), "Stopping Long (2020).mp4")
        self.assertNotEqual(a.cut_proc.wait(timeout=10), 0)
        self.assertTrue(a.stop_asked)

    def test_stopped_movie_waits_for_start(self):
        a = self.make_agent()
        a.remote_settings["cut_on"] = "laptop"
        movie = self.watch / "Paused (2020).mp4"
        movie.write_bytes(b"movie")
        a.memory["stopped"] = [movie.name]
        started = []
        with mock.patch.object(agent, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(a, "_cut_in_background", side_effect=started.append):
            a.watcher.ready(now=0)
            a.check_folder()
            self.assertEqual(a.cutting, None)
            a.run_command("cut_now", {})
            self.assertEqual(a.memory["stopped"], [])
            a.check_folder()
            self.assertEqual(a.cutting, str(movie))


class SubtitleTest(TempDirTest):
    def test_downloads_english_subtitles_named_like_the_movie(self):
        movie = self.tmp / "Knives.Out.2019.1080p.mkv"
        movie.write_bytes(b"x" * 200000)
        calls = []

        def http(method, url, headers=None, body=None, timeout=60):
            calls.append((method, url))
            if "/subtitles?" in url and "moviehash" in url:
                return 200, json.dumps({"data": []}).encode()
            if "/subtitles?" in url:
                self.assertIn("query=Knives+Out", url)
                self.assertIn("year=2019", url)
                return 200, json.dumps({"data": [
                    {"attributes": {"language": "en", "download_count": 5, "hearing_impaired": True,
                                    "files": [{"file_id": 1}]}},
                    {"attributes": {"language": "en", "download_count": 3, "files": [{"file_id": 2}]}},
                    {"attributes": {"language": "fr", "download_count": 99, "files": [{"file_id": 3}]}}]}).encode()
            if url.endswith("/download"):
                self.assertEqual(json.loads(body), {"file_id": 2})
                return 200, json.dumps({"link": "https://dl.example/sub.srt"}).encode()
            return 200, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"

        out = agent.download_subtitles(movie, {"opensubtitles_api_key": "k"}, http)
        self.assertEqual(out, self.tmp / "Knives.Out.2019.1080p.srt")
        self.assertEqual(agent.matching_srt(movie), out)
        self.assertIsNone(agent.download_subtitles(movie, {}, http))     # no key: nothing to do


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


class InsightsTest(unittest.TestCase):
    def test_matches_videos_to_parts_and_adds_up_movies(self):
        state = {"movies": [{"name": "Gone Girl", "modes": {"recap": {"parts": [
            {"n": 1, "caption": "She vanished on their anniversary... #gonegirl #movie", "post": {}},
            {"n": 2, "caption": "The diary was a lie 😳 #gonegirl", "post": {"url": "https://www.tiktok.com/@me/video/22"}}]}}}]}
        videos = [{"id": "11", "desc": "She vanished on their anniversary... #gonegirl #movie #fyp", "views": 1000,
                   "avg_watch": 20.0, "full_watch": 10.0, "created": 2},
                  {"id": "22", "desc": "something else", "views": 3000, "avg_watch": 30.0, "created": 3},
                  {"id": "33", "desc": "unrelated", "views": 5, "created": 1}]
        data = agent.build_insights(state, videos)
        self.assertEqual([v.get("part") for v in data["videos"]], [1, 2, None])
        m = data["movies"]["Gone Girl"]
        self.assertEqual((m["views"], m["videos"], m["avg_watch"], m["best_part"]), (4000, 2, 25.0, [2, 3000]))
        self.assertEqual(data["totals"]["views"], 4005)

    def test_picks_keep_answers_and_tick_off_downloaded(self):
        old = [{"title": "Zodiac", "status": "skipped"}, {"title": "Prisoners", "status": "new"},
               {"title": "Gone Girl", "status": "new"}]
        out = agent.merge_picks(old, [{"title": "Zodiac (2007)", "why": "x"}, {"title": "The Others", "year": 2001,
                                       "genres": ["Horror"], "why": "Halloween", "trend": "Spooky season"},
                                      {"title": "Prisoners", "why": "still good"}, "junk", {"title": ""}],
                                ["Gone Girl (2014)", "Se7en (1995)"], now="t")
        by = {p["title"]: p for p in out}
        self.assertEqual(by["Zodiac"]["status"], "skipped")          # turned down stays turned down
        self.assertEqual(by["Gone Girl"]["status"], "have")          # it's on the laptop now
        self.assertEqual(by["Prisoners"]["why"], "still good")
        self.assertEqual(out[0]["title"], "The Others")              # newest pick first
        self.assertEqual([p["status"] for p in out[:2]], ["new", "new"])
        self.assertTrue(agent.has_movie("Se7en", ["Se7en (1995)"]))
        self.assertFalse(agent.has_movie("It", ["It Follows (2014)"]))

    def test_insights_prompt_has_trends_and_past_picks(self):
        data = {"totals": {}, "movies": {}, "videos": [], "library": ["Gone Girl (2014)"],
                "trends": {"date": "2026-10-28", "season": [{"moment": "Halloween", "genres": ["Horror"]}], "trending": []},
                "picks": [{"title": "Zodiac", "status": "skipped"}]}
        p = agent.insights_prompt(data, {})
        self.assertIn("Halloween", p)
        self.assertIn("Zodiac", p)
        self.assertIn("Gone Girl (2014)", p)

    def test_tuning_stays_in_safe_limits(self):
        cur = {"narrator_funny": 0.5, "recap_part_seconds": 180, "narrator_tempo": "1.2", "thumbnail_badge": "PART {n}"}
        out = agent.clean_tuning({"narrator_funny": 1.0, "recap_part_seconds": 60, "narrator_tempo": "1.3x",
                                  "thumbnail_badge": "no number", "story_model": "x", "script_direction": "Roast more."}, cur)
        self.assertEqual(out, {"narrator_funny": 0.65, "recap_part_seconds": 150, "narrator_tempo": "1.3",
                               "script_direction": "Roast more."})


class DayHistoryTest(unittest.TestCase):
    def test_one_row_per_day_kept_60_days(self):
        h = agent.day_history([{"day": "2026-10-27", "views": 5}], {"views": 9, "videos": 2}, today="2026-10-28")
        h = agent.day_history(h, {"views": 12, "videos": 3}, today="2026-10-28")       # same day: replaced
        self.assertEqual([(r["day"], r["views"]) for r in h], [("2026-10-27", 5), ("2026-10-28", 12)])
        many = [{"day": f"d{i}"} for i in range(80)]
        self.assertEqual(len(agent.day_history(many, {}, today="x")), 60)


class InsightsActTest(AgentTestBase):
    """Insights acting on its advice: modes (protected ones kept), holds, experiments, cut settings."""

    def test_acts_but_keeps_viral_mode_and_holds_a_flop(self):
        a = self.make_agent()
        a.remote_settings["modes"] = ["recap", "stretch"]
        now = time.time() - 3 * 86400
        videos = [{"id": f"r{i}", "movie": "Flop", "mode": "recap", "part": i, "views": 50, "created": now}
                  for i in range(3)] + [{"id": "s1", "movie": "Hit", "mode": "stretch", "part": 1,
                                         "views": 300000, "created": now}]
        data = {"videos": videos, "movies": {"Flop": {"videos": 3, "modes": {"recap": 150}},
                                             "Hit": {"videos": 1, "modes": {"stretch": 300000}}}}
        act = a.act_context(data)
        self.assertIn("stretch", act["protected"])
        self.assertIn("PROTECTED", agent.insights_prompt({"totals": {}, "movies": {}, "videos": []}, {}, act))
        ai = {"modes": ["recap"], "hold": [{"movie": "Flop", "mode": "recap", "why": "50 views"},
                                           {"movie": "Hit", "mode": "stretch", "why": "no"}],
              "next_experiment": {"factor": "narrator_tempo", "arms": ["1.3"], "why": "faster?"}}
        with mock.patch.object(a, "notify"):
            changes, notes = a.act_on_insights(ai, data, act, {"narrator_funny": 0.5})
        self.assertNotIn("modes", changes)                         # stretch can't be dropped
        self.assertEqual(changes, {"narrator_funny": 0.5})
        self.assertEqual([(h["movie"], h["mode"]) for h in a.memory["holds"]], [("Flop", "recap")])
        self.assertEqual(a.memory["experiment"]["current"]["factor"], "narrator_tempo")
        # while the tempo test runs, auto-tune can't touch the tempo
        with mock.patch.object(a, "notify"):
            changes, _ = a.act_on_insights({}, data, act, {"narrator_tempo": "1.5"})
        self.assertEqual(changes, {})
        # each new movie is cut with the next arm
        tempos = [a.cut_settings(Path(f"M{i}.mkv"))["narrator_tempo"] for i in range(3)]
        self.assertEqual(tempos, ["1.2", "1.3", "1.2"])
        self.assertEqual(a.run_command("release_hold", {"movie": "Flop", "mode": "recap"}), "Flop posts again")
        self.assertEqual(a.memory["holds"], [])
        self.assertIn("Stopped", a.run_command("stop_experiment", {}))
class ModeStatsTest(unittest.TestCase):
    def test_viral_mode_ranks_first_even_with_weak_average(self):
        vids = [{"mode": "recap", "views": 3000}, {"mode": "recap", "views": 2800},
                {"mode": "best stretch", "views": 900000}, {"mode": "best stretch", "views": 50},
                {"mode": "best stretch", "views": 40}, {"views": 10}]
        st = agent.mode_stats(vids)
        self.assertEqual(list(st)[0], "best stretch")
        self.assertEqual(st["best stretch"]["best_views"], 900000)
        self.assertEqual(st["recap"]["videos"], 2)

    def test_owner_notes_reach_the_prompt(self):
        self.assertEqual(agent.owner_notes_text({}), "")
        self.assertIn("Keep Best stretch", agent.owner_notes_text({"notes": [{"text": "Keep Best stretch on"}]}))


class RepoMusicTests(TempDirTest):
    """Songs committed to assets/music/<kind>/ come down to the laptop by themselves."""

    def test_downloads_new_songs_once(self):
        listing = {"hype": [{"type": "file", "name": "A.mp3", "path": "assets/music/hype/A.mp3", "size": 3},
                            {"type": "file", "name": "notes.txt", "path": "assets/music/hype/notes.txt", "size": 1}]}
        calls = []

        def request(method, path, accept=None, sink=None, **kw):
            calls.append(path)
            if path.startswith("/contents/assets/music/hype/"):
                sink(b"abc")
                return 200, {}, b""
            kind = path.rsplit("/", 1)[1]
            return (200, {}, json.dumps(listing[kind]).encode()) if kind in listing else (404, {}, b"")

        me = mock.Mock(gh=mock.Mock(request=request))
        (self.tmp / "laptop").mkdir()
        with mock.patch.object(agent, "HERE", self.tmp / "laptop"):
            self.assertEqual(agent.Agent.repo_music(me), 1)
            self.assertEqual((self.tmp / "assets/music/hype/A.mp3").read_bytes(), b"abc")
            self.assertFalse((self.tmp / "assets/music/hype/notes.txt").exists())
            calls.clear()
            self.assertEqual(agent.Agent.repo_music(me), 0)          # already here: not downloaded again
            self.assertFalse(any(c.endswith("A.mp3") for c in calls))
