"""The app-driven parts of the laptop helper: pause switch, speed limit, commands, clips, self-update."""
import json
import os
from unittest import mock

from tests import test_agent as base

import agent
import ghapi
import status
import transport


def commands(fake, *cmds):
    fake.put_file("config/commands.json", json.dumps({"commands": list(cmds)}), "main")


def incoming(fake):
    return [r for r in fake.releases.values() if transport.kind_of(r) == "incoming"]


class PauseTest(base.AgentTestBase):
    def test_paused_in_app_sends_nothing_and_says_why(self):
        self.fake.put_file("config/settings.json", json.dumps({"paused": True}), "main")
        (self.watch / "Film.mkv").write_bytes(b"x" * 100)
        a = self.make_agent()
        a.refresh_settings(force=True)
        a.watcher.ready(now=0)
        a.watcher.stable = 0
        a.tick(now=10 ** 9)
        self.assertEqual(incoming(self.fake), [])
        self.assertEqual(self.fake.json_file("laptop.json")["paused"], "Paused in the app")
        self.assertEqual(self.fake.dispatches, [])

    def test_pause_mid_send_stops_then_carries_on(self):
        movie = self.watch / "Big.mkv"
        movie.write_bytes(os.urandom(12_000))
        a = self.make_agent()
        calls = [0]

        def flip(n, upload=False):
            calls[0] += 1
            if calls[0] == 2:
                raise agent.Paused("Paused in the app")
        with mock.patch.object(a, "gate", flip):
            self.send_ready(a)
        rel = incoming(self.fake)[0]
        self.assertFalse(json.loads(rel["body"])["complete"])
        self.send_ready(a)                                     # unpaused: carries on
        rel = incoming(self.fake)[0]
        self.assertTrue(json.loads(rel["body"])["complete"])

    def test_on_battery(self):
        a = self.make_agent()
        a.remote_settings["pause_on_battery"] = True
        with mock.patch.object(agent, "on_battery", return_value=True):
            self.assertEqual(a.paused_reason(), "Waiting: the laptop is on battery")
        a.last["battery"] = 0
        with mock.patch.object(agent, "on_battery", return_value=False):
            self.assertIsNone(a.paused_reason())

    def test_upload_speed_limit_waits(self):
        a = self.make_agent()
        a.remote_settings["upload_limit_mbps"] = 8          # 1 MB/s
        with mock.patch.object(agent.time, "sleep") as sleep:
            a.gate(2_000_000, upload=True)
        self.assertAlmostEqual(sleep.call_args[0][0], 2.0, delta=0.2)


class CommandTest(base.AgentTestBase):
    def test_library_to_queue_reorder_and_bin(self):
        lib = self.tmp / "dl"
        lib.mkdir()
        (lib / "A.mkv").write_bytes(b"a")
        (lib / "B.mkv").write_bytes(b"b")
        done = self.out / "Done"
        done.mkdir(parents=True)
        (done / "Old.mkv").write_bytes(b"o")
        a = self.make_agent()
        a.heartbeat(force=True)
        lj = self.fake.json_file("laptop.json")
        self.assertEqual(sorted(f["name"] for f in lj["library"]), ["A.mkv", "B.mkv"])
        self.assertEqual(lj["bin"][0]["name"], "Old.mkv")
        commands(self.fake,
                 {"id": "1", "type": "to_queue", "file": "A.mkv", "from": "library"},
                 {"id": "2", "type": "to_queue", "file": "B.mkv", "from": "library"},
                 {"id": "3", "type": "queue_order", "order": ["B.mkv", "A.mkv"]},
                 {"id": "4", "type": "to_bin", "file": "Old.mkv", "from": "done"},
                 {"id": "5", "type": "to_queue", "file": "../../etc/passwd", "from": "library"})
        with mock.patch.object(agent, "send_to_trash", return_value=True) as trash:
            a.check_commands()
        self.assertTrue((self.watch / "A.mkv").exists() and (self.watch / "B.mkv").exists())
        trash.assert_called_once_with(done / "Old.mkv")
        self.assertEqual([p.name for p in a.queue_sorted(a.watcher.candidates())], ["B.mkv", "A.mkv"])
        lj = self.fake.json_file("laptop.json")
        self.assertEqual([q["name"] for q in lj["queue"]], ["B.mkv", "A.mkv"])
        self.assertEqual(lj["commands"]["5"]["status"], "failed")
        self.assertEqual({k: v["status"] for k, v in lj["commands"].items() if k != "5"},
                         {"1": "done", "2": "done", "3": "done", "4": "done"})
        # done commands aren't run again
        (self.watch / "A.mkv").rename(lib / "A.mkv")
        a.check_commands()
        self.assertTrue((lib / "A.mkv").exists())

    def test_queue_order_decides_what_is_sent_first(self):
        for n in ("A.mkv", "B.mkv"):
            (self.watch / n).write_bytes(b"x")
        a = self.make_agent()
        a.memory["queue_order"] = ["B.mkv"]
        sent = []
        with mock.patch.object(a, "send_movie", lambda p, *x, **k: sent.append(p.name)):
            self.send_ready(a)
        self.assertEqual(sent, ["B.mkv", "A.mkv"])


class ClipTest(base.ReceiveTest):
    def finished_movie(self, a):
        movie, inc = self.sent_movie(a, "Film.2015.mkv")
        rid = transport.id_of(inc)
        self.fake.releases.pop(inc["id"])
        self.fake_cutter(rid, "Film (2015)", [(1, b"one")])
        a.receive_parts()
        return self.out / "Done" / "Film.2015.mkv"

    def test_clip_request_sends_original_and_clips_come_back(self):
        a = self.make_agent()
        original = self.finished_movie(a)
        self.assertTrue(original.exists())
        mid = status.movie_id("Film (2015)")
        self.assertEqual(a.memory["originals"][mid]["path"], str(original))
        commands(self.fake, {"id": "c1", "type": "clip", "movie_id": mid, "movie": "Film (2015)",
                             "start": 61.5, "end": 95, "title": "The chase"})
        a.check_commands()
        rel = incoming(self.fake)[0]
        meta = json.loads(rel["body"])
        self.assertEqual(meta["clips"], [{"id": "c1", "start": 61.5, "end": 95.0, "title": "The chase"}])
        self.assertEqual(meta["movie"], "Film (2015)")
        self.assertEqual(self.fake.json_file("laptop.json")["commands"]["c1"]["status"], "working")
        a.check_commands()                                     # not sent twice while it's being cut
        self.assertEqual(len(incoming(self.fake)), 1)
        # the cutter makes the clip
        rid = transport.id_of(rel)
        self.fake.releases.pop(rel["id"])
        gh = ghapi.GitHub("o/r", "t", self.fake.api)
        out = gh.create_release("parts-" + rid, "p", "{}")
        f = self.tmp / "Film (2015) - Clip 01 - The chase.mp4"
        f.write_bytes(b"clip!")
        names = transport.send(gh, out, f, "clips-01")
        gh.update_release(out["id"], body=json.dumps({"kind": "parts", "id": rid, "movie": "Film (2015)", "done": True, "clips": True, "parts": [
            {"mode": "clips", "label": "Custom clips", "n": 1, "file": f.name, "assets": names,
             "caption": "lol #fyp"}]}))
        a.receive_parts()
        folder = self.out / "Ready to post" / "Film (2015)" / "Custom clips"
        self.assertEqual((folder / f.name).read_bytes(), b"clip!")
        self.assertEqual((folder / "Film (2015) - Clip 01 - The chase - caption.txt").read_text().strip(), "lol #fyp")
        self.assertTrue(original.exists())                     # the original stays in Done
        self.assertFalse((self.out / "Done" / "Film.2015 (2).mkv").exists())
        lj = self.fake.json_file("laptop.json")
        self.assertEqual(lj["commands"]["c1"]["status"], "done")
        self.assertIn("c1", lj["commands_done"])

    def test_clip_failed_keeps_original(self):
        a = self.make_agent()
        original = self.finished_movie(a)
        mid = status.movie_id("Film (2015)")
        commands(self.fake, {"id": "c2", "type": "clip", "movie_id": mid, "start": 1, "end": 5})
        a.check_commands()
        rel = incoming(self.fake)[0]
        meta = json.loads(rel["body"])
        meta.update(failed=True, error="bad range")
        rel["body"] = json.dumps(meta)
        a.receive_parts()
        self.assertTrue(original.exists())
        self.assertFalse((self.out / "Failed").exists())
        self.assertEqual(self.fake.json_file("laptop.json")["commands"]["c2"],
                         {"status": "failed", "note": "bad range"})

    def test_clip_without_original(self):
        a = self.make_agent()
        commands(self.fake, {"id": "c3", "type": "clip", "movie_id": "nope", "start": 1, "end": 5})
        a.check_commands()
        self.assertEqual(incoming(self.fake), [])
        self.assertEqual(self.fake.json_file("laptop.json")["commands"]["c3"]["status"], "failed")


class SelfUpdateTest(base.AgentTestBase):
    def test_updates_changed_files_only_if_valid(self):
        code = agent.HERE.parent
        a = self.make_agent()
        with mock.patch.object(agent, "HERE", self.tmp / "repo" / "laptop"), \
                mock.patch.object(agent, "CODE_FILES", ["laptop/agent.py", "names.py"]):
            (self.tmp / "repo" / "laptop").mkdir(parents=True)
            (self.tmp / "repo" / "laptop" / "agent.py").write_text("print('old')\n")
            (self.tmp / "repo" / "names.py").write_text((code / "names.py").read_text())
            self.fake.put_file("laptop/agent.py", "print('new')\n", "main")
            self.fake.put_file("names.py", (code / "names.py").read_text(), "main")
            self.assertTrue(a.self_update())
            self.assertEqual((self.tmp / "repo" / "laptop" / "agent.py").read_text(), "print('new')\n")
            self.assertEqual((self.tmp / "repo" / "laptop" / ".backup" / "agent.py").read_text(), "print('old')\n")
            self.assertFalse(a.self_update())                  # nothing new now
            self.fake.put_file("laptop/agent.py", "def broken(:\n", "main")
            with self.assertRaises(SyntaxError):
                a.self_update()
            self.assertEqual((self.tmp / "repo" / "laptop" / "agent.py").read_text(), "print('new')\n")


class PartActionTest(base.AgentTestBase):
    def setUp(self):
        super().setUp()
        st = status.empty_state()
        part = {"n": 3, "drive_path": "Ready to post/M/Best stretch/M - Part 03.mp4", "caption": "lol",
                "post": {"status": "none", "at": None, "url": None, "error": None}}
        st["movies"] = [{"id": "m", "name": "M", "status": "done", "started": "2026-10-10T10:00:00+13:00",
                         "modes": {"stretch": {"label": "Best stretch", "planned": 3, "parts": [part]}}}]
        self.fake.put_file("state.json", json.dumps(st), "status")
        f = self.out / part["drive_path"]
        f.parent.mkdir(parents=True)
        f.write_bytes(b"full quality part")

    def post_status(self):
        return self.fake.json_file("state.json")["movies"][0]["modes"]["stretch"]["parts"][0]["post"]["status"]

    def test_send_to_tiktok_now(self):
        a = self.make_agent(composio_api_key="k", composio_user_id="u")
        commands(self.fake, {"id": "p1", "type": "post_part", "movie_id": "m", "mode": "stretch", "n": 3})
        calls = []
        with mock.patch.object(agent.posting, "post_video",
                               lambda path, cap, priv, creds, publish=True: calls.append((path.read_bytes(), publish))
                               or (True, "v_inbox~1", None)):
            a.check_commands()
        self.assertEqual(calls, [(b"full quality part", False)])
        self.assertEqual(self.post_status(), "inbox")
        self.assertEqual(self.fake.json_file("laptop.json")["commands"]["p1"]["status"], "done")

    def test_mark_posted(self):
        a = self.make_agent()
        commands(self.fake, {"id": "p2", "type": "mark_posted", "movie_id": "m", "mode": "stretch", "n": 3})
        a.check_commands()
        self.assertEqual(self.post_status(), "posted")

    def test_save_to_phone_puts_encrypted_copy_on_previews_branch(self):
        import previews
        a = self.make_agent()
        commands(self.fake, {"id": "p3", "type": "share_part", "movie_id": "m", "mode": "stretch", "n": 3})
        a.check_commands()
        files = self.fake.bin_branches["previews"]
        self.assertIn("share/m/stretch-003.bin", files)
        self.assertEqual(previews.decrypt(self.fake.blobs[files["share/m/stretch-003.bin"]], "test-topic"),
                         b"full quality part")
        lj = self.fake.json_file("laptop.json")
        self.assertEqual(lj["shared"], ["share/m/stretch-003.bin"])
        self.assertEqual(lj["commands"]["p3"]["status"], "done")

    def test_save_all_shares_every_part_in_order_and_skips_missing(self):
        a = self.make_agent()
        commands(self.fake, {"id": "sa", "type": "share_all", "movie_id": "m", "mode": "stretch", "ns": [3, 9]})
        a.check_commands()
        lj = self.fake.json_file("laptop.json")
        self.assertEqual(lj["shared"], ["share/m/stretch-003.bin"])
        self.assertEqual(lj["commands"]["sa"], {"status": "done",
                                                "note": "1 parts ready to save (not on the laptop: 9)"})

    def test_big_part_goes_up_in_pieces(self):
        import previews
        a = self.make_agent()
        commands(self.fake, {"id": "p5", "type": "share_part", "movie_id": "m", "mode": "stretch", "n": 3})
        with mock.patch.object(previews, "PIECE", 10):
            a.check_commands()
        files, blobs = self.fake.bin_branches["previews"], self.fake.blobs
        n = json.loads(blobs[files["share/m/stretch-003.bin"]])["pieces"]
        locked = b"".join(blobs[files[f"share/m/stretch-003.bin.{i}"]] for i in range(1, n + 1))
        self.assertEqual(previews.decrypt(locked, "test-topic"), b"full quality part")
        self.assertEqual(self.fake.json_file("laptop.json")["commands"]["p5"]["status"], "done")

    def test_missing_part_file_fails_clearly(self):
        a = self.make_agent()
        (self.out / "Ready to post/M/Best stretch/M - Part 03.mp4").unlink()
        commands(self.fake, {"id": "p4", "type": "share_part", "movie_id": "m", "mode": "stretch", "n": 3})
        a.check_commands()
        c = self.fake.json_file("laptop.json")["commands"]["p4"]
        self.assertEqual(c["status"], "failed")
        self.assertIn("isn't on the laptop", c["note"])


class DoubleTapTest(PartActionTest):
    def test_same_part_twice_is_sent_once(self):
        a = self.make_agent(composio_api_key="k", composio_user_id="u")
        commands(self.fake, {"id": "d1", "type": "post_part", "movie_id": "m", "mode": "stretch", "n": 3},
                 {"id": "d2", "type": "post_part", "movie_id": "m", "mode": "stretch", "n": 3})
        calls = []
        with mock.patch.object(agent.posting, "post_video",
                               lambda path, cap, priv, creds, publish=True: calls.append(1) or (True, "v~1", None)):
            a.check_commands()
        self.assertEqual(len(calls), 1)
        lj = self.fake.json_file("laptop.json")["commands"]
        self.assertEqual((lj["d1"]["status"], lj["d2"]["status"]), ("done", "done"))


class ResumeTest(base.AgentTestBase):
    def test_send_started_by_older_version_keeps_its_piece_size(self):
        movie = self.watch / "Big.mkv"
        movie.write_bytes(os.urandom(15_000))
        a = self.make_agent(chunk_mb=0.006)                 # old version: 6 kB pieces
        self.fake.fail_upload_names = {"movie.001"}
        self.send_ready(a)                                  # stops after the first piece
        b = self.make_agent(chunk_mb=0.002)                 # new version: smaller pieces
        b.memory["sends"] = a.memory["sends"]
        for s in b.memory["sends"].values():
            s.pop("chunk", None)                            # as if written by the old version
        b.watcher.ready(now=0)
        b.watcher.stable = 0
        b.check_folder()
        rel = incoming(self.fake)[0]
        gh = ghapi.GitHub("o/r", "t", self.fake.api)
        pieces = transport.assets_for(gh.assets(rel["id"]), "movie")
        self.assertEqual([p["size"] for p in pieces], [6000, 6000, 3000])   # first piece reused, same size
        out = self.tmp / "o.mkv"
        transport.receive(gh, pieces, out)
        self.assertEqual(out.read_bytes(), movie.read_bytes())

    def test_switching_pause_off_restarts_the_cutter(self):
        a = self.make_agent()
        self.fake.put_file("config/settings.json", json.dumps({"paused": True}), "main")
        a.refresh_settings(force=True)
        self.assertEqual(self.fake.dispatches, [])
        self.fake.put_file("config/settings.json", json.dumps({"paused": False}), "main")
        a.refresh_settings(force=True)
        self.assertEqual(self.fake.dispatches, [("make-parts.yml", {"ref": "main"})])


class PostAllTest(base.AgentTestBase):
    def setUp(self):
        super().setUp()
        st = status.empty_state()
        parts = []
        for n in (1, 2, 3, 4):
            parts.append({"n": n, "drive_path": f"Ready to post/M/Best stretch/M - Part {n:02}.mp4", "caption": f"c{n}",
                          "post": {"status": "inbox" if n == 1 else "none", "at": None, "url": None, "error": None}})
            f = self.out / parts[-1]["drive_path"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"p%d" % n)
        st["movies"] = [{"id": "m", "name": "M", "status": "done", "started": "2026-10-10T10:00:00+13:00",
                         "modes": {"stretch": {"label": "Best stretch", "planned": 4, "parts": parts}}}]
        self.fake.put_file("state.json", json.dumps(st), "status")
        self.a = self.make_agent(composio_api_key="k", composio_user_id="u")
        commands(self.fake, {"id": "all1", "type": "post_all", "movie_id": "m", "mode": "stretch"})
        self.sent = []

    def check(self, results):
        def fake(path, cap, priv, creds, publish=True):
            self.sent.append(path.name)
            return results.pop(0)
        with mock.patch.object(agent.posting, "post_video", fake), mock.patch.object(agent.time, "sleep"):
            self.a.check_commands()
        return self.fake.json_file("laptop.json")["commands"]["all1"]

    def test_sends_every_part_not_sent_yet_in_order(self):
        cmd = self.check([(True, "v", None)] * 3)
        self.assertEqual(self.sent, ["M - Part 02.mp4", "M - Part 03.mp4", "M - Part 04.mp4"])  # 1 was sent before
        self.assertEqual(cmd, {"status": "done", "note": "All 3 parts are in TikTok, in order"})

    def test_waits_at_tiktoks_limit_then_carries_on_in_order(self):
        cmd = self.check([(True, "v", None), (False, None, "spam_risk_too_many_pending_share")])
        self.assertEqual(self.sent, ["M - Part 02.mp4", "M - Part 03.mp4"])
        self.assertEqual(cmd["status"], "working")
        self.assertIn("Trying part 3 again", cmd["note"])
        self.check([])                                         # too early: nothing is sent
        self.assertEqual(len(self.sent), 2)
        b = self.make_agent(composio_api_key="k", composio_user_id="u")   # e.g. after a restart
        b.memory["post_jobs"]["all1"]["next_try"] = 0           # 10 minutes later
        self.a = b
        cmd = self.check([(True, "v", None), (True, "v", None)])
        self.assertEqual(self.sent, ["M - Part 02.mp4", "M - Part 03.mp4", "M - Part 03.mp4", "M - Part 04.mp4"])
        self.assertEqual(cmd["status"], "done")


class PostAllStillCuttingTest(PostAllTest):
    def test_parts_cut_later_go_too_in_order(self):
        st = self.fake.json_file("state.json", "status")
        st["movies"][0]["status"] = "processing"
        self.fake.put_file("state.json", json.dumps(st), "status")
        cmd = self.check([(True, "v", None)] * 3)
        self.assertEqual(cmd["status"], "working")             # movie still cutting: waits for more
        self.assertIn("next part", cmd["note"])
        p5 = dict(st["movies"][0]["modes"]["stretch"]["parts"][-1], n=5, drive_path="Ready to post/M/Best stretch/M - Part 05.mp4")
        p5["post"] = {"status": "none"}
        (self.out / p5["drive_path"]).write_bytes(b"p5")
        st["movies"][0]["modes"]["stretch"]["parts"].append(p5)
        st["movies"][0]["status"] = "done"
        self.fake.put_file("state.json", json.dumps(st), "status")
        self.a.memory["post_jobs"]["all1"]["next_try"] = 0
        cmd = self.check([(True, "v", None)])
        self.assertEqual(self.sent, ["M - Part 02.mp4", "M - Part 03.mp4", "M - Part 04.mp4", "M - Part 05.mp4"])
        self.assertEqual(cmd, {"status": "done", "note": "All 4 parts are in TikTok, in order"})


class CancelPostTest(PostAllTest):
    def test_cancel_while_waiting_at_tiktoks_limit(self):
        self.check([(True, "v", None), (False, None, "spam_risk_too_many_pending_share")])
        commands(self.fake, {"id": "all1", "type": "post_all", "movie_id": "m", "mode": "stretch"},
                 {"id": "x1", "type": "cancel_post", "target": "all1"})
        self.a.memory["post_jobs"]["all1"]["next_try"] = 0
        self.check([])
        cmds = self.fake.json_file("laptop.json")["commands"]
        self.assertEqual(cmds["all1"], {"status": "failed", "note": "Cancelled. 1 of 3 were sent before that"})
        self.assertEqual(self.sent, ["M - Part 02.mp4", "M - Part 03.mp4"])     # nothing more went
        self.assertEqual(self.a.memory["post_jobs"], {})

    def test_cancel_stops_before_the_next_part(self):
        commands(self.fake, {"id": "all1", "type": "post_all", "movie_id": "m", "mode": "stretch"},
                 {"id": "x2", "type": "cancel_post", "target": "all"})
        cmd = self.check([])
        self.assertEqual(self.sent, [])
        self.assertEqual(cmd["status"], "failed")
        self.assertTrue(cmd["note"].startswith("Cancelled"))

    def test_parts_marked_posted_are_skipped_and_marking_many_works(self):
        commands(self.fake, {"id": "mk", "type": "mark_posted", "movie_id": "m", "mode": "stretch", "ns": [2, 3]})
        self.a.check_commands()
        parts = self.fake.json_file("state.json")["movies"][0]["modes"]["stretch"]["parts"]
        self.assertEqual([p["post"]["status"] for p in parts], ["inbox", "posted", "posted", "none"])
        commands(self.fake, {"id": "all2", "type": "post_all", "movie_id": "m", "mode": "stretch"})
        def fake(path, cap, priv, creds, publish=True):
            self.sent.append(path.name)
            return True, "v", None
        with mock.patch.object(agent.posting, "post_video", fake), mock.patch.object(agent.time, "sleep"):
            self.a.check_commands()
        self.assertEqual(self.sent, ["M - Part 04.mp4"])
        self.assertEqual(self.fake.json_file("laptop.json")["commands"]["all2"]["status"], "done")


class StyleTest(base.AgentTestBase):
    def test_make_one_now_puts_the_movie_first_with_its_style_and_character(self):
        lib = self.tmp / "dl"
        lib.mkdir()
        (lib / "New.mkv").write_bytes(b"n")
        (self.watch / "Waiting.mkv").write_bytes(b"w")
        a = self.make_agent()
        commands(self.fake, {"id": "m1", "type": "make_now", "file": "New.mkv", "from": "library",
                             "style": "classic", "character": "Blanc"})
        a.check_commands()
        self.assertTrue((self.watch / "New.mkv").exists())
        self.assertEqual([p.name for p in a.queue_sorted(a.watcher.candidates())], ["New.mkv", "Waiting.mkv"])
        q = a.queue_files()[0]
        self.assertEqual((q["name"], q["style"], q.get("character")), ("New.mkv", "classic", "Blanc"))
        self.assertEqual(self.fake.json_file("laptop.json")["commands"]["m1"]["status"], "done")

    def test_style_picked_in_the_app_goes_with_the_movie(self):
        movie = self.watch / "Big.Film.2015.mkv"
        movie.write_bytes(os.urandom(5_000))
        a = self.make_agent()
        self.assertEqual(a.queue_files()[0]["style"], "zoom")             # the default: zoomed in, following the characters
        commands(self.fake, {"id": "s1", "type": "set_style", "file": "Big.Film.2015.mkv", "style": "classic"})
        a.check_commands()
        self.assertEqual(a.queue_files()[0]["style"], "classic")
        self.assertEqual(self.fake.json_file("laptop.json")["queue"][0]["style"], "classic")
        a.send_movie(movie)
        rel = [r for r in self.fake.releases.values() if transport.kind_of(r) == "incoming"][0]
        self.assertEqual(json.loads(rel["body"])["style"], "classic")

    def test_default_comes_from_settings(self):
        (self.watch / "Other.mkv").write_bytes(b"x" * 100)
        a = self.make_agent()
        self.assertEqual(a.queue_files()[0]["style"], "zoom")
        a.remote_settings["cut_style"] = "classic"
        self.assertEqual(a.queue_files()[0]["style"], "classic")
