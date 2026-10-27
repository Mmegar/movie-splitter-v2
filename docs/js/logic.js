// Pure helpers (no DOM, no network) so they can be unit-tested under node.

export const MODE_LABELS = {
  recap: 'Movie recap', character: 'Character story', hype: 'Hype edit',
  stretch: 'Best stretch', highlights: 'Highlights', all: 'Whole movie', clips: 'Custom clips',
};
export const MODE_HINTS = {
  recap: 'A narrator tells the whole story, in parts',
  character: 'The story of one character, narrated',
  hype: 'Disrespected, answers it, then a beat-synced edit',
  stretch: 'The best few minutes, back to back',
  highlights: 'Best moments from the whole movie',
  all: 'Everything, back to back',
};
// the older clip modes, still offered as switches next to the story modes
export const OLD_MODES = ['stretch', 'highlights', 'all'];
export const ALL_MODES = ['recap', 'character', 'hype', ...OLD_MODES];

// Stepper shown on the run card. Contract stages map onto these five steps.
export const STEPS = [
  { key: 'downloading', label: 'Download' },
  { key: 'analysing', label: 'Analyse' },
  { key: 'planning', label: 'Plan' },
  { key: 'rendering', label: 'Render' },
  { key: 'uploading', label: 'Upload' },
];
const STAGE_TO_STEP = { downloading: 0, analysing: 1, subtitles: 1, planning: 2, rendering: 3, uploading: 4, posting: 4, done: 5 };
export const STAGE_TEXT = {
  idle: 'Idle', downloading: 'Downloading the movie', analysing: 'Finding the best moments',
  subtitles: 'Making subtitles', planning: 'Planning the parts', rendering: 'Making parts',
  uploading: 'Sending parts back', posting: 'Posting to TikTok', done: 'Finished', failed: 'Failed',
};
/** How far a cutting run is, 0-100, from its stage and part (for the laptop's progress bar). */
export function cutPercent(run) {
  if (!run || !run.active) return 0;
  const before = { downloading: 2, analysing: 5, subtitles: 12, planning: 25 };
  if (run.stage in before) return before[run.stage];
  if ((run.stage === 'rendering' || run.stage === 'uploading') && run.parts_total) {
    return Math.min(99, Math.round(30 + 70 * (Math.max(1, run.part || 1) - 1) / run.parts_total));
  }
  return run.stage === 'done' ? 100 : 30;
}

export function stepIndex(stage) {
  return STAGE_TO_STEP[stage] ?? -1;
}

// ---------- formatting ----------
export function timecode(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`;
}

export function shortDuration(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return r ? `${m}m ${r}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function elapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`;
}

export function relTime(t, now = Date.now()) {
  const d = now - t;
  const future = d < 0;
  const a = Math.abs(d);
  const m = Math.round(a / 60000);
  let s;
  if (a < 45000) return future ? 'in a moment' : 'just now';
  if (m < 60) s = `${m} min`;
  else if (m < 60 * 24) s = `${Math.round(m / 60)} h`;
  else s = `${Math.round(m / 1440)} d`;
  return future ? `in ${s}` : `${s} ago`;
}

export function startOfDay(t) {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function dayLabel(t, now = Date.now()) {
  const diff = Math.round((startOfDay(now) - startOfDay(t)) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  const d = new Date(t);
  if (diff < 7) return d.toLocaleDateString(undefined, { weekday: 'long' });
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

export function clock(t) {
  return new Date(t).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export function fileNameOf(path) {
  return String(path || '').split('/').pop();
}

export function driveSearchUrl(q) {
  return `https://drive.google.com/drive/search?q=${encodeURIComponent(q)}`;
}

// ---------- demo time shifting ----------
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;
/** Returns a deep copy with every ISO timestamp moved by deltaMs. */
export function shiftTimes(value, deltaMs) {
  if (typeof value === 'string') return ISO_RE.test(value) ? new Date(Date.parse(value) + deltaMs).toISOString() : value;
  if (Array.isArray(value)) return value.map((v) => shiftTimes(v, deltaMs));
  if (value && typeof value === 'object') {
    const o = {};
    for (const [k, v] of Object.entries(value)) o[k] = shiftTimes(v, deltaMs);
    return o;
  }
  return value;
}

// ---------- wish list ----------
/** Keeps comments/blank lines where they are; titles become editable items. */
export function parseWishlist(text) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines.map((raw) => {
    const t = raw.trim();
    if (!t) return { kind: 'blank', raw };
    if (t.startsWith('#')) return { kind: 'comment', raw };
    return { kind: 'title', raw: t, title: t };
  });
}

export function serializeWishlist(lines) {
  return lines.map((l) => (l.kind === 'title' ? l.title : l.raw)).join('\n') + '\n';
}

export function wishTitles(lines) {
  return lines.filter((l) => l.kind === 'title').map((l) => l.title);
}

export function formatWishTitle(title, year) {
  const t = String(title || '').replace(/\s+/g, ' ').trim().replace(/^#+\s*/, '');
  if (!t) return '';
  const y = String(year || '').trim();
  if (/^\d{4}$/.test(y) && !/\(\d{4}\)\s*$/.test(t)) return `${t} (${y})`;
  return t;
}

export function titleKey(title, keepYear = true) {
  let t = String(title || '').toLowerCase();
  if (!keepYear) t = t.replace(/\(\s*\d{4}\s*\)/g, '');
  return t.normalize('NFKD').replace(/[^a-z0-9]+/g, '');
}

/** Finds this title's entry in laptop.json's wishlist (exact incl. year first, then without year). */
export function wishEntryFor(title, laptopWish) {
  const list = laptopWish || [];
  const k = titleKey(title);
  const hit = list.find((w) => titleKey(w.title) === k);
  if (hit) return hit;
  const k2 = titleKey(title, false);
  return list.find((w) => titleKey(w.title, false) === k2) || null;
}

export const WISH_STATUS_TEXT = {
  waiting: 'Waiting for laptop', searching: 'Searching archive.org', found: 'Found',
  downloading: 'Downloading', uploaded: 'In To edit', not_available: 'Not available', failed: 'Failed',
};

// ---------- settings ----------
export const SETTING_LABELS = {
  modes: 'Modes & order',
  filter: 'Film look',
  filter_strength: 'Film look strength',
  character: 'Character to follow',
  narrator_voice: 'Narrator voice',
  narrator_tempo: 'Narrator speed',
  narrator_funny: 'Funny dial',
  recap_style: 'Recap style',
  narrator_intensity: 'Expressiveness',
  light_sweep: 'Light sweep',
  light_sweep_seconds: 'Light sweep speed',
  script_direction: 'Script direction',
  caption_writer: 'Captions written by',
  caption_direction: 'Caption direction',
  thumbnail_badge: 'Thumbnail badge',
  recap_part_seconds: 'Narrated part length',
  music: 'Background music',
  hype_count: 'Hype edits per movie',
  stretch_minutes: 'Best stretch length',
  stretch_from: 'Best stretch starts at',
  stretch_to: 'Best stretch ends by',
  stretch_part_seconds: 'Best stretch part length',
  part_max_extra: 'Extra time to end well',
  highlight_seconds: 'Highlight clip length',
  highlight_count: 'Number of highlights',
  part_seconds: 'Whole movie part length',
  flip: 'Flip',
  flip_every: 'Flip timing',
  auto_subtitles: 'Auto subtitles',
  whisper_model: 'Speech model',
  line: 'Line',
  line_opacity: 'Line darkness',
  line_thickness: 'Line thickness',
  cockroach: 'Cockroach',
  cockroach_chance: 'Cockroach chance',
  cockroach_size: 'Cockroach size',
  captions: 'Captions',
  caption_style: 'Caption style',
  cut_style: 'Cutting style for new movies',
  zoom_part_seconds: 'Zoomed part length',
  paused: 'Pause everything',
  pause_on_battery: 'Pause on battery',
  upload_limit_mbps: 'Upload speed limit',
  auto_update_laptop: 'Laptop updates itself',
  previews: 'Previews',
  preview_keep_movies: 'Previews kept for',
  wishlist_archive: 'archive.org search',
  video_quality: 'Video quality',
  delete_originals: 'Delete originals',
  cleanup_parts_after_days: 'Clean up old parts',
  'posting.enabled': 'Auto-posting',
  'posting.privacy': 'Post privacy',
  'posting.method': 'How to post',
  'posting.when': 'When to post',
  'posting.modes': 'Modes to post',
  'posting.per_day': 'Posts per day',
  'posting.times': 'Post times',
  'posting.timezone': 'Time zone',
  'posting.hashtags': 'Hashtags',
};

const PCT_KEYS = new Set(['stretch_from', 'stretch_to', 'line_opacity', 'cockroach_chance', 'filter_strength']);
const SEC_KEYS = new Set(['stretch_part_seconds', 'part_max_extra', 'highlight_seconds', 'part_seconds', 'recap_part_seconds']);

export function describeFlipEvery(v) {
  const s = String(v ?? '').trim();
  if (s === '0' || s === '') return 'Always flipped';
  if (s === '-1') return 'Every scene cut';
  return `Every ${s} s`;
}

export function formatSetting(path, v) {
  if (v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'On' : 'Off';
  if (path === 'modes' || path === 'posting.modes') return (v || []).map((m) => MODE_LABELS[m] || m).join(' → ') || 'None';
  if (path === 'posting.times') return (v || []).join(', ') || 'None';
  if (PCT_KEYS.has(path)) return `${Math.round(Number(v) * 100)}%`;
  if (path === 'zoom_part_seconds') return `${Math.floor(v / 60)} min${v % 60 ? ` ${v % 60} s` : ''}`;
  if (path === 'recap_style') return v === 'chatty' ? 'Chatty' : 'Fast storyteller';
  if (path === 'cut_style') return v === 'classic' ? 'Classic' : 'Zoomed + tracking';
  if (SEC_KEYS.has(path)) return `${v} s`;
  if (path === 'stretch_minutes') return `${v} min`;
  if (path === 'highlight_count') return Number(v) === 0 ? 'Automatic' : String(v);
  if (path === 'cleanup_parts_after_days') return Number(v) === 0 ? 'Never' : `After ${v} days`;
  if (path === 'flip_every') return describeFlipEvery(v);
  if (path === 'posting.privacy') return v === 'self_only' ? 'Only me' : 'Public';
  if (path === 'posting.when') return v === 'as_ready' ? 'As soon as ready' : 'At set times';
  if (path === 'posting.method') return v === 'direct' ? 'Post straight away' : 'To my TikTok drafts';
  if (path === 'delete_originals') return v === 'permanent' ? 'Delete permanently' : 'Move to Recycle Bin';
  if (path === 'line_thickness' || path === 'cockroach_size') return `${v} px`;
  if (path === 'video_quality') return `CRF ${v}`;
  if (path === 'upload_limit_mbps') return Number(v) ? `${v} Mbit/s` : 'No limit';
  if (path === 'preview_keep_movies') return `${v} movie${Number(v) === 1 ? '' : 's'}`;
  if (path === 'narrator_tempo') return `${v}x`;
  if (path === 'light_sweep_seconds') return `${v} s across`;
  if (path === 'narrator_intensity') { const n = Number(v); return n < 0.7 ? 'Calm' : n < 0.95 ? 'Lively' : n < 1.1 ? 'Very expressive' : 'Wild'; }
  if (path === 'narrator_funny') { const n = Number(v); return n < 0.25 ? 'Serious' : n < 0.6 ? 'A bit funny' : n < 0.85 ? 'Funny' : 'Full comedy'; }
  if (path === 'caption_writer') return v === 'templates' ? 'Stock lines' : 'Claude (Haiku)';
  if (path === 'script_direction' || path === 'caption_direction') return v ? (String(v).length > 40 ? `${String(v).slice(0, 40)}…` : String(v)) : 'None';
  if (path === 'caption_style') return v === 'plain' ? 'Plain' : 'Goofy';
  if (path === 'flip') return { none: 'None', mirror: 'Mirror', upsidedown: 'Upside down', both: 'Both' }[v] || String(v);
  return String(v);
}

function flatten(obj, prefix = '', out = {}) {
  for (const [k, v] of Object.entries(obj || {})) {
    const p = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, p, out);
    else out[p] = v;
  }
  return out;
}

/** Lists what changed between two settings objects, in a friendly form. */
export function diffSettings(before, after) {
  const a = flatten(before), b = flatten(after);
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])];
  const changes = [];
  for (const k of keys) {
    if (k === 'paused') continue;     // the Pause switch saves on its own
    if (JSON.stringify(a[k]) === JSON.stringify(b[k])) continue;
    changes.push({ path: k, label: SETTING_LABELS[k] || k, from: formatSetting(k, a[k]), to: formatSetting(k, b[k]) });
  }
  return changes;
}

/** Re-serialises settings in the same key order as the original file, 2-space JSON. */
export function serializeSettings(obj) {
  return JSON.stringify(obj, null, 2) + '\n';
}

export function validateSettings(s) {
  const errs = [];
  if (!Array.isArray(s.modes) || !s.modes.length) errs.push('Turn on at least one mode.');
  if (Number(s.stretch_from) >= Number(s.stretch_to)) errs.push('Best stretch must start before it ends.');
  const times = (s.posting && s.posting.times) || [];
  if (times.some((t) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(t))) errs.push('Post times must look like 18:00.');
  if (new Set(times).size !== times.length) errs.push('Two post times are the same.');
  return errs;
}

// ---------- alerts feed ----------
const TYPE_RULES = [
  [/post failed|couldn.t post/i, 'post_failed'],
  [/^posted|posted:/i, 'posted'],
  [/part \d+ of \d+ ready/i, 'part_ready'],
  [/parts? planned/i, 'planned'],
  [/finished/i, 'finished'],
  [/failed|error/i, 'failed'],
  [/^started/i, 'started'],
  [/paused/i, 'paused'],
  [/new (movie|file)/i, 'new_file'],
];
export function inferType(title) {
  for (const [re, t] of TYPE_RULES) if (re.test(title)) return t;
  return 'info';
}

function normKey(s) {
  return String(s || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

/** Caption text inside a notification ("📝 Caption:" block), if any. */
export function extractCaption(message) {
  const m = String(message || '').match(/Caption:\s*\n([\s\S]+)$/);
  return m ? m[1].trim() : null;
}

/** "[Best stretch] Name: Part 3 of 10 ready" -> the part in state.json (for its caption). */
export function findPartForTitle(title, state) {
  const m = String(title || '').match(/^\[(.+?)\]\s*(.+):\s*Part (\d+) of \d+/);
  if (!m || !state) return null;
  const [, label, name, n] = m;
  const movie = (state.movies || []).find((mv) => mv.name === name);
  if (!movie) return null;
  for (const [mode, md] of Object.entries(movie.modes || {})) {
    if ((md.label || MODE_LABELS[mode]) !== label) continue;
    const part = (md.parts || []).find((p) => p.n === Number(n));
    if (part) return { movie, mode, part };
  }
  return null;
}

/** Merges state.json events and ntfy messages, de-duplicated, newest first. */
export function mergeFeed(events, ntfy, state, windowMs = 10 * 60 * 1000) {
  const items = [];
  for (const e of events || []) {
    items.push({ at: Date.parse(e.at), type: e.type || inferType(e.title), title: e.title || '', message: e.message || '', sources: ['status'] });
  }
  for (const n of ntfy || []) {
    const title = n.title || String(n.message || '').split('\n')[0];
    items.push({ at: n.at, type: inferType(title), title, message: n.message || '', sources: ['ntfy'], id: n.id });
  }
  items.sort((a, b) => b.at - a.at);
  const kept = [];
  for (const it of items) {
    if (Number.isNaN(it.at)) continue;
    const k = normKey(it.title);
    const dup = kept.find((x) => x._k === k && Math.abs(x.at - it.at) <= windowMs);
    if (dup) {
      if (it.message.length > dup.message.length) dup.message = it.message;
      if (dup.type === 'info' && it.type !== 'info') dup.type = it.type;
      for (const s of it.sources) if (!dup.sources.includes(s)) dup.sources.push(s);
      continue;
    }
    kept.push({ ...it, _k: k });
  }
  return kept.map((it) => {
    const found = findPartForTitle(it.title, state);
    const caption = (found && found.part.caption) || extractCaption(it.message);
    const { _k, ...rest } = it;
    return { ...rest, key: `${_k}@${Math.round(it.at / 60000)}`, caption, movieId: found ? found.movie.id : null };
  });
}

export function groupByDay(items, now = Date.now()) {
  const groups = [];
  for (const it of items) {
    const label = dayLabel(it.at, now);
    let g = groups[groups.length - 1];
    if (!g || g.label !== label) groups.push((g = { label, items: [] }));
    g.items.push(it);
  }
  return groups;
}

// ---------- movies ----------
export function postCounts(movie) {
  const c = { parts: 0, posted: 0, scheduled: 0, failed: 0 };
  for (const md of Object.values(movie.modes || {})) {
    for (const p of md.parts || []) {
      c.parts++;
      const s = p.post && p.post.status === 'inbox' ? 'posted' : p.post && p.post.status;
      if (s in c) c[s]++;
    }
  }
  return c;
}

/** Best guess of the movie length for the cut map (duration is optional in state.json). */
export function movieLength(movie, stretchTo = 0.75) {
  if (movie.duration) return movie.duration;
  let maxEnd = 0;
  for (const md of Object.values(movie.modes || {})) for (const p of md.parts || []) maxEnd = Math.max(maxEnd, p.end);
  return maxEnd ? maxEnd / Math.min(0.95, Math.max(stretchTo, 0.5)) : 0;
}

// ---------- commands to the laptop (config/commands.json) ----------
/** waiting (laptop hasn't seen it yet) | working | done | failed, plus the laptop's note. */
export function commandState(cmd, laptop) {
  const lp = laptop || {};
  const st = (lp.commands || {})[cmd.id];
  if (st) return { status: st.status, note: st.note || '' };
  if ((lp.commands_done || []).includes(cmd.id)) return { status: 'done', note: '' };
  return { status: 'waiting', note: 'Waiting for the laptop (it checks every 30 seconds)' };
}

/** Commands not finished yet, newest last. */
export function pendingCommands(commands, laptop, type = null) {
  const list = (commands && commands.json && commands.json.commands) || [];
  return list.filter((c) => (!type || c.type === type) && !['done', 'failed'].includes(commandState(c, laptop).status));
}

/** Movie id like status.movie_id() in Python. */
export function movieId(name) {
  return String(name || '').toLowerCase().replace(/['.’]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'movie';
}

/** Sizes like 1.4 GB / 655 MB. */
export function sizeText(mb) {
  if (!mb && mb !== 0) return '';
  return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

/** What a part is called: "Clip 2", "Highlight 3" or "Part 4". */
export function partLabel(mode, n) {
  return `${mode === 'clips' ? 'Clip' : mode === 'highlights' ? 'Highlight' : 'Part'} ${n}`;
}
