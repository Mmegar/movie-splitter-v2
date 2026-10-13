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
    return [r for r in fake.releases.values() if r["tag_name"].startswith("incoming-")]


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
        rid = inc["tag_name"][len("incoming-"):]
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
        rid = rel["tag_name"][len("incoming-"):]
        self.fake.releases.pop(rel["id"])
        gh = ghapi.GitHub("o/r", "t", self.fake.api)
        out = gh.create_release("parts-" + rid, "p", "{}")
        f = self.tmp / "Film (2015) - Clip 01 - The chase.mp4"
        f.write_bytes(b"clip!")
        names = transport.send(gh, out, f, "clips-01")
        gh.update_release(out["id"], body=json.dumps({"movie": "Film (2015)", "done": True, "clips": True, "parts": [
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
