import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from tests.helpers import TempDirTest

import posting
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
        m, mode, p = posting.next_part(state, ["stretch"])
        self.assertEqual((m["name"], mode, p["n"]), ("Old", "stretch", 2))
        m, mode, p = posting.next_part(state, ["highlights", "stretch"])
        self.assertEqual((m["name"], mode, p["n"]), ("Old", "highlights", 1))

    def test_failed_parts_retry_until_limit(self):
        state = {"movies": [movie("A", "2026-10-10T10:00:00+13:00", [part(1, "failed", attempts=1), part(2)])]}
        self.assertEqual(posting.next_part(state, ["stretch"])[2]["n"], 1)
        state["movies"][0]["modes"]["stretch"]["parts"][0]["post"]["attempts"] = posting.MAX_ATTEMPTS
        self.assertEqual(posting.next_part(state, ["stretch"])[2]["n"], 2)

    def test_nothing_to_post(self):
        state = {"movies": [movie("A", "2026-10-10T10:00:00+13:00", [part(1, "posted", "2026-10-10T12:00:00+13:00")],
                                  [part(1)])]}
        self.assertIsNone(posting.next_part(state, ["stretch"]))
        self.assertIsNone(posting.next_part({}, ["stretch"]))

    def test_time_windows(self):
        self.assertEqual(posting.slot_now(datetime(2026, 10, 13, 18, 59, tzinfo=NZ), POSTING["times"]), (18, 0))
        self.assertIsNone(posting.slot_now(datetime(2026, 10, 13, 19, 0, tzinfo=NZ), POSTING["times"]))
        self.assertEqual(posting.parse_times(["7:5", "bad", "21:00"]), [(7, 5), (21, 0)])

    def test_per_day_and_hour_counts_in_local_time(self):
        state = {"movies": [movie("A", "2026-10-10T10:00:00+13:00", [
            part(1, "posted", "2026-10-13T12:03:00+13:00"),
            part(2, "posted", "2026-10-12T23:30:00Z"),     # = 13 Oct 12:30 in NZ
            part(3, "posted", "2026-10-12T10:00:00+13:00"),  # yesterday
            part(4, "failed", "2026-10-13T18:01:00+13:00"),
        ])]}
        now = datetime(2026, 10, 13, 18, 10, tzinfo=NZ)
        self.assertEqual(posting.posted_today(state, now), 2)
        self.assertFalse(posting.posted_this_hour(state, now))
        self.assertTrue(posting.posted_this_hour(state, datetime(2026, 10, 13, 12, 40, tzinfo=NZ)))

    def test_next_at(self):
        now = datetime(2026, 10, 13, 12, 5, tzinfo=NZ)
        self.assertEqual(posting.next_at(now, POSTING, 1), "2026-10-13T18:00:00+13:00")
        self.assertEqual(posting.next_at(now, POSTING, 3), "2026-10-14T12:00:00+13:00")
        self.assertEqual(posting.next_at(datetime(2026, 10, 13, 22, 0, tzinfo=NZ), POSTING, 0),
                         "2026-10-14T12:00:00+13:00")
        self.assertIsNone(posting.next_at(now, dict(POSTING, enabled=False), 0))

    def test_decide(self):
        state = {"movies": [movie("A", "2026-10-10T10:00:00+13:00", [part(1), part(2)])]}
        at_six = datetime(2026, 10, 13, 18, 10, tzinfo=NZ)
        self.assertEqual(posting.decide(state, POSTING, at_six)[0][2]["n"], 1)
        self.assertIsNone(posting.decide(state, POSTING, datetime(2026, 10, 13, 17, 0, tzinfo=NZ))[0])
        self.assertIsNone(posting.decide(state, dict(POSTING, enabled=False), at_six)[0])
        self.assertIsNotNone(posting.decide(state, dict(POSTING, enabled=False), at_six, force=True)[0])
        # part 1 not on the laptop yet: wait (keep the order); gone for good: skip it
        self.assertIsNone(posting.decide(state, POSTING, at_six, available=lambda p: None)[0])
        pick = posting.decide(state, POSTING, at_six, available=lambda p: {1: None, 2: True}[p["n"]])
        self.assertIsNone(pick[0])
        pick = posting.decide(state, POSTING, at_six, available=lambda p: False if p["n"] == 1 else True)
        self.assertEqual(pick[0][2]["n"], 2)

    def test_caption(self):
        self.assertEqual(posting.build_caption("Hi\n#fyp #viral", "#movie #FYP"), "Hi\n#fyp #viral\n#movie")
        self.assertEqual(posting.build_caption(None, "#movie"), "#movie")
        self.assertEqual(len(posting.build_caption("x" * 3000, "")), 2200)


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
        self.creds = {"api_key": "k", "user_id": "me@example.com", "username": "@me"}

    def test_upload_publish_and_poll(self):
        sdk, calls = self.fake_sdk()
        with mock.patch.dict(sys.modules, {"composio": sdk}), mock.patch.object(posting.time, "sleep"):
            ok, ref, err = posting.post_video(self.video, "cap", "SELF_ONLY", self.creds)
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
            self.assertEqual(posting.post_video(self.video, "c", "SELF_ONLY", self.creds), (False, None, "token expired"))

    def test_tiktok_rejects(self):
        sdk, _ = self.fake_sdk(statuses=("FAILED",))
        with mock.patch.dict(sys.modules, {"composio": sdk}), mock.patch.object(posting.time, "sleep"):
            ok, ref, err = posting.post_video(self.video, "c", "SELF_ONLY", self.creds)
        self.assertFalse(ok)
        self.assertEqual(ref, "v_pub_1")

    def test_missing_key_or_package(self):
        self.assertIn("composio_api_key", posting.post_video(self.video, "c", "SELF_ONLY", {})[2])
        self.assertIn("composio_user_id", posting.post_video(self.video, "c", "SELF_ONLY", {"api_key": "k"})[2])
        with mock.patch.dict(sys.modules, {"composio": None}):
            self.assertIn("pip install composio", posting.post_video(self.video, "c", "SELF_ONLY", self.creds)[2])


class GapScheduleTest(unittest.TestCase):
    """when = "gap": one part every 15-30 minutes (random), inside active hours, up to per_day."""
    P = {"enabled": True, "when": "gap", "gap_min": 15, "gap_max": 30, "active_from": "09:00",
         "active_to": "23:30", "per_day": 10, "modes": ["recap"]}

    def state(self):
        return {"movies": [{"id": "a", "name": "A", "started": "2026-10-10T10:00:00+13:00", "modes": {"recap": {"parts": [
            {"n": 2, "drive_path": "p2", "post": {}}, {"n": 1, "drive_path": "p1", "post": {}}]}}}]}

    def test_series_order_and_gap(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Pacific/Auckland")
        now = datetime(2026, 10, 13, 12, 0, tzinfo=tz)
        pick, _ = posting.decide(self.state(), self.P, now)
        self.assertEqual(pick[2]["n"], 1)                                  # Part 1 first
        pick, why = posting.decide(self.state(), self.P, now, next_due=now + timedelta(minutes=20))
        self.assertIsNone(pick)
        self.assertIn("gap", why)
        pick, why = posting.decide(self.state(), self.P, datetime(2026, 10, 13, 3, 0, tzinfo=tz))
        self.assertIsNone(pick)                                            # asleep: outside active hours
        for _ in range(50):
            self.assertTrue(15 <= posting.next_gap_minutes(self.P) <= 30)

    def test_next_at_moves_into_active_hours(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Pacific/Auckland")
        now = datetime(2026, 10, 13, 23, 50, tzinfo=tz)
        self.assertEqual(posting.next_at(now, self.P, 1), "2026-10-14T09:00:00+13:00")
