import json
import os
import sys
import types
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from tests.helpers import TempDirTest

import poster
import status

NZ = ZoneInfo("Pacific/Auckland")
POSTING = {"enabled": True, "privacy": "self_only", "modes": ["stretch"], "per_day": 3,
           "times": ["12:00", "18:00", "21:00"], "timezone": "Pacific/Auckland", "hashtags": "#movie #fyp"}


def part(n, st="none", at=None, attempts=0, path=True):
    return {"n": n, "start": 0, "end": 60, "seconds": 60, "caption": f"cap {n} #fyp",
            "drive_path": f"Ready to post/M/Best stretch/M - Part {n:02}.mp4" if path else None,
            "post": {"status": st, "at": at, "url": None, "error": None, "attempts": attempts}}


def movie(name, started, stretch=(), highlights=()):
    return {"id": status.movie_id(name), "name": name, "started": started, "status": "done",
            "modes": {"stretch": {"label": "Best stretch", "planned": len(stretch), "parts": list(stretch)},
                      "highlights": {"label": "Highlights", "planned": len(highlights), "parts": list(highlights)}}}


class ScheduleTest(TempDirTest):
    def test_next_part_oldest_movie_and_part_order(self):
        state = {"movies": [
            movie("New", "2026-10-12T10:00:00+13:00", [part(1)]),
            movie("Old", "2026-10-10T10:00:00+13:00", [part(2), part(1, "posted", "2026-10-11T12:00:00+13:00")],
                  [part(1)]),
        ]}
        m, mode, p = poster.next_part(state, ["stretch"])
        self.assertEqual((m["name"], mode, p["n"]), ("Old", "stretch", 2))
        m, mode, p = poster.next_part(state, ["highlights", "stretch"])
        self.assertEqual((m["name"], mode, p["n"]), ("Old", "highlights", 1))

    def test_failed_parts_retry_until_limit(self):
        state = {"movies": [movie("A", "2026-10-10T10:00:00+13:00", [part(1, "failed", attempts=1), part(2)])]}
        self.assertEqual(poster.next_part(state, ["stretch"])[2]["n"], 1)
        state["movies"][0]["modes"]["stretch"]["parts"][0]["post"]["attempts"] = poster.MAX_ATTEMPTS
        self.assertEqual(poster.next_part(state, ["stretch"])[2]["n"], 2)

    def test_nothing_to_post(self):
        state = {"movies": [movie("A", "2026-10-10T10:00:00+13:00", [part(1, "posted", "2026-10-10T12:00:00+13:00")],
                                  [part(1)])]}
        self.assertIsNone(poster.next_part(state, ["stretch"]))
        self.assertIsNone(poster.next_part({}, ["stretch"]))

    def test_time_windows(self):
        self.assertEqual(poster.slot_now(datetime(2026, 10, 13, 18, 59, tzinfo=NZ), POSTING["times"]), (18, 0))
        self.assertIsNone(poster.slot_now(datetime(2026, 10, 13, 19, 0, tzinfo=NZ), POSTING["times"]))
        self.assertEqual(poster.parse_times(["7:5", "bad", "21:00"]), [(7, 5), (21, 0)])

    def test_per_day_and_hour_counts_in_local_time(self):
        state = {"movies": [movie("A", "2026-10-10T10:00:00+13:00", [
            part(1, "posted", "2026-10-13T12:03:00+13:00"),
            part(2, "posted", "2026-10-12T23:30:00Z"),     # = 13 Oct 12:30 in NZ
            part(3, "posted", "2026-10-12T10:00:00+13:00"),  # yesterday
            part(4, "failed", "2026-10-13T18:01:00+13:00"),
        ])]}
        now = datetime(2026, 10, 13, 18, 10, tzinfo=NZ)
        self.assertEqual(poster.posted_today(state, now), 2)
        self.assertFalse(poster.posted_this_hour(state, now))
        self.assertTrue(poster.posted_this_hour(state, datetime(2026, 10, 13, 12, 40, tzinfo=NZ)))

    def test_next_at(self):
        now = datetime(2026, 10, 13, 12, 5, tzinfo=NZ)
        self.assertEqual(poster.next_at(now, POSTING, 1), "2026-10-13T18:00:00+13:00")
        self.assertEqual(poster.next_at(now, POSTING, 3), "2026-10-14T12:00:00+13:00")
        self.assertEqual(poster.next_at(datetime(2026, 10, 13, 22, 0, tzinfo=NZ), POSTING, 0),
                         "2026-10-14T12:00:00+13:00")
        self.assertIsNone(poster.next_at(now, dict(POSTING, enabled=False), 0))

    def test_caption(self):
        self.assertEqual(poster.build_caption("Hi\n#fyp #viral", "#movie #FYP"), "Hi\n#fyp #viral\n#movie")
        self.assertEqual(poster.build_caption(None, "#movie"), "#movie")
        self.assertEqual(len(poster.build_caption("x" * 3000, "")), 2200)


class PostRunTest(TempDirTest):
    """poster.main() end to end, with the fake rclone, local status file, and a pretend TikTok."""

    def setUp(self):
        super().setUp()
        self.drive = self.tmp / "drive"
        p = self.drive / "Ready to post" / "M" / "Best stretch"
        p.mkdir(parents=True)
        (p / "M - Part 01.mp4").write_bytes(b"video")
        os.environ.update(self.fake_rclone_env(self.drive))
        os.environ.update(STATUS_DIR=str(self.tmp / "status"), NTFY_TOPIC="-")
        for k in ("GITHUB_ACTIONS", "STATUS"):
            os.environ.pop(k, None)
        settings_file = self.tmp / "settings.json"
        settings_file.write_text(json.dumps({"posting": POSTING}))
        os.environ["SETTINGS_FILE"] = str(settings_file)
        (self.tmp / "status").mkdir()
        st = status.empty_state()
        st["movies"] = [movie("M", "2026-10-10T10:00:00+13:00", [part(1), part(2)])]
        (self.tmp / "status" / "state.json").write_text(json.dumps(st))
        status.reset()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self._env.get("PWD", "/"))
        status.reset()
        super().tearDown()

    def state(self):
        return json.loads((self.tmp / "status" / "state.json").read_text())

    def test_force_posts_and_records(self):
        calls = []

        def fake_post(path, caption, privacy):
            calls.append((path.read_bytes(), caption, privacy))
            return True, "https://www.tiktok.com/@me/video/1", None
        with mock.patch.object(poster, "post_video", fake_post):
            poster.main(["--force"])
        self.assertEqual(calls, [(b"video", "cap 1 #fyp\n#movie", "SELF_ONLY")])
        st = self.state()
        post = st["movies"][0]["modes"]["stretch"]["parts"][0]["post"]
        self.assertEqual((post["status"], post["url"], post["attempts"]), ("posted", "https://www.tiktok.com/@me/video/1", 1))
        self.assertEqual(st["events"][0]["type"], "posted")
        self.assertIn("cap 1", st["events"][0]["message"])
        self.assertEqual(st["posting"]["posted_today"], 1)
        self.assertEqual(st["run"]["stage"], "idle")
        self.assertFalse((self.tmp / "work-post").exists())

    def test_failed_post_and_missing_file(self):
        (self.drive / "Ready to post" / "M" / "Best stretch" / "M - Part 01.mp4").unlink()
        with mock.patch.object(poster, "post_video", side_effect=AssertionError("should not post")):
            poster.main(["--force"])
        st = self.state()
        post = st["movies"][0]["modes"]["stretch"]["parts"][0]["post"]
        self.assertEqual(post["status"], "failed")
        self.assertIn("Couldn't download", post["error"])
        self.assertEqual(st["events"][0]["type"], "post_failed")

    def test_dry_run_changes_nothing(self):
        before = self.state()
        with mock.patch.object(poster, "post_video", side_effect=AssertionError("should not post")):
            self.assertEqual(poster.main(["--dry-run", "--force"]), 0)
        self.assertEqual(self.state(), before)

    def test_outside_schedule_does_nothing_but_updates_next_at(self):
        with mock.patch.object(poster, "post_video", side_effect=AssertionError("should not post")), \
                mock.patch.object(poster, "slot_now", return_value=None):
            poster.main([])
        st = self.state()
        self.assertEqual(st["movies"][0]["modes"]["stretch"]["parts"][0]["post"]["status"], "none")
        self.assertTrue(st["posting"]["enabled"])
        self.assertIsNotNone(st["posting"]["next_at"])


class ComposioCallTest(TempDirTest):
    """post_video() against a pretend composio package: checks the documented tool calls."""

    def fake_sdk(self, upload_ok=True, statuses=("PROCESSING_UPLOAD", "PUBLISH_COMPLETE")):
        calls, seq = [], list(statuses)

        class Tools:
            def execute(self, slug, arguments, **kw):
                calls.append((slug, arguments, kw))
                if slug == "TIKTOK_UPLOAD_VIDEO":
                    return {"successful": upload_ok, "data": {"data": {"publish_id": "v_pub_1"}},
                            "error": None if upload_ok else "token expired"}
                st = seq.pop(0) if seq else "PUBLISH_COMPLETE"
                return {"successful": True, "error": None,
                        "data": json.dumps({"status": st, "publicaly_available_post_id": [777]})}

        class Composio:
            def __init__(self, api_key, **kw):
                calls.append(("init", api_key, kw))
                self.tools = Tools()
        return types.SimpleNamespace(Composio=Composio), calls

    def setUp(self):
        super().setUp()
        self.video = self.tmp / "p.mp4"
        self.video.write_bytes(b"x")
        os.environ.update(COMPOSIO_API_KEY="k", COMPOSIO_USER_ID="me@example.com", TIKTOK_USERNAME="@me")
        os.environ.pop("COMPOSIO_CONNECTED_ACCOUNT_ID", None)
        os.environ.pop("COMPOSIO_TIKTOK_VERSION", None)

    def test_upload_publish_and_poll(self):
        sdk, calls = self.fake_sdk()
        with mock.patch.dict(sys.modules, {"composio": sdk}), mock.patch.object(poster.time, "sleep"):
            ok, ref, err = poster.post_video(self.video, "cap", "SELF_ONLY")
        self.assertEqual((ok, ref, err), (True, "https://www.tiktok.com/@me/video/777", None))
        init = calls[0]
        self.assertEqual(init[2]["file_upload_dirs"], [str(self.tmp.resolve())])
        self.assertTrue(init[2]["dangerously_allow_auto_upload_download_files"])
        slug, args, kw = calls[1]
        self.assertEqual(slug, "TIKTOK_UPLOAD_VIDEO")
        self.assertEqual(args, {"file_to_upload": str(self.video.resolve()), "caption": "cap", "publish": True,
                                "privacy_level": "SELF_ONLY"})
        self.assertEqual(kw["user_id"], "me@example.com")
        self.assertTrue(kw["dangerously_skip_version_check"])
        self.assertEqual([c[0] for c in calls[2:]], ["TIKTOK_FETCH_PUBLISH_STATUS"] * 2)
        self.assertEqual(calls[2][1], {"publish_id": "v_pub_1"})

    def test_upload_error(self):
        sdk, _ = self.fake_sdk(upload_ok=False)
        with mock.patch.dict(sys.modules, {"composio": sdk}):
            self.assertEqual(poster.post_video(self.video, "c", "SELF_ONLY"), (False, None, "token expired"))

    def test_tiktok_rejects(self):
        sdk, _ = self.fake_sdk(statuses=("FAILED",))
        with mock.patch.dict(sys.modules, {"composio": sdk}), mock.patch.object(poster.time, "sleep"):
            ok, ref, err = poster.post_video(self.video, "c", "SELF_ONLY")
        self.assertFalse(ok)
        self.assertEqual(ref, "v_pub_1")

    def test_missing_key_or_package(self):
        os.environ.pop("COMPOSIO_API_KEY")
        self.assertIn("COMPOSIO_API_KEY", poster.post_video(self.video, "c", "SELF_ONLY")[2])
        os.environ["COMPOSIO_API_KEY"] = "k"
        with mock.patch.dict(sys.modules, {"composio": None}):
            self.assertIn("pip install composio", poster.post_video(self.video, "c", "SELF_ONLY")[2])
