// App state: picks the data source (demo or GitHub), polls while visible, caches for offline.
import { createGitHub, fetchNtfy, ApiError, STATUS_BRANCH, activeMakeRun } from './api.js';
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
    const [settings, wish] = await Promise.all([gh.getJson('config/settings.json'), gh.getFile('config/wishlist.txt')]);
    configs = {
      settings: settings ? { json: settings.json, sha: settings.sha } : { json: null, sha: null },
      wishlist: wish ? { text: wish.text, sha: wish.sha } : { text: '', sha: null },
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
        try { const n = await fetchNtfy(conn.topic); out.ntfy = `${n.length} notifications in the last 7 days`; } catch (e) { out.ntfy = e.message; }
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
