import json
import os
import subprocess
import sys

from tests.helpers import ROOT, TempDirTest

import settings


class SettingsTest(TempDirTest):
    def write(self, data):
        p = self.tmp / "settings.json"
        p.write_text(json.dumps(data) if not isinstance(data, str) else data)
        return p

    def test_real_settings_file_loads(self):
        s = settings.load(ROOT / "config" / "settings.json", env={})     # whatever the app saved: it loads
        self.assertIn("posting", s)
        self.assertIn(s["posting"]["timezone"], ("Pacific/Auckland",) + (s["posting"]["timezone"],))

    def test_missing_file_gives_defaults(self):
        s = settings.load(self.tmp / "nope.json", env={})
        self.assertEqual(s, settings.DEFAULTS)

    def test_broken_file_gives_defaults(self):
        s = settings.load(self.write("{not json"), env={})
        self.assertEqual(s["modes"], ["recap", "character", "hype"])

    def test_file_values_and_partial_posting(self):
        s = settings.load(self.write({"cockroach_size": 90, "posting": {"enabled": True}}), env={})
        self.assertEqual(s["cockroach_size"], 90)
        self.assertTrue(s["posting"]["enabled"])
        self.assertEqual(s["posting"]["per_day"], 3)          # filled in from defaults

    def test_env_overrides_with_types(self):
        env = {"MODES": "all, highlights", "COCKROACH_SIZE": "80", "AUTO_SUBTITLES": "off",
               "LINE_OPACITY": "0.2", "POSTING_ENABLED": "on", "POSTING_TIMES": "09:00,10:30",
               "FLIP_EVERY": "-1", "COCKROACH_CHANCE": "0.5"}
        s = settings.load(self.write({"cockroach_size": 90}), env=env)
        self.assertEqual(s["modes"], ["all", "highlights"])
        self.assertEqual(s["cockroach_size"], 80)
        self.assertIs(s["auto_subtitles"], False)
        self.assertEqual(s["line_opacity"], 0.2)
        self.assertIs(s["posting"]["enabled"], True)
        self.assertEqual(s["posting"]["times"], ["09:00", "10:30"])
        self.assertEqual(s["flip_every"], "-1")
        self.assertEqual(s["cockroach_chance"], 0.5)   # the default is int 1, but a fraction must still work

    def test_bad_env_value_ignored(self):
        s = settings.load(self.write({}), env={"VIDEO_QUALITY": "high"})
        self.assertEqual(s["video_quality"], 23)

    def test_apply_to_env_keeps_existing(self):
        env = {"MODES": "all"}
        settings.apply_to_env(settings.load(self.write({"line": False}), env=env), env=env)
        self.assertEqual(env["MODES"], "all")
        self.assertEqual(env["LINE"], "off")
        self.assertEqual(env["POSTING_PER_DAY"], "3")
        self.assertEqual(env["FLIP_EVERY"], "25-30")

    def test_text_instead_of_file(self):
        s = settings.load(text='{"posting": {"enabled": true, "per_day": 5}}', env={})
        self.assertTrue(s["posting"]["enabled"])
        self.assertEqual(s["posting"]["per_day"], 5)
        self.assertEqual(settings.load(text="broken", env={})["video_quality"], 23)

    def test_process_reads_settings_and_env_wins(self):
        p = self.write({"modes": ["all"], "cockroach_size": 99, "video_quality": 27, "flip_every": "10-12"})
        env = {k: v for k, v in os.environ.items() if k not in ("MODES", "COCKROACH_SIZE", "VIDEO_QUALITY", "FLIP_EVERY", "LINE_THICKNESS")}
        env.update(SETTINGS_FILE=str(p), STATUS="off", LINE_THICKNESS="3")
        code = ("import process as p; print(p.MODES, p.COCKROACH_SIZE, p.VIDEO_QUALITY, p.FLIP_MIN, p.FLIP_MAX,"
                " p.LINE_THICKNESS)")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["['all']", "99", "27", "10.0", "12.0", "3"])
