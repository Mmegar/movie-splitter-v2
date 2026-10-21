"""
Drives FilmCraft (the video editor) with no window open, through its own command line tool
(`filmcraft-cli mcp`, which speaks MCP over stdin/stdout). Every edit is a FilmCraft command
(`command_list` shows them all); this file only sends them and reads the answers.

    with FilmCraft() as fc:
        seq = fc.run("file.newSequence", name="Edit", width=1080, height=1920, fps=30)["sequence"]
        ...
        fc.export("out.mp4")
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

TICKS = 254016000000          # FilmCraft time units per second
CLI_PATHS = [r"C:\Program Files\FilmCraft\filmcraft-cli.exe", "/Applications/FilmCraft.app/Contents/MacOS/filmcraft-cli"]


def find_cli():
    """The filmcraft-cli program (FILMCRAFT_CLI, on PATH, or the usual install folder), or None."""
    for p in [os.environ.get("FILMCRAFT_CLI"), shutil.which("filmcraft-cli"), *CLI_PATHS]:
        if p and Path(p).exists():
            return p
    return None


def ticks(seconds):
    return int(round(seconds * TICKS))


class FilmCraftError(RuntimeError):
    pass


class FilmCraft:
    """A headless FilmCraft engine. Use it in a `with` block so the engine always closes."""

    def __init__(self, cli=None, project=None, log=print):
        self.cli = cli or find_cli()
        if not self.cli:
            raise FilmCraftError("FilmCraft isn't installed (filmcraft-cli not found)")
        self.project, self.log, self.proc, self.next_id = project, log, None, 0

    def __enter__(self):
        args = [self.cli] + (["--project", str(self.project)] if self.project else []) + ["mcp"]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", bufsize=1)
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "movie-splitter", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *exc):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=20)
            except Exception:
                self.proc.kill()
            self.proc = None

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _rpc(self, method, params):
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise FilmCraftError("FilmCraft stopped unexpectedly")
            msg = json.loads(line)
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise FilmCraftError(msg["error"].get("message", str(msg["error"])))
                return msg.get("result")

    def tool(self, name, **args):
        """Calls one of FilmCraft's tools (command_run, sequence_inspect, ...); returns its JSON answer."""
        res = self._rpc("tools/call", {"name": name, "arguments": args})
        text = "".join(c.get("text", "") for c in (res or {}).get("content", []) if c.get("type") == "text")
        if (res or {}).get("isError"):
            raise FilmCraftError(f"{name}: {text}")
        try:
            return json.loads(text) if text else None
        except ValueError:
            return text

    def run(self, command, **params):
        """Runs one editor command, e.g. run("timeline.place", item=3, track="V1", seconds=0, ...)."""
        return self.tool("command_run", id=command, params=params)

    def sequence(self):
        return self.tool("sequence_inspect")

    def export(self, path, **settings):
        """Exports the active sequence to an H.264 MP4 and waits for it to finish."""
        return self.run("file.exportMedia", path=str(Path(path).resolve()), format="h264", wait=True, **settings)
