// Demo data source: bundled sample files + a simulated run so the app feels alive without setup.
import { parseNtfy } from './api.js';
import { shiftTimes, MODE_LABELS, timecode, parseWishlist, wishTitles, wishEntryFor } from './logic.js';

const HOOKS = ['Wait for it… 😳', 'He did NOT see that coming 😮', 'This scene still holds up 🔥',
  'Nobody talks about this part 👀', 'The tension here 😬', 'Best line in the whole movie 😂',
  'Watch his face 😶', 'It only gets better from here 🍿', 'Okay this is iconic ✨', 'Turn the sound on for this one 🔊'];

const LS_SETTINGS = 'ms.demo.settings';
const LS_WISH = 'ms.demo.wishlist';

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

const iso = (t) => new Date(t).toISOString();
const fakeSha = () => Math.random().toString(16).slice(2, 12);

export function createDemoSource({ base = 'demo/', fetchImpl = (...a) => fetch(...a) } = {}) {
  let d = null; // { state, laptop, ntfy, runs, settings, wishlist }
  let lastStep = 0;
  let ntfyId = 1000;

  async function getText(name) {
    const res = await fetchImpl(base + name, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`demo/${name}: ${res.status}`);
    return res.text();
  }

  async function load() {
    const [st, lp, nt, rn, se, wl] = await Promise.all(
      ['state.json', 'laptop.json', 'ntfy.jsonl', 'runs.json', 'settings.json', 'wishlist.txt'].map(getText));
    const state0 = JSON.parse(st);
    const delta = Date.now() - Date.parse(state0.updated);
    const savedSettings = lsGet(LS_SETTINGS);
    d = {
      state: shiftTimes(state0, delta),
      laptop: shiftTimes(JSON.parse(lp), delta),
      ntfy: parseNtfy(nt).map((m) => ({ ...m, at: m.at + delta })),
      runs: shiftTimes(JSON.parse(rn), delta).workflow_runs,
      settings: { json: savedSettings ? JSON.parse(savedSettings) : JSON.parse(se), sha: fakeSha() },
      wishlist: { text: lsGet(LS_WISH) ?? wl, sha: fakeSha() },
    };
    lastStep = Date.now();
  }

  function note(type, title, message, tags = []) {
    const at = Date.now();
    d.state.events.unshift({ at: iso(at), type, title, message });
    d.state.events = d.state.events.slice(0, 200);
    // ntfy copy arrives a few seconds later in reality; same moment is fine for the demo
    d.ntfy.unshift({ id: `sim${ntfyId++}`, at: at + 2000, title, message, tags, priority: 3, click: null });
  }

  function movieOf(name) { return d.state.movies.find((m) => m.name === name); }

  function addPart(movie, mode, n, total) {
    const md = movie.modes[mode];
    const prev = md.parts[md.parts.length - 1];
    const len = Math.round((75 + [0, 4.5, 9, 15.5, 22][n % 5]) * 10) / 10;
    const start = mode === 'highlights'
      ? Math.round(200 + (n - 1) * (movie.duration || 6000) / (total + 1))
      : prev ? prev.end : Math.round((movie.duration || 6000) * 0.3);
    const tags = (d.settings.json.posting && d.settings.json.posting.hashtags) || '#movie #fyp';
    const caption = `${HOOKS[(n - 1) % HOOKS.length]}\nPart ${n} 🎬 · ${n < total ? `Part ${n + 1} is up next 👀` : 'Final part 🎬'}\n${tags}`;
    const label = MODE_LABELS[mode];
    const part = {
      n, start, end: Math.round((start + len) * 10) / 10, seconds: len,
      drive_path: `Ready to post/${movie.name}/${label}/${movie.name} - Part ${String(n).padStart(2, '0')}.mp4`,
      caption, post: { status: 'none', at: null, url: null, error: null },
    };
    md.parts.push(part);
    note('part_ready', `[${label}] ${movie.name}: Part ${n} of ${total} ready`,
      `Part ${n}: ${timecode(part.start)} to ${timecode(part.end)} (${Math.round(len)}s)\nIt's in Ready to post/${movie.name}/${label}.\n\n📝 Caption:\n${caption}`,
      ['film_frames']);
  }

  function finishRun(run, movie) {
    run.active = false;
    run.stage = 'done';
    movie.status = 'done';
    movie.finished = iso(Date.now());
    const summary = Object.entries(movie.modes).map(([m, md]) => `${MODE_LABELS[m]}: ${md.parts.length} parts`).join(', ');
    note('finished', `✅ Finished: ${movie.name}`, `${summary}.\nAll in Ready to post/${movie.name}.`, ['tada']);
    const r = d.runs.find((x) => x.id === run.run_id);
    if (r) { r.status = 'completed'; r.conclusion = 'success'; }
  }

  /** One simulation step: the run moves forward, the laptop upload creeps up. */
  function step() {
    const s = d.state;
    const run = s.run;
    const movie = run && run.movie ? movieOf(run.movie) : null;
    if (run && run.active && movie) {
      const modes = Object.keys(movie.modes);
      switch (run.stage) {
        case 'downloading': run.stage = 'analysing'; break;
        case 'analysing': run.stage = 'subtitles'; movie.subtitles = 'made automatically (English)'; break;
        case 'subtitles': run.stage = 'planning'; break;
        case 'planning': {
          const md = movie.modes[run.mode];
          md.planned = md.planned || (run.mode === 'highlights' ? 6 : 8);
          run.parts_total = md.planned;
          run.part = 1;
          run.stage = 'rendering';
          note('planned', `[${MODE_LABELS[run.mode]}] ${movie.name}: ${md.planned} parts planned`,
            `The parts list is in Ready to post/${movie.name}/${MODE_LABELS[run.mode]}. Parts will appear there as they're made.`, ['scissors']);
          break;
        }
        case 'rendering': {
          addPart(movie, run.mode, run.part, run.parts_total);
          if (run.part < run.parts_total) { run.part += 1; break; }
          const next = modes[modes.indexOf(run.mode) + 1];
          if (next) { run.mode = next; run.stage = 'planning'; run.part = 0; run.parts_total = 0; }
          else run.stage = 'uploading';
          break;
        }
        case 'uploading': finishRun(run, movie); break;
        default: break;
      }
    } else if (run && run.stage === 'done') {
      s.run = { active: false, stage: 'idle' };
    }
    // laptop: heartbeat + upload progress
    const lp = d.laptop;
    lp.updated = iso(Date.now());
    if (lp.uploading) {
      lp.uploading.percent = Math.min(100, lp.uploading.percent + 6);
      if (lp.uploading.percent >= 100) {
        const file = lp.uploading.file;
        lp.uploaded.unshift({ file, at: iso(Date.now()), size_mb: 655 });
        lp.uploading = null;
        const w = lp.wishlist.find((x) => x.status === 'downloading');
        const name = w ? w.title : file;
        if (w) { w.status = 'uploaded'; w.note = null; }
        s.queue.push({ name, source: w ? 'wishlist' : 'laptop', added: iso(Date.now()), size_mb: 655 });
        note('new_file', `New movie in To edit: ${name}`, `Uploaded from ${lp.host}.`, ['inbox_tray']);
      }
    }
    for (const w of lp.wishlist) if (w.status === 'searching' && Math.random() < 0.25) {
      const pd = /\((19[0-6]\d|19[0-2]\d)\)/.test(w.title);
      w.status = pd ? 'found' : 'not_available';
      w.note = pd ? 'Public domain copy on archive.org' : 'Not public domain - add the file to your laptop folder instead';
    }
    s.updated = iso(Date.now());
  }

  function snapshot() {
    return JSON.parse(JSON.stringify({
      state: d.state, laptop: d.laptop, ntfy: d.ntfy, runs: d.runs,
      settings: d.settings, wishlist: d.wishlist, fetchedAt: Date.now(), notes: [],
    }));
  }

  return {
    kind: 'demo',
    pollMs: 4000,
    async refresh() {
      if (!d) await load();
      else if (Date.now() - lastStep >= 3500) { step(); lastStep = Date.now(); }
      return snapshot();
    },
    async saveSettings(json) {
      d.settings = { json: JSON.parse(JSON.stringify(json)), sha: fakeSha() };
      lsSet(LS_SETTINGS, JSON.stringify(json));
      return d.settings.sha;
    },
    async saveWishlist(text) {
      d.wishlist = { text, sha: fakeSha() };
      lsSet(LS_WISH, text);
      for (const t of wishTitles(parseWishlist(text))) {
        if (!wishEntryFor(t, d.laptop.wishlist)) d.laptop.wishlist.push({ title: t, status: 'searching', source_url: null, note: null });
      }
      return d.wishlist.sha;
    },
    async startRun() {
      const s = d.state;
      if (s.run && s.run.active) throw new Error('A run is already going.');
      const next = s.queue.shift();
      if (!next) throw Object.assign(new Error('Nothing is waiting in To edit. Add a movie on the laptop or the wish list first.'), { kind: 'empty' });
      const id = Date.now();
      const modes = d.settings.json.modes || ['stretch', 'highlights'];
      const mv = {
        id: next.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-$/, ''), name: next.name, status: 'processing',
        source: next.source, started: iso(id), finished: null, duration: 5400, subtitles: null, error: null,
        modes: Object.fromEntries(modes.map((m) => [m, { label: MODE_LABELS[m], planned: 0, parts: [] }])),
      };
      s.movies.unshift(mv);
      s.run = { active: true, run_id: id, started: iso(id), movie: mv.name, mode: modes[0], stage: 'downloading', part: 0, parts_total: 0 };
      d.runs.unshift({ id, name: 'Make TikTok parts', path: '.github/workflows/make-parts.yml', status: 'in_progress', conclusion: null, created_at: iso(id), html_url: '#' });
      note('started', `Started: ${mv.name}`, `Making: ${modes.map((m) => MODE_LABELS[m]).join(', ')}.`, ['clapper']);
      lastStep = Date.now();
    },
    async stopRun() {
      const s = d.state;
      if (!s.run || !s.run.active) return;
      const mv = movieOf(s.run.movie);
      if (mv) { mv.status = 'failed'; mv.finished = iso(Date.now()); mv.error = 'Stopped from the app. Parts already made are kept on the laptop.'; }
      const r = d.runs.find((x) => x.id === s.run.run_id);
      if (r) { r.status = 'completed'; r.conclusion = 'cancelled'; }
      note('info', `Run stopped: ${s.run.movie}`, 'Stopped from the app. Parts already made are kept.', ['stop_sign']);
      s.run = { active: false, stage: 'idle' };
    },
  };
}
