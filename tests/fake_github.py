"""
A pretend GitHub (API + uploads + asset downloads) on 127.0.0.1, for tests. Implements just the
endpoints ghapi.py uses, with GitHub's behaviour where it matters:
  * asset downloads answer 302 to a different host ("localhost"), which must NOT get the token;
  * contents PUT needs the current sha (409 if stale, 422 if missing);
  * PUT to a missing branch is 404; /git/trees + /git/commits + /git/refs make an orphan branch.
Hooks for tests: fail_upload_names (fail these uploads once), conflict_puts (another writer saves
first, N times), on_put(path, text) callback.
"""
import base64
import hashlib
import http.server
import json
import threading
import urllib.parse
from datetime import datetime, timezone

REPO = "o/r"


class FakeGitHub:
    def __init__(self):
        self.lock = threading.RLock()
        self.releases, self.assets, self.files = {}, {}, {}
        self.branches = {"main"}
        self.next_id = 100
        self.dispatches, self.runs = [], []
        self.fail_upload_names = set()
        self.conflict_puts = 0
        self.conflict_writer = None      # fn(text) -> text another writer saves when a conflict is injected
        self.requests = []
        self.auth_seen_on_download = []
        self.trees, self.commits = {}, {}
        self.blobs, self.bin_branches = {}, {}      # binary files (previews): blob sha -> bytes; branch -> {path: sha}
        handler = self._handler()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_port
        self.api = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ---- helpers for tests
    def put_file(self, path, text, branch="main"):
        with self.lock:
            self.branches.add(branch)
            self.files[(branch, path)] = (text, hashlib.sha1(text.encode()).hexdigest())

    def file(self, path, branch="status"):
        f = self.files.get((branch, path))
        return f[0] if f else None

    def json_file(self, path, branch="status"):
        t = self.file(path, branch)
        return json.loads(t) if t else None

    def release_by_tag(self, tag):
        return next((r for r in self.releases.values() if r["tag_name"] == tag), None)

    def _id(self):
        self.next_id += 1
        return self.next_id

    def _release_json(self, r):
        out = {k: v for k, v in r.items() if k != "asset_ids"}
        out["assets"] = [self._asset_json(self.assets[a]) for a in r["asset_ids"]]
        return out

    def _asset_json(self, a):
        return {k: v for k, v in a.items() if k != "data"}

    # ---- the HTTP side
    def _handler(self):
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def reply(self, status, obj=None, raw=None, headers=None):
                data = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                self.route("GET")

            def do_POST(self):
                self.route("POST")

            def do_PUT(self):
                self.route("PUT")

            def do_PATCH(self):
                self.route("PATCH")

            def do_DELETE(self):
                self.route("DELETE")

            def route(self, method):
                u = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(u.query)
                path = u.path
                fake.requests.append((method, path))
                with fake.lock:
                    try:
                        return self._route(method, path, q)
                    except KeyError:
                        return self.reply(404, {"message": "Not Found"})

            def _route(self, method, path, q):
                if path.startswith("/dl/"):                        # the "storage host" for downloads
                    fake.auth_seen_on_download.append(self.headers.get("Authorization"))
                    a = fake.assets[int(path.split("/")[2])]
                    return self.reply(200, raw=a["data"], headers={"Content-Type": "application/octet-stream"})
                up = f"/uploads/repos/{REPO}/releases/"
                if path.startswith(up) and method == "POST":
                    rid = int(path[len(up):].split("/")[0])
                    name = q["name"][0]
                    data = self.body()
                    r = fake.releases[rid]
                    if name in fake.fail_upload_names:
                        fake.fail_upload_names.discard(name)
                        return self.reply(502, {"message": "Bad gateway (test)"})
                    if any(fake.assets[a]["name"] == name for a in r["asset_ids"]):
                        return self.reply(422, {"message": "already_exists"})
                    aid = fake._id()
                    fake.assets[aid] = {"id": aid, "name": name, "size": len(data), "state": "uploaded",
                                        "data": data, "release": rid}
                    r["asset_ids"].append(aid)
                    return self.reply(201, fake._asset_json(fake.assets[aid]))
                base = f"/repos/{REPO}"
                if not path.startswith(base):
                    return self.reply(404, {"message": "Not Found"})
                p = path[len(base):]
                if p == "" and method == "GET":
                    return self.reply(200, {"full_name": REPO, "permissions": {"push": True}})
                # releases
                if p == "/releases" and method == "GET":
                    items = sorted(fake.releases.values(), key=lambda r: -r["id"])
                    page, per = int(q.get("page", ["1"])[0]), int(q.get("per_page", ["30"])[0])
                    return self.reply(200, [fake._release_json(r) for r in items[(page - 1) * per:page * per]])
                if p == "/releases" and method == "POST":
                    b = json.loads(self.body())
                    rid = fake._id()
                    fake.releases[rid] = {"id": rid, "tag_name": b["tag_name"], "name": b.get("name"),
                                          "body": b.get("body", ""), "draft": b.get("draft", False),
                                          "created_at": datetime.now(timezone.utc).isoformat(),
                                          "upload_url": f"{fake.api}/uploads/repos/{REPO}/releases/{rid}/assets{{?name,label}}",
                                          "asset_ids": []}
                    return self.reply(201, fake._release_json(fake.releases[rid]))
                parts = p.strip("/").split("/")
                if parts[0] == "releases" and parts[1] == "assets":
                    a = fake.assets[int(parts[2])]
                    if method == "GET":
                        if "octet-stream" in (self.headers.get("Accept") or ""):
                            return self.reply(302, headers={"Location": f"http://localhost:{fake.port}/dl/{a['id']}"})
                        return self.reply(200, fake._asset_json(a))
                    if method == "DELETE":
                        fake.releases[a["release"]]["asset_ids"].remove(a["id"])
                        del fake.assets[a["id"]]
                        return self.reply(204)
                if parts[0] == "releases" and len(parts) >= 2:
                    rid = int(parts[1])
                    r = fake.releases[rid]
                    if len(parts) == 3 and parts[2] == "assets":
                        page, per = int(q.get("page", ["1"])[0]), int(q.get("per_page", ["30"])[0])
                        items = [fake._asset_json(fake.assets[a]) for a in r["asset_ids"]]
                        return self.reply(200, items[(page - 1) * per:page * per])
                    if method == "PATCH":
                        r.update({k: v for k, v in json.loads(self.body()).items() if k in ("body", "name", "draft")})
                        return self.reply(200, fake._release_json(r))
                    if method == "DELETE":
                        for a in list(r["asset_ids"]):
                            del fake.assets[a]
                        del fake.releases[rid]
                        return self.reply(204)
                    if method == "GET":
                        return self.reply(200, fake._release_json(r))
                # files
                if parts[0] == "contents":
                    fpath = urllib.parse.unquote("/".join(parts[1:]))
                    if method == "GET":
                        branch = q.get("ref", ["main"])[0]
                        if fpath in fake.bin_branches.get(branch, {}):
                            data = fake.blobs[fake.bin_branches[branch][fpath]]
                            fake.files[(branch, fpath)] = (data.decode("utf-8", "replace"), fake.bin_branches[branch][fpath])
                        f = fake.files.get((branch, fpath))
                        if not f:
                            return self.reply(404, {"message": "Not Found"})
                        if "raw" in (self.headers.get("Accept") or ""):
                            return self.reply(200, raw=f[0].encode())
                        return self.reply(200, {"sha": f[1], "size": len(f[0].encode()),
                                                "content": base64.b64encode(f[0].encode()).decode()})
                    if method == "PUT":
                        b = json.loads(self.body())
                        branch = b.get("branch", "main")
                        if branch not in fake.branches:
                            return self.reply(404, {"message": "Branch not found"})
                        cur = fake.files.get((branch, fpath))
                        if fake.conflict_puts and cur:
                            fake.conflict_puts -= 1
                            other = fake.conflict_writer(cur[0]) if fake.conflict_writer else cur[0] + " "
                            fake.put_file(fpath, other, branch)
                            return self.reply(409, {"message": "sha does not match"})
                        if cur and not b.get("sha"):
                            return self.reply(422, {"message": "sha wasn't supplied"})
                        if cur and b["sha"] != cur[1]:
                            return self.reply(409, {"message": "sha does not match"})
                        text = base64.b64decode(b["content"]).decode()
                        fake.put_file(fpath, text, branch)
                        return self.reply(200 if cur else 201, {"content": {"sha": fake.files[(branch, fpath)][1]}})
                if parts[:2] == ["git", "blobs"] and method == "POST":
                    b = json.loads(self.body())
                    sha = f"blob{fake._id()}"
                    fake.blobs[sha] = base64.b64decode(b["content"])
                    return self.reply(201, {"sha": sha})
                if parts[:2] == ["git", "trees"] and method == "GET":
                    branch = urllib.parse.unquote(parts[2])
                    if branch not in fake.bin_branches:
                        return self.reply(404, {"message": "Not Found"})
                    return self.reply(200, {"tree": [{"path": k, "type": "blob", "sha": v}
                                                     for k, v in fake.bin_branches[branch].items()]})
                if parts[:3] == ["git", "refs", "heads"] and method == "PATCH":
                    branch = urllib.parse.unquote("/".join(parts[3:]))
                    if branch not in fake.bin_branches:
                        return self.reply(422, {"message": "Reference does not exist"})
                    b = json.loads(self.body())
                    fake.bin_branches[branch] = {i["path"]: i["sha"] for i in fake.trees[fake.commits[b["sha"]]["tree"]]}
                    return self.reply(200, {})
                if parts[0] == "branches":
                    return self.reply(200 if parts[1] in fake.branches else 404, {})
                if parts[:2] == ["git", "trees"]:
                    b = json.loads(self.body())
                    sha = f"tree{fake._id()}"
                    fake.trees[sha] = b["tree"]
                    return self.reply(201, {"sha": sha})
                if parts[:2] == ["git", "commits"]:
                    b = json.loads(self.body())
                    sha = f"commit{fake._id()}"
                    fake.commits[sha] = b
                    return self.reply(201, {"sha": sha})
                if parts[:2] == ["git", "refs"] and method == "POST":
                    b = json.loads(self.body())
                    branch = b["ref"].split("refs/heads/")[1]
                    if branch in fake.branches:
                        return self.reply(422, {"message": "Reference already exists"})
                    fake.branches.add(branch)
                    items = fake.trees[fake.commits[b["sha"]]["tree"]]
                    if items and "sha" in items[0]:
                        fake.bin_branches[branch] = {i["path"]: i["sha"] for i in items}
                    for item in items:
                        if "content" in item:
                            fake.put_file(item["path"], item["content"], branch)
                    return self.reply(201, {})
                if parts[:2] == ["git", "refs"] and method == "DELETE":
                    return self.reply(422, {"message": "Reference does not exist"})
                # actions
                if parts[0] == "actions" and parts[-1] == "runs":
                    return self.reply(200, {"workflow_runs": fake.runs})
                if parts[0] == "actions" and parts[-1] == "dispatches":
                    fake.dispatches.append((parts[2], json.loads(self.body())))
                    return self.reply(204)
                return self.reply(404, {"message": "Not Found"})
        return H
