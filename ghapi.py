"""
Small GitHub API client (Python standard library only), shared by the laptop helper and the
cutter on GitHub Actions: files on a branch, draft releases and their files ("assets") with
streamed upload/download and progress, and starting the cutter workflow.
"""
import base64
import http.client
import json
import time
import urllib.parse
import urllib.request

API = "https://api.github.com"
BLOCK = 1 << 20


class GitHubError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"GitHub answered {status}: {message}")
        self.status = status


def _connection(url, timeout):
    u = urllib.parse.urlsplit(url)
    secure = u.scheme == "https"
    host, port = u.hostname, u.port or (443 if secure else 80)
    proxy = urllib.request.getproxies().get("https" if secure else "http")
    cls = http.client.HTTPSConnection if secure else http.client.HTTPConnection
    if proxy and not urllib.request.proxy_bypass(host):
        p = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
        conn = cls(p.hostname, p.port or 8080, timeout=timeout)
        if secure:
            conn.set_tunnel(host, port)
        else:
            return conn, url                       # plain http through a proxy: full URL as the path
    else:
        conn = cls(host, port, timeout=timeout)
    return conn, (u.path or "/") + (f"?{u.query}" if u.query else "")


class GitHub:
    def __init__(self, repo, token, api_url=API, timeout=120):
        self.repo, self.token, self.api, self.timeout = repo, token, api_url.rstrip("/"), timeout

    # ---------------------------------------------------------------- plumbing
    def url(self, path):
        return path if path.startswith("http") else f"{self.api}/repos/{self.repo}{path}"

    def request(self, method, path, body=None, accept="application/vnd.github+json", headers=None,
                stream=None, length=None, progress=None, sink=None, auth=True, redirects=3):
        """
        Returns (status, headers, bytes). body: dict (sent as JSON) or bytes. stream: an iterable of
        bytes of total `length` (sent as it's produced). sink(bytes): receives a 2xx response body
        block by block instead of returning it. progress(bytes so far) for uploads and downloads.
        """
        url = self.url(path)
        h = {"Accept": accept, "User-Agent": "movie-splitter", "X-GitHub-Api-Version": "2022-11-28"}
        if auth and self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn, target = _connection(url, self.timeout)
        try:
            conn.putrequest(method, target, skip_accept_encoding=True)
            if stream is not None:
                h["Content-Length"] = str(length)
            elif body is not None or method in ("POST", "PUT", "PATCH"):
                h["Content-Length"] = str(len(body or b""))
            for k, v in h.items():
                conn.putheader(k, v)
            conn.endheaders()
            if stream is not None:
                sent = 0
                for block in stream:
                    for i in range(0, len(block), BLOCK):
                        conn.send(block[i:i + BLOCK])
                        sent += len(block[i:i + BLOCK])
                        if progress:
                            progress(sent)
            elif body:
                conn.send(body)
            r = conn.getresponse()
            if r.status in (301, 302, 303, 307, 308) and redirects:
                where = r.getheader("Location")
                r.read()
                same_host = urllib.parse.urlsplit(where).netloc == urllib.parse.urlsplit(url).netloc
                return self.request("GET", where, accept=accept, sink=sink, progress=progress,
                                    auth=auth and same_host, redirects=redirects - 1)
            if sink and 200 <= r.status < 300:
                got = 0
                for block in iter(lambda: r.read(BLOCK), b""):
                    sink(block)
                    got += len(block)
                    if progress:
                        progress(got)
                return r.status, dict(r.getheaders()), b""
            return r.status, dict(r.getheaders()), r.read()
        finally:
            conn.close()

    def call(self, method, path, body=None, ok=(200, 201, 204), **kw):
        status, _, data = self.request(method, path, body, **kw)
        if status not in ok:
            raise GitHubError(status, data[:300].decode("utf-8", "replace"))
        return json.loads(data) if data.strip() else None

    def _pages(self, path):
        out, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            items = self.call("GET", f"{path}{sep}per_page=100&page={page}") or []
            out += items
            if len(items) < 100:
                return out
            page += 1

    # ---------------------------------------------------------------- files on a branch
    def read_file(self, path, ref=None):
        """(text, sha), or (None, None) if the file (or branch) isn't there."""
        q = f"?ref={urllib.parse.quote(ref)}" if ref else ""
        status, _, data = self.request("GET", f"/contents/{urllib.parse.quote(path)}{q}")
        if status == 404:
            return None, None
        if status != 200:
            raise GitHubError(status, data[:300].decode("utf-8", "replace"))
        info = json.loads(data)
        text = base64.b64decode(info.get("content") or "").decode("utf-8")
        if not text and info.get("size"):                 # big file: fetch the raw text instead
            status, _, raw = self.request("GET", f"/contents/{urllib.parse.quote(path)}{q}",
                                          accept="application/vnd.github.raw")
            text = raw.decode("utf-8")
        return text, info.get("sha")

    def write_file(self, path, text, branch, message, sha=None):
        """(new sha, None) when saved; (None, status) when GitHub refused (409/422 = someone saved first)."""
        body = {"message": message, "branch": branch, "content": base64.b64encode(text.encode()).decode()}
        if sha:
            body["sha"] = sha
        status, _, data = self.request("PUT", f"/contents/{urllib.parse.quote(path)}", body)
        if status in (200, 201):
            return json.loads(data)["content"]["sha"], None
        if status == 404 or (status == 422 and b"branch" in data.lower()):
            if self.request("GET", f"/branches/{urllib.parse.quote(branch)}")[0] == 404:
                sha = self.make_orphan_branch(branch, path, text, message)
                if sha:
                    return sha, None
        if status in (404, 409, 422):
            return None, status
        raise GitHubError(status, data[:300].decode("utf-8", "replace"))

    def save_file(self, path, text_fn, branch, message, tries=6):
        """Saves text_fn(current text or None); if someone saved first, reads theirs and tries again."""
        for attempt in range(tries):
            current, sha = self.read_file(path, branch)
            new_sha, refused = self.write_file(path, text_fn(current), branch, message, sha)
            if new_sha:
                return new_sha
            time.sleep(min(5, 0.5 * (attempt + 1)))
        raise GitHubError(409, f"kept being refused while saving {path}")

    def make_orphan_branch(self, branch, path, text, message):
        """A brand-new branch holding just this one file (like 'git checkout --orphan')."""
        tree = self.call("POST", "/git/trees", {"tree": [{"path": path, "mode": "100644", "type": "blob",
                                                          "content": text}]})
        commit = self.call("POST", "/git/commits", {"message": message, "tree": tree["sha"], "parents": []})
        status, _, _ = self.request("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        if status == 201:
            info = self.call("GET", f"/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(branch)}")
            return info["sha"]
        return None                     # someone else made the branch a moment ago

    # ---------------------------------------------------------------- releases
    def releases(self):
        return self._pages("/releases")

    def create_release(self, tag, name, body, draft=True):
        return self.call("POST", "/releases", {"tag_name": tag, "name": name, "body": body, "draft": draft})

    def update_release(self, release_id, **fields):
        return self.call("PATCH", f"/releases/{release_id}", fields)

    def delete_release(self, release, tag=None):
        self.call("DELETE", f"/releases/{release}", ok=(204, 404))
        if tag:                                   # drafts normally have no tag yet; delete it if there is one
            self.request("DELETE", f"/git/refs/tags/{urllib.parse.quote(tag)}")

    def assets(self, release_id):
        return self._pages(f"/releases/{release_id}/assets")

    def upload_asset(self, release, name, length, stream, progress=None):
        base = release["upload_url"].split("{")[0]
        url = f"{base}?name={urllib.parse.quote(name)}"
        return self.call("POST", url, stream=stream, length=length, progress=progress, ok=(201,),
                         headers={"Content-Type": "application/octet-stream"})

    def download_asset(self, asset_id, sink, progress=None):
        status, _, data = self.request("GET", f"/releases/assets/{asset_id}", accept="application/octet-stream",
                                       sink=sink, progress=progress)
        if not 200 <= status < 300:
            raise GitHubError(status, data[:300].decode("utf-8", "replace"))

    def delete_asset(self, asset_id):
        self.call("DELETE", f"/releases/assets/{asset_id}", ok=(204, 404))

    # ---------------------------------------------------------------- the cutter workflow
    def run_active(self, workflow):
        runs = (self.call("GET", f"/actions/workflows/{workflow}/runs?per_page=5") or {}).get("workflow_runs", [])
        return any(r.get("status") != "completed" for r in runs)

    def dispatch(self, workflow, ref="main"):
        self.call("POST", f"/actions/workflows/{workflow}/dispatches", {"ref": ref}, ok=(204,))
