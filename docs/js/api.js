// GitHub + ntfy data layer (contract §6). No DOM use, so it also runs under node for tests.

export const GITHUB_API = 'https://api.github.com';
export const NTFY_SERVER = 'https://ntfy.sh';
export const WORKFLOW_FILE = 'make-parts.yml';
export const STATUS_BRANCH = 'status';

// ---------- base64 <-> UTF-8 (emoji-safe) ----------
export function b64ToUtf8(b64) {
  const bin = atob(String(b64 || '').replace(/\s+/g, ''));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder('utf-8').decode(bytes);
}

export function utf8ToB64(str) {
  const bytes = new TextEncoder().encode(String(str));
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

// ---------- ntfy ----------
/** Parses ntfy's newline-delimited JSON. Keeps only event=="message", newest first. */
export function parseNtfy(text) {
  const out = [];
  for (const line of String(text || '').split('\n')) {
    const s = line.trim();
    if (!s) continue;
    let m;
    try { m = JSON.parse(s); } catch { continue; }
    if (!m || m.event !== 'message') continue;
    out.push({
      id: String(m.id ?? ''),
      at: Number(m.time) * 1000,
      title: m.title || '',
      message: m.message || '',
      tags: Array.isArray(m.tags) ? m.tags : [],
      priority: m.priority || 3,
      click: m.click || null,
    });
  }
  out.sort((a, b) => b.at - a.at);
  return out;
}

export function ntfyUrl(topic, server = NTFY_SERVER, since = '7d') {
  return `${server.replace(/\/$/, '')}/${encodeURIComponent(topic)}/json?poll=1&since=${since}`;
}

export async function fetchNtfy(topic, { server = NTFY_SERVER, since = '7d', fetchImpl = fetch } = {}) {
  if (!topic) return [];
  let res;
  try {
    res = await fetchImpl(ntfyUrl(topic, server, since), { cache: 'no-store' });
  } catch (e) {
    throw new ApiError('offline', "Couldn't reach ntfy. Check your connection.", 0);
  }
  if (!res.ok) throw new ApiError('ntfy', `ntfy answered ${res.status}. Check the topic name in Settings.`, res.status);
  return parseNtfy(await res.text());
}

// ---------- laptop ----------
export const LAPTOP_STALE_MS = 15 * 60 * 1000;

/** Online = agent says online AND its heartbeat is at most 15 minutes old. */
export function laptopStatus(laptop, now = Date.now()) {
  if (!laptop || !laptop.updated) return { online: false, reason: 'never', ageMs: null };
  const t = Date.parse(laptop.updated);
  if (Number.isNaN(t)) return { online: false, reason: 'never', ageMs: null };
  const ageMs = Math.max(0, now - t);
  if (laptop.online === false) return { online: false, reason: 'off', ageMs };
  if (ageMs > LAPTOP_STALE_MS) return { online: false, reason: 'stale', ageMs };
  return { online: true, reason: 'ok', ageMs };
}

// ---------- errors ----------
export class ApiError extends Error {
  constructor(kind, message, status = 0, extra = {}) {
    super(message);
    this.kind = kind; // auth | forbidden | ratelimit | notfound | conflict | offline | server | bad | ntfy | setup
    this.status = status;
    Object.assign(this, extra);
  }
}

/** Turns a failed GitHub response into a friendly ApiError. */
export function errorFromResponse(res, body, what = 'GitHub') {
  const h = (k) => (res.headers && res.headers.get ? res.headers.get(k) : null);
  const status = res.status;
  const msg = (body && body.message) || '';
  if (status === 401) return new ApiError('auth', 'GitHub rejected the token. Make a new one and paste it in Settings → Connection.', status);
  if ((status === 403 || status === 429) && (h('x-ratelimit-remaining') === '0' || /rate limit/i.test(msg))) {
    const reset = Number(h('x-ratelimit-reset')) * 1000;
    return new ApiError('ratelimit', 'GitHub rate limit reached. The app will try again automatically.', status, { resetAt: reset || null });
  }
  if (status === 403) return new ApiError('forbidden', `The token isn't allowed to do this (${what}). Give it Contents and Actions read/write access.`, status);
  if (status === 404) return new ApiError('notfound', `${what} not found. Check the owner and repo name, and that the token can see this repo.`, status);
  if (status === 409 || (status === 422 && /sha/i.test(msg))) return new ApiError('conflict', 'This file changed on GitHub since you opened it. The latest version was loaded, please review and save again.', status);
  if (status >= 500) return new ApiError('server', 'GitHub is having trouble right now. The app will try again.', status);
  return new ApiError('bad', msg ? `GitHub said: ${msg}` : `GitHub answered ${status}.`, status);
}

// ---------- GitHub client ----------
export function createGitHub({ owner, repo, token, fetchImpl = (...a) => fetch(...a) } = {}) {
  if (!owner || !repo) throw new ApiError('setup', 'Add the GitHub owner and repo in Settings → Connection.');
  const base = `${GITHUB_API}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
  let repoInfo = null;

  async function request(path, { method = 'GET', body, accept = 'application/vnd.github+json', what } = {}) {
    const headers = { Accept: accept, 'X-GitHub-Api-Version': '2022-11-28' };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetchImpl(path.startsWith('http') ? path : base + path, {
        method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, cache: 'no-store',
      });
    } catch (e) {
      throw new ApiError('offline', "Couldn't reach GitHub. You may be offline.", 0);
    }
    if (res.status === 204) return null;
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch { data = text; } }
    if (!res.ok) throw errorFromResponse(res, data, what);
    return data;
  }

  async function getRepo() {
    if (!repoInfo) repoInfo = await request('', { what: `Repo ${owner}/${repo}` });
    return repoInfo;
  }

  async function defaultBranch() {
    return (await getRepo()).default_branch || 'main';
  }

  /** Returns {text, sha} or null if the file doesn't exist. */
  async function getFile(path, ref) {
    const q = ref ? `?ref=${encodeURIComponent(ref)}` : '';
    let data;
    try {
      data = await request(`/contents/${path}${q}`, { what: path });
    } catch (e) {
      if (e.kind === 'notfound') {
        await getRepo(); // distinguishes "repo missing / no access" (throws) from "file not written yet"
        return null;
      }
      throw e;
    }
    let text;
    if (data && typeof data.content === 'string' && data.content.length) text = b64ToUtf8(data.content);
    else { // files > 1 MB come without content
      text = await request(`/contents/${path}${q}`, { accept: 'application/vnd.github.raw+json', what: path });
      if (typeof text !== 'string') text = JSON.stringify(text);
    }
    return { text, sha: data.sha };
  }

  async function getJson(path, ref) {
    const f = await getFile(path, ref);
    if (!f) return null;
    try { return { json: JSON.parse(f.text), sha: f.sha, text: f.text }; }
    catch { throw new ApiError('bad', `${path} isn't valid JSON.`); }
  }

  async function putFile(path, text, sha, message) {
    const branch = await defaultBranch();
    const body = { message, content: utf8ToB64(text), branch };
    if (sha) body.sha = sha;
    const data = await request(`/contents/${path}`, { method: 'PUT', body, what: path });
    return data && data.content ? data.content.sha : null;
  }

  async function listRuns() {
    const data = await request('/actions/runs?per_page=10', { what: 'Workflow runs' });
    return (data && data.workflow_runs) || [];
  }

  async function startRun() {
    const ref = await defaultBranch();
    await request(`/actions/workflows/${WORKFLOW_FILE}/dispatches`, { method: 'POST', body: { ref }, what: `Workflow ${WORKFLOW_FILE}` });
  }

  async function cancelRun(id) {
    await request(`/actions/runs/${id}/cancel`, { method: 'POST', what: 'That run' });
  }

  return { request, getRepo, defaultBranch, getFile, getJson, putFile, listRuns, startRun, cancelRun };
}

/** The make-parts run that is queued or in progress, if any. */
export function activeMakeRun(runs) {
  return (runs || []).find((r) => /make-parts\.yml$/.test(r.path || '') && r.status !== 'completed') || null;
}
