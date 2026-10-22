// App state: picks the data source (demo or GitHub), polls while visible, caches for offline.
import { createGitHub, fetchNtfy, ApiError, STATUS_BRANCH, PREVIEWS_BRANCH, activeMakeRun } from './api.js';
import { decryptPreview } from './crypto.js';
import { createDemoSource } from './demo.js';

const LS_CONN = 'ms.conn';
const LS_CACHE = 'ms.cache.live';
const LIVE_POLL_MS = 20000;

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* storage full or blocked */ } }

export function loadConn() {
  try {
    const c = JSON.parse(lsGet(LS_CONN) || 'null');
    if (c) return { owner: '', repo: '', token: '', topic: '', demo: true, ...c };
  } catch { /* ignore */ }
  return { owner: '', repo: '', token: '', topic: '', demo: true };
}

function createLiveSource(conn) {
  const gh = createGitHub(conn);
  let configs = null; // settings + wishlist, refreshed every few polls
  let polls = 0;

  async function loadConfigs() {
    const [settings, wish, cmds] = await Promise.all([gh.getJson('config/settings.json'), gh.getFile('config/wishlist.txt'),
      gh.getJson('config/commands.json').catch(() => null)]);
    configs = {
      settings: settings ? { json: settings.json, sha: settings.sha } : { json: null, sha: null },
      wishlist: wish ? { text: wish.text, sha: wish.sha } : { text: '', sha: null },
      commands: cmds ? { json: cmds.json && Array.isArray(cmds.json.commands) ? cmds.json : { commands: [] }, sha: cmds.sha } : { json: { commands: [] }, sha: null },
    };
  }

  return {
    kind: 'live',
    pollMs: LIVE_POLL_MS,
    async refresh({ full = false } = {}) {
      const notes = [];
      const [state, laptop] = await Promise.all([
        gh.getJson('state.json', STATUS_BRANCH), gh.getJson('laptop.json', STATUS_BRANCH),
      ]);
      if (!state) notes.push('No status yet. state.json appears after the first run with the v2 pipeline.');
      if (!laptop) notes.push("The laptop hasn't reported yet. Start the laptop agent once to see it here.");
      const [runs, ntfy] = await Promise.allSettled([
        gh.listRuns(),
        conn.topic ? fetchNtfy(conn.topic) : Promise.resolve(null),
      ]);
      if (runs.status === 'rejected') notes.push(`Runs: ${runs.reason.message}`);
      if (ntfy.status === 'rejected') notes.push(`Notifications: ${ntfy.reason.message}`);
      if (full || !configs || polls % 3 === 0) await loadConfigs();
      polls++;
      return {
        state: state ? state.json : null,
        laptop: laptop ? laptop.json : null,
        runs: runs.status === 'fulfilled' ? runs.value : undefined,
        ntfy: ntfy.status === 'fulfilled' ? ntfy.value : undefined,
        settings: configs.settings,
        wishlist: configs.wishlist,
        commands: configs.commands,
        fetchedAt: Date.now(),
        notes,
      };
    },
    async saveSettings(json, sha, message) {
      const text = JSON.stringify(json, null, 2) + '\n';
      try {
        const newSha = await gh.putFile('config/settings.json', text, sha, message);
        configs.settings = { json, sha: newSha };
        return newSha;
      } catch (e) {
        if (e.kind === 'conflict') await loadConfigs();
        throw e;
      }
    },
    async saveWishlist(text, sha, message) {
      try {
        const newSha = await gh.putFile('config/wishlist.txt', text, sha, message);
        configs.wishlist = { text, sha: newSha };
        return newSha;
      } catch (e) {
        if (e.kind === 'conflict') await loadConfigs();
        throw e;
      }
    },
    async saveCommands(json, sha, message) {
      const text = JSON.stringify(json, null, 1) + '\n';
      try {
        const newSha = await gh.putFile('config/commands.json', text, sha, message);
        configs.commands = { json, sha: newSha };
        return newSha;
      } catch (e) {
        if (e.kind === 'conflict') await loadConfigs();
        throw e;
      }
    },
    async getPreview(path) {
      let buf = await gh.getBinary(path, PREVIEWS_BRANCH);
      if (!buf) return null;
      if (buf.byteLength && new Uint8Array(buf, 0, 1)[0] === 0x7b) {   // '{': a big file stored in pieces
        const { pieces } = JSON.parse(new TextDecoder().decode(buf));
        const got = [];
        for (let i = 1; i <= pieces; i++) {
          const piece = await gh.getBinary(`${path}.${i}`, PREVIEWS_BRANCH);
          if (!piece) return null;
          got.push(new Uint8Array(piece));
        }
        const all = new Uint8Array(got.reduce((n, p) => n + p.length, 0));
        let at = 0;
        for (const p of got) { all.set(p, at); at += p.length; }
        buf = all.buffer;
      }
      return decryptPreview(buf, conn.topic);
    },
    startRun: () => gh.startRun(),
    async stopRun(state, runs) {
      const id = (state && state.run && state.run.active && state.run.run_id) || (activeMakeRun(runs) || {}).id;
      if (!id) throw new ApiError('bad', 'No run to stop.');
      await gh.cancelRun(id);
    },
    test: async () => {
      const repo = await gh.getRepo();
      const out = { repo: `${repo.full_name} (default branch ${repo.default_branch})`, canPush: !!(repo.permissions && repo.permissions.push) };
      try { await gh.listRuns(); out.actions = true; } catch (e) { out.actions = e.message; }
      if (conn.topic) {
        try { const n = await fetchNtfy(conn.topic); out.ntfy = `Working: ${n.length} recent notification${n.length === 1 ? '' : 's'}`; } catch (e) { out.ntfy = e.message; }
      }
      return out;
    },
  };
}

export function createStore() {
  const listeners = new Set();
  const st = {
    conn: loadConn(),
    data: null,       // last good snapshot
    error: null,      // ApiError from the last refresh, if it failed
    loading: false,
    offline: typeof navigator !== 'undefined' && navigator.onLine === false,
    starting: 0,      // time a run was requested (until GitHub shows it)
    stopping: 0,
  };
  let source = null;
  let timer = null;
  let inflight = null;
  let pausedUntil = 0;

  const emit = () => listeners.forEach((fn) => fn(st));

  function makeSource() {
    const c = st.conn;
    if (c.demo || !c.owner || !c.repo) return createDemoSource();
    return createLiveSource(c);
  }

  function isDemo() { return !source || source.kind === 'demo'; }

  function restoreCache() {
    if (isDemo()) return;
    try {
      const c = JSON.parse(lsGet(LS_CACHE) || 'null');
      if (c && c.key === `${st.conn.owner}/${st.conn.repo}`) st.data = { ...c.data, fromCache: true };
    } catch { /* ignore */ }
  }

  async function refresh(opts = {}) {
    if (inflight) return inflight;
    if (Date.now() < pausedUntil && !opts.force) return;
    st.loading = true;
    emit();
    inflight = (async () => {
      try {
        const snap = await source.refresh(opts);
        const prev = st.data || {};
        st.data = {
          ...snap,
          ntfy: snap.ntfy === undefined ? prev.ntfy || [] : snap.ntfy || [],
          runs: snap.runs === undefined ? prev.runs || [] : snap.runs,
        };
        st.error = null;
        st.offline = false;
        if (st.starting && (activeMakeRun(st.data.runs) || (st.data.state && st.data.state.run && st.data.state.run.active))) st.starting = 0;
        if (st.starting && Date.now() - st.starting > 5 * 60000) st.starting = 0;
        if (st.stopping && !(st.data.state && st.data.state.run && st.data.state.run.active)) st.stopping = 0;
        if (!isDemo()) lsSet(LS_CACHE, JSON.stringify({ key: `${st.conn.owner}/${st.conn.repo}`, data: st.data }));
      } catch (e) {
        st.error = e instanceof ApiError ? e : new ApiError('bad', e.message || String(e));
        if (st.error.kind === 'offline') st.offline = true;
        if (st.error.kind === 'ratelimit' && st.error.resetAt) pausedUntil = st.error.resetAt;
      } finally {
        st.loading = false;
        inflight = null;
        emit();
      }
    })();
    return inflight;
  }

  function schedule() {
    clearTimeout(timer);
    if (typeof document !== 'undefined' && document.hidden) return;
    timer = setTimeout(async () => { await refresh(); schedule(); }, source.pollMs);
  }

  function connect(conn) {
    st.conn = { ...st.conn, ...conn };
    lsSet(LS_CONN, JSON.stringify(st.conn));
    source = makeSource();
    st.data = null;
    st.error = null;
    restoreCache();
    emit();
    refresh({ full: true }).then(schedule);
  }

  return {
    get state() { return st; },
    isDemo,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    start() {
      source = makeSource();
      restoreCache();
      emit();
      refresh({ full: true }).then(schedule);
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) clearTimeout(timer);
        else refresh().then(schedule);
      });
      window.addEventListener('focus', () => { if (!inflight) refresh().then(schedule); });
      window.addEventListener('online', () => { st.offline = false; refresh().then(schedule); });
      window.addEventListener('offline', () => { st.offline = true; emit(); });
    },
    refresh: (opts) => refresh({ force: true, ...opts }),
    connect,
    async saveSettings(json, message) {
      await source.saveSettings(json, st.data.settings.sha, message);
      await refresh({ full: true, force: true });
    },
    async saveWishlist(text, message) {
      await source.saveWishlist(text, st.data.wishlist.sha, message);
      await refresh({ full: true, force: true });
    },
    /** Asks the laptop to do something (it checks every 30 s). Returns the command with its id. */
    async sendCommand(cmd) {
      const full = { id: `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`, at: new Date().toISOString(), ...cmd };
      for (let attempt = 0; attempt < 3; attempt++) {
        const cur = (st.data.commands && st.data.commands.json && st.data.commands.json.commands) || [];
        const done = new Set(((st.data.laptop || {}).commands_done) || []);
        const dayAgo = Date.now() - 24 * 3600 * 1000;
        // finished commands are dropped after a day, so the file stays small
        const keep = cur.filter((c) => !(done.has(c.id) && Date.parse(c.at || 0) < dayAgo)).slice(-120);
        try {
          await source.saveCommands({ commands: [...keep, full] }, st.data.commands && st.data.commands.sha, `App: ${cmd.type}${cmd.file ? ` ${cmd.file}` : ''}`);
          break;
        } catch (e) {
          if (e.kind === 'conflict' && attempt < 2) { await refresh({ full: true, force: true }); continue; }
          throw e;
        }
      }
      await refresh({ full: true, force: true });
      return full;
    },
    /** The big Pause switch: saved straight away (no review), the laptop and GitHub both obey it. */
    async setPaused(on) {
      for (let attempt = 0; attempt < 3; attempt++) {
        const s = st.data.settings || {};
        const json = { ...(s.json || {}), paused: !!on };
        try {
          await source.saveSettings(json, s.sha, on ? 'App: pause everything' : 'App: resume');
          if (!on && source.startRun && (json.cut_on || 'laptop') !== 'laptop') { try { await source.startRun(); } catch { /* the laptop starts it too */ } }
          break;
        } catch (e) {
          if (e.kind === 'conflict' && attempt < 2) { await refresh({ full: true, force: true }); continue; }
          throw e;
        }
      }
      await refresh({ full: true, force: true });
    },
    /** "Post each part as soon as it's ready": saved straight away, like Pause. */
    async setAsReady(on) {
      for (let attempt = 0; attempt < 3; attempt++) {
        const s = st.data.settings || {};
        const json = JSON.parse(JSON.stringify(s.json || {}));
        json.posting = { ...(json.posting || {}), when: on ? 'as_ready' : 'times' };
        if (on) json.posting.enabled = true;
        try {
          await source.saveSettings(json, s.sha, on ? 'App: post each part as soon as it is ready' : 'App: post at set times');
          break;
        } catch (e) {
          if (e.kind === 'conflict' && attempt < 2) { await refresh({ full: true, force: true }); continue; }
          throw e;
        }
      }
      await refresh({ full: true, force: true });
    },
    previewsAvailable() { return !!(source && source.getPreview); },
    async getPreview(path) {
      if (!source.getPreview) return null;
      return source.getPreview(path);
    },
    async startRun() {
      await source.startRun();
      st.starting = Date.now();
      emit();
      setTimeout(() => refresh({ force: true }), isDemo() ? 600 : 6000);
      if (!isDemo()) setTimeout(() => refresh({ force: true }), 20000);
    },
    async stopRun() {
      await source.stopRun(st.data && st.data.state, st.data && st.data.runs);
      st.stopping = Date.now();
      emit();
      setTimeout(() => refresh({ force: true }), isDemo() ? 300 : 5000);
    },
    async testConnection(conn) {
      const src = createLiveSource({ ...st.conn, ...conn });
      return src.test();
    },
  };
}
