import { esc, icon, toast, haptic, openSheet, confirmSheet } from '../ui.js';
import { MODE_LABELS, MODE_HINTS, ALL_MODES, OLD_MODES, diffSettings, validateSettings, formatSetting, describeFlipEvery } from '../logic.js';
import { makePage, freshness, banner, skeleton, empty } from './common.js';
import { laptopGroup, togglePause } from './queue.js';

const clone = (o) => JSON.parse(JSON.stringify(o));
function getPath(o, p) { return p.split('.').reduce((a, k) => (a == null ? a : a[k]), o); }
function setPath(o, p, v) {
  const ks = p.split('.');
  let a = o;
  for (const k of ks.slice(0, -1)) { if (a[k] == null || typeof a[k] !== 'object') a[k] = {}; a = a[k]; }
  a[ks[ks.length - 1]] = v;
}

const pct = (v) => `${Math.round(Number(v) * 100)}%`;
const CLEANUP_STEPS = [0, 3, 7, 14, 30, 60, 90];
const UPLOAD_STEPS = [0, 2, 5, 10, 20, 50, 100];

function timeZones() {
  try { if (Intl.supportedValuesOf) return Intl.supportedValuesOf('timeZone'); } catch { /* old Safari */ }
  return ['Pacific/Auckland', 'Australia/Sydney', 'Asia/Phnom_Penh', 'Asia/Bangkok', 'Asia/Singapore', 'Europe/London', 'America/New_York', 'America/Los_Angeles', 'UTC'];
}

// ---------- control builders (HTML) ----------
function swRow(d, path, title, sub = '') {
  const id = `s-${path.replace(/\W/g, '-')}`;
  return `<label class="row row--ctl" for="${id}">
    <span class="row-main"><span class="row-title">${esc(title)}</span>${sub ? `<span class="row-sub">${esc(sub)}</span>` : ''}</span>
    <input type="checkbox" class="switch" role="switch" id="${id}" data-k="${path}" data-t="bool" ${getPath(d, path) ? 'checked' : ''}>
  </label>`;
}

function stepRow(d, path, title, { min = 0, max = 100, step = 1, values = null, sub = '', def = null } = {}) {
  const raw = getPath(d, path);
  const v = Number(raw == null && def != null ? def : raw);
  let dec, inc;
  if (values) { const i = values.indexOf(v); dec = i > 0 || i === -1; inc = i < values.length - 1; }
  else { dec = v > min; inc = v < max; }
  return `<div class="row row--ctl">
    <span class="row-main"><span class="row-title">${esc(title)}</span>${sub ? `<span class="row-sub">${esc(sub)}</span>` : ''}</span>
    <span class="stepper-ctl" data-step="${path}" data-min="${min}" data-max="${max}" data-inc="${step}" data-def="${def ?? ''}" ${values ? `data-values="${values.join(',')}"` : ''}>
      <button type="button" data-d="-1" aria-label="Less ${esc(title)}" ${dec ? '' : 'disabled'}>${icon('minus')}</button>
      <span class="stepper-val" aria-live="polite">${esc(formatSetting(path, v))}</span>
      <button type="button" data-d="1" aria-label="More ${esc(title)}" ${inc ? '' : 'disabled'}>${icon('plus')}</button>
    </span>
  </div>`;
}

function sliderRow(d, path, title, { min, max, step, ends = null, sub = '', fmt = null } = {}) {
  const v = Number(getPath(d, path));
  const p = ((v - min) / (max - min)) * 100;
  return `<div class="row row--stack">
    <div class="row-head"><span class="row-title">${esc(title)}</span><span class="row-val" data-out="${path}">${esc(fmt ? fmt(v) : formatSetting(path, v))}</span></div>
    <input type="range" min="${min}" max="${max}" step="${step}" value="${v}" data-k="${path}" data-t="num" style="--p:${p}%" aria-label="${esc(title)}">
    ${ends ? `<div class="range-ends"><span>${esc(ends[0])}</span><span>${esc(ends[1])}</span></div>` : ''}
    ${sub ? `<p class="note">${esc(sub)}</p>` : ''}
  </div>`;
}

function segRow(d, path, title, options, sub = '') {
  const raw = getPath(d, path);
  const v = raw === undefined || raw === null ? options[0][0] : raw;   // never saved yet: the first option is the default
  return `<div class="row row--stack">
    <div class="row-head"><span class="row-title">${esc(title)}</span></div>
    <div class="seg-sm" role="group" aria-label="${esc(title)}">${options.map(([val, label]) => `<button type="button" data-seg="${path}" data-v="${esc(val)}" aria-pressed="${String(v) === String(val)}">${esc(label)}</button>`).join('')}</div>
    ${sub ? `<p class="note">${sub}</p>` : ''}
  </div>`;
}

// Microsoft voices the narrator can use (Chatterbox copies the chosen one with its funny, intense delivery).
// Samples live in docs/voices (tools/voice_samples.py makes them on GitHub).
export const VOICES = [
  ['en-US-GuyNeural', 'Guy'], ['en-US-ChristopherNeural', 'Christopher'], ['en-US-AndrewNeural', 'Andrew'],
  ['en-US-BrianNeural', 'Brian'], ['en-US-EricNeural', 'Eric'], ['en-US-RogerNeural', 'Roger'],
  ['en-US-SteffanNeural', 'Steffan'], ['en-US-AriaNeural', 'Aria'], ['en-US-AvaNeural', 'Ava'],
  ['en-US-JennyNeural', 'Jenny'], ['en-GB-RyanNeural', 'Ryan (UK)'], ['en-GB-ThomasNeural', 'Thomas (UK)'],
  ['en-GB-SoniaNeural', 'Sonia (UK)'], ['en-AU-WilliamNeural', 'William (AU)'],
];

function voiceRow(d) {
  const v = d.narrator_voice || VOICES[0][0];
  const funny = (d.narrator_engine || 'chatterbox') === 'chatterbox';
  return `<div class="row row--stack">
    <div class="row-head"><span class="row-title">Narrator voice</span></div>
    <div class="seg-sm seg-wrap" role="group" aria-label="Narrator voice">${VOICES.map(([val, label]) => `<button type="button" data-seg="narrator_voice" data-v="${esc(val)}" aria-pressed="${v === val}">${esc(label)}</button>`).join('')}</div>
    <div class="voice-play">
      <button type="button" class="btn btn--small btn--quiet" data-voice-sample="${esc(v)}" data-voice-kind="${funny ? 'funny' : 'plain'}">${icon('play')}Play ${funny ? 'funny ' : ''}sample</button>
      ${funny ? `<button type="button" class="btn btn--small btn--quiet" data-voice-sample="${esc(v)}" data-voice-kind="plain">${icon('play')}Standard voice</button>` : ''}
    </div>
    <p class="note">Samples play at 1x here; your Narrator speed is added when parts are made.</p>
  </div>`;
}

let sampleAudio = null;
function playSample(voice, kind) {
  try { if (sampleAudio) sampleAudio.pause(); } catch { /* ignore */ }
  const tryPlay = (k) => {
    sampleAudio = new Audio(`voices/${voice}${k === 'funny' ? '-funny' : ''}.mp3`);
    sampleAudio.onerror = () => { if (k === 'funny') tryPlay('plain'); else toast('No sample for this voice yet.', { kind: 'err' }); };
    return sampleAudio.play().catch(() => { /* onerror handles a missing file */ });
  };
  tryPlay(kind);
}

const slug = (t) => `sec-${String(t).toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
function group(title, rows, foot = '') {
  return `${title ? `<h3 class="group-title" id="${slug(title)}">${esc(title)}</h3>` : ''}<div class="group">${rows.join('')}</div>${foot ? `<p class="group-foot">${foot}</p>` : ''}`;
}

/** Chips at the top of Settings that jump to a section. */
function jumpNav(html) {
  const titles = [...html.matchAll(/<h3 class="group-title" id="([^"]+)">([^<]+)<\/h3>/g)];
  if (titles.length < 4) return '';
  return `<nav class="jump" aria-label="Sections">${titles.map(([, id, t]) => `<button type="button" data-jump-to="${id}">${t}</button>`).join('')}</nav>`;
}

// ---------- the Memory tab: what the story writer remembers and follows ----------
function memoryForm(d, st) {
  const queue = ((st && st.data && st.data.laptop) || {}).queue || [];
  const per = queue.filter((f) => f.character);
  return [
    group('Who to follow', [
      `<label class="row row--stack"><span class="row-title">Main character</span>
        <input class="input" data-k="character" data-t="str" value="${esc(d.character || '')}" autocomplete="off" spellcheck="false" placeholder="Blank = the main character">
        <span class="row-sub">Character story and Hype edit follow this character, and the zoomed picture keeps them in frame. Type the name the way the movie says it.</span></label>`,
      ...per.map((f) => `<a class="row" href="#/queue"><span class="row-main"><span class="row-title clamp1">${esc(String(f.name).replace(/\.[a-z0-9]{2,4}$/i, ''))}</span><span class="row-sub">This movie only: following ${esc(f.character)}</span></span>${icon('chevR', 'row-chev')}</a>`),
    ], 'Per-movie characters are set in Queue (⋯ › Character to follow) or Make one now.'),
    group('Narrator notes', [
      `<label class="row row--stack"><span class="row-title">Script direction</span>
        <textarea class="input" rows="6" data-k="script_direction" data-t="str" maxlength="600" spellcheck="true" placeholder="e.g. roast the villain, more Gen Z slang, hype up the twist">${esc(d.script_direction || '')}</textarea>
        <span class="row-sub">Tone, running jokes, names, what to focus on. Claude reads this every time it writes a recap.</span></label>`,
      sliderRow({ narrator_funny: 0.7, ...d }, 'narrator_funny', 'Funny dial', { min: 0, max: 1, step: 0.05, ends: ['Serious', 'Full comedy'], sub: 'Used by the Chatty recap style (Settings › Story & voice). The Fast storyteller tells it straight.' }),
    ]),
    group('Caption notes', [
      `<label class="row row--stack"><span class="row-title">Caption direction</span>
        <textarea class="input" rows="4" data-k="caption_direction" data-t="str" maxlength="600" spellcheck="true" placeholder="e.g. short and mysterious, ask a question, Gen Z slang">${esc(d.caption_direction || '')}</textarea>
        <span class="row-sub">How Claude writes each part's TikTok caption.</span></label>`,
    ]),
  ].join('');
}

// ---------- the form ----------
function modesGroup(d) {
  const on = (d.modes || []).filter((m) => ALL_MODES.includes(m));
  const order = [...on, ...ALL_MODES.filter((m) => !on.includes(m))];
  const rows = order.map((m) => {
    const i = on.indexOf(m);
    return `<div class="mode-row ${i < 0 ? 'is-off' : ''}" data-mode-row="${m}">
      <span class="mode-order" aria-hidden="true">${i < 0 ? '–' : i + 1}</span>
      <span class="row-main"><span class="mode-name">${esc(MODE_LABELS[m])}</span><span class="row-sub">${esc(MODE_HINTS[m])}</span></span>
      <input type="checkbox" class="switch" role="switch" data-mode-toggle="${m}" ${i >= 0 ? 'checked' : ''} aria-label="Make ${esc(MODE_LABELS[m])}">
      <button type="button" class="grip" data-grip="${m}" aria-label="Move ${esc(MODE_LABELS[m])} up" ${i < 0 ? 'style="visibility:hidden" tabindex="-1"' : ''}>${icon('grip')}</button>
    </div>`;
  });
  return group('Modes & order', rows, 'Switch on every mode you want: each movie is cut in all of them, top to bottom. Drag the handle to change the order.');
}

function flipEveryKind(v) {
  const s = String(v ?? '').trim();
  if (s === '-1') return 'cut';
  if (s === '0' || s === '') return 'always';
  return 'timed';
}

function buildForm(d) {
  const p = d.posting || {};
  const fk = flipEveryKind(d.flip_every);
  const from = Math.round(Number(d.stretch_from) * 100), to = Math.round(Number(d.stretch_to) * 100);
  const old = (d.modes || []).some((m) => OLD_MODES.includes(m));
  const narrated = (d.modes || []).some((m) => m === 'recap' || m === 'character');
  const tz = timeZones();
  if (p.timezone && !tz.includes(p.timezone)) tz.unshift(p.timezone);
  return [
    group('Memory', [`<a class="row" href="#/memory"><span class="row-ic tone-memory">${icon('memory')}</span><span class="row-main"><span class="row-title">Character, narrator and caption notes</span><span class="row-sub">${esc(d.character ? `Following ${d.character}` : 'Following the main character')}${d.script_direction ? ' · has script notes' : ''}</span></span>${icon('chevR', 'row-chev')}</a>`]),
    modesGroup(d),

    group('Story & voice', [
      `<a class="row" href="#/memory"><span class="row-main"><span class="row-title">Character, narrator notes and Funny dial</span><span class="row-sub">${esc(d.character ? `Following ${d.character}` : 'Following the main character')} · in Memory</span></span>${icon('chevR', 'row-chev')}</a>`,
      narrated ? segRow({ ...d, recap_style: d.recap_style || 'fast' }, 'recap_style', 'Recap style', [['fast', 'Fast storyteller'], ['chatty', 'Chatty']],
        (d.recap_style || 'fast') === 'fast' ? 'Like the big recap channels: the story told straight in the third person, a quick cut every second or so, one big word on screen at a time, loud and non-stop.' : 'Talks to the viewer, jokes (Funny dial), pauses, a welcome intro and a sign-off.') : '',
      narrated ? stepRow(d, 'recap_part_seconds', 'Narrated part length', { def: 180, min: 60, max: 300, step: 15, sub: 'Every part runs 1 to 5 minutes (a little over to end on a cliffhanger): short ones are joined, long ones split' }) : '',
      narrated ? voiceRow(d) : '',
      narrated ? sliderRow({ narrator_intensity: 0.9, ...d }, 'narrator_intensity', 'Expressiveness', { min: 0.5, max: 1.2, step: 0.05, ends: ['Calm', 'Wild'], sub: 'Every voice: each line is read in its mood (tense, shocked, whispered, hyped…), and this sets how far it goes.' }) : '',
      narrated ? segRow(d, 'narrator_tempo', 'Narrator speed', [['1.2', '1.2x'], ['1', '1x'], ['1.1', '1.1x'], ['1.3', '1.3x'], ['1.5', '1.5x']]) : '',
      (d.modes || []).includes('hype') ? stepRow(d, 'hype_count', 'Hype edits per movie', { def: 1, min: 1, max: 5, step: 1 }) : '',
      swRow(d, 'music', 'Background music', 'On every video: under the narrator, full volume in hype montages. Songs come from the laptop\'s Music folder (hype / story sub-folders), kept in step with GitHub'),
    ], 'Claude writes the narration from the subtitles and the plot, so mystery twists stay hidden until the last part.'),

    group('Film look', [
      segRow(d, 'filter', 'Look', [['vivid', 'Vivid'], ['mystery', 'Mystery'], ['noir', 'Noir'], ['teal_orange', 'Teal & orange'], ['warm', 'Warm'], ['none', 'None']]),
      d.filter === 'none' ? '' : sliderRow(d, 'filter_strength', 'Strength', { min: 0.1, max: 1, step: 0.05, ends: ['Subtle', 'Strong'] }),
      swRow({ light_sweep: false, ...d }, 'light_sweep', 'Light sweep', 'A diagonal light slides across the picture once in each narrated part (off by default: bright streaks can hurt to watch)'),
      !d.light_sweep ? '' : sliderRow({ light_sweep_seconds: 3, ...d }, 'light_sweep_seconds', 'Light sweep speed', { min: 1, max: 6, step: 0.5, ends: ['Fast', 'Slow'], sub: 'The diagonal light that slides across the picture, once in each narrated part.' }),
    ], 'A colour grade, soft vignette and light grain on every part.'),

    group('Cutting style', [
      segRow({ ...d, cut_style: d.cut_style || 'zoom' }, 'cut_style', 'New movies', [['zoom', 'Zoomed + tracking'], ['classic', 'Classic']],
        (d.cut_style || 'zoom') === 'zoom' ? 'Zoomed in on the characters: the picture follows whoever is talking, else the main person in the shot (never extras in the background). Clip modes get 3–5 minute parts.' : 'The whole picture on a blurred background.'),
      (d.cut_style || 'zoom') === 'zoom' && old ? stepRow(d, 'zoom_part_seconds', 'Zoomed part length', { def: 210, min: 120, max: 420, step: 15, sub: 'Each part runs about this long, up to 1½ minutes more to end on a pause' }) : '',
    ], 'You can also pick the style for each movie in Queue, under Up next.'),

    group('Thumbnails', [
      `<label class="row row--stack"><span class="row-title">Badge text</span>
        <input class="input" data-k="thumbnail_badge" data-t="str" maxlength="40" value="${esc(d.thumbnail_badge || 'PART {n}')}" autocomplete="off" spellcheck="false" placeholder="PART {n}">
        <span class="row-sub">The red badge in the top-left of each part's thumbnail. {n} becomes the part number.</span></label>`,
    ]),

    group('Captions', [
      swRow(d, 'captions', 'Write a caption for each part', 'A hook line, part number and hashtags, ready to copy'),
      d.captions ? segRow(d, 'caption_writer', 'Written by', [['claude', 'Claude (Haiku)'], ['templates', 'Stock lines']],
        (d.caption_writer || 'claude') === 'claude' ? 'Claude writes each narrated part\'s caption along with its story. Hype edits and clips use stock lines.' : '') : '',
      d.captions && (d.caption_writer || 'claude') === 'claude' ? `<a class="row" href="#/memory"><span class="row-main"><span class="row-title">Caption direction</span><span class="row-sub">${esc(d.caption_direction ? d.caption_direction.slice(0, 80) : 'None yet')} · in Memory</span></span>${icon('chevR', 'row-chev')}</a>` : '',
      d.captions ? segRow(d, 'caption_style', 'Style', [['goofy', 'Goofy 🤪'], ['plain', 'Plain']],
        d.caption_style === 'plain' ? 'Calm hooks like "Wait for it 😳".' : 'Jokes, chaos and sometimes a line quoted from the part: "\'Run!\' and then THIS happened 😳".') : '',
    ]),


    group('TikTok', [
      swRow(d, 'posting.enabled', 'Post parts to TikTok automatically', 'The laptop posts them itself, in series order: Part 1, Part 2… of the oldest movie first'),
      `<div class="row row--ctl"><span class="row-main"><span class="row-title">TikTok login on the laptop</span><span class="row-sub">Opens TikTok's login page on the laptop's screen. Log in once; the laptop remembers it.</span></span><button type="button" class="btn btn--small btn--quiet" data-tiktok-login>Log in</button></div>`,
      `<label class="row row--stack"><span class="row-title">Your TikTok username</span>
        <input class="input" data-k="posting.username" data-t="str" value="${esc(p.username || '')}" autocapitalize="off" autocomplete="off" spellcheck="false" placeholder="@yourname">
        <span class="row-sub">Used to read your views and watch time for Insights.</span></label>`,
      ...(p.enabled ? [
        segRow({ ...d, posting: { ...p, method: p.method || 'browser' } }, 'posting.method', 'How to post', [['browser', 'Browser'], ['draft', 'Drafts (old)'], ['direct', 'API (old)']],
          (p.method || 'browser') === 'browser'
            ? 'The laptop opens TikTok Studio in its own Chrome window, uploads the part, sets the thumbnail as the cover, pastes the caption and posts it publicly.'
            : '<span class="note--warn">The old Composio API: drafts need you to tap Post, and direct posts stay private. Pick Browser instead.</span>'),
        segRow({ ...d, posting: { ...p, when: p.when || 'gap' } }, 'posting.when', 'When to post', [['gap', 'Random gap'], ['times', 'Set times'], ['as_ready', 'ASAP']],
          (p.when || 'gap') === 'gap' ? 'One part, then a random wait inside the gap below before the next one, only during your posting hours. A random gap looks human and keeps the account safe from shadowbans.'
            : p.when === 'as_ready' ? 'Each new part goes when it reaches the laptop, one every 5 minutes. Fast, but riskier for shadowbans.' : 'The next part goes at each time below.'),
        (p.when || 'gap') === 'gap' ? stepRow(d, 'posting.gap_min', 'Shortest gap (minutes)', { def: 15, min: 5, max: 120, step: 5 }) : '',
        (p.when || 'gap') === 'gap' ? stepRow(d, 'posting.gap_max', 'Longest gap (minutes)', { def: 30, min: 10, max: 240, step: 5 }) : '',
        (p.when || 'gap') === 'gap' ? `<div class="row row--stack"><div class="row-head"><span class="row-title">Posting hours</span></div>
          <div class="input-row"><label class="field"><span>From</span><input class="input" type="time" data-k="posting.active_from" data-t="str" value="${esc(p.active_from || '09:00')}"></label>
          <label class="field"><span>Until</span><input class="input" type="time" data-k="posting.active_to" data-t="str" value="${esc(p.active_to || '23:30')}"></label></div>
          <p class="note">Nothing posts at night. Times are in the time zone below.</p></div>` : '',
        `<div class="row row--stack"><div class="row-head"><span class="row-title">Modes to post</span></div>
          <div class="chips-pick" role="group" aria-label="Modes to post">${[...ALL_MODES.filter((m) => (d.modes || []).includes(m) || (p.modes || []).includes(m)), 'clips'].map((m) => {
            const onm = (p.modes || []).includes(m);
            return `<button type="button" data-pmode="${m}" aria-pressed="${onm}">${onm ? icon('check') : ''}${esc(MODE_LABELS[m])}</button>`;
          }).join('')}</div></div>`,
        p.when === 'as_ready' ? '' : stepRow(d, 'posting.per_day', 'Most posts per day', { min: 1, max: 20, step: 1 }),
        p.when !== 'times' ? '' : `<div class="row row--stack"><div class="row-head"><span class="row-title">Post times</span><span class="row-val">${(p.times || []).length} a day</span></div>
          <div class="times">${(p.times || []).map((t, i) => `<span class="time-chip"><input type="time" value="${esc(t)}" data-time="${i}" aria-label="Post time ${i + 1}"><button type="button" data-time-del="${i}" aria-label="Remove ${esc(t)}">${icon('x')}</button></span>`).join('')}
            <button type="button" class="time-add" data-time-add>${icon('plus')}Add time</button></div>
        </div>`,
        `<label class="row row--stack"><span class="row-title">Time zone</span>
          <select class="input" data-k="posting.timezone" data-t="str">${tz.map((z) => `<option ${z === p.timezone ? 'selected' : ''}>${esc(z)}</option>`).join('')}</select></label>`,
        `<label class="row row--stack"><span class="row-title">Extra hashtags</span>
          <input class="input" data-k="posting.hashtags" data-t="str" value="${esc(p.hashtags || '')}" autocapitalize="off" autocomplete="off" spellcheck="false" placeholder="#movie #fyp"></label>`,
      ] : []),
      swRow({ insights_autotune: true, ...d }, 'insights_autotune', 'Let Insights tune things by itself', 'Every few hours Claude reads your views and watch time, then adjusts script and caption notes, the thumbnail badge, Funny dial and part length in small steps'),
    ], p.enabled ? 'Captions never include a music credit line.' : 'When off, parts wait in Ready to post. Copy each caption from Movies and post them yourself.'),

    !old ? '' : group('Part lengths', [
      stepRow(d, 'stretch_minutes', 'Best stretch length', { min: 2, max: 40, step: 1, sub: 'How much of the movie Best stretch covers' }),
      stepRow(d, 'stretch_part_seconds', 'Best stretch parts', { min: 30, max: 180, step: 5 }),
      stepRow(d, 'highlight_seconds', 'Highlight clips', { min: 30, max: 180, step: 5 }),
      stepRow(d, 'highlight_count', 'Number of highlights', { min: 0, max: 30, step: 1, sub: 'Automatic is about one per 10 minutes' }),
      stepRow(d, 'part_seconds', 'Whole movie parts', { min: 60, max: 300, step: 10 }),
      stepRow(d, 'part_max_extra', 'Extra time to end well', { min: 0, max: 300, step: 15, sub: 'A part may run this much longer to finish on a pause' }),
    ]),

    !(d.modes || []).includes('stretch') ? '' : group('Best stretch window', [`<div class="row row--stack">
      <div class="range2-labels"><span>Starts at <strong data-out="stretch_from">${from}%</strong></span><span>Ends by <strong data-out="stretch_to">${to}%</strong></span></div>
      <div class="range2">
        <div class="range2-track"><div class="range2-fill" data-fill style="left:${from}%;width:${to - from}%"></div></div>
        <input type="range" min="0" max="100" step="5" value="${from}" data-range2="stretch_from" aria-label="Best stretch starts at">
        <input type="range" min="0" max="100" step="5" value="${to}" data-range2="stretch_to" aria-label="Best stretch ends by">
      </div>
      <div class="range-ends"><span>Start of movie</span><span>Ending</span></div>
    </div>`], 'Best stretch only picks scenes from inside this window, so the ending is never spoiled.'),

    group('Flip', [
      segRow(d, 'flip', 'Flip the picture', [['none', 'None'], ['mirror', 'Mirror'], ['upsidedown', 'Upside down'], ['both', 'Both']]),
      d.flip === 'none' ? '' : `<div class="row row--stack">
        <div class="row-head"><span class="row-title">When</span><span class="row-val">${esc(describeFlipEvery(d.flip_every))}</span></div>
        <div class="seg-sm" role="group" aria-label="Flip timing">
          <button type="button" data-flipkind="timed" aria-pressed="${fk === 'timed'}">Every few seconds</button>
          <button type="button" data-flipkind="cut" aria-pressed="${fk === 'cut'}">Every cut</button>
          <button type="button" data-flipkind="always" aria-pressed="${fk === 'always'}">Always</button>
        </div>
        ${fk === 'timed' ? `<label class="field"><span>Seconds between flips: one number, or a range like 25-30 for a random gap</span>
          <input class="input" data-k="flip_every" data-t="flipev" value="${esc(d.flip_every)}" inputmode="text" autocomplete="off" pattern="\\d+(-\\d+)?"></label>` : ''}
        <p class="note">${fk === 'always' ? 'Every part is flipped the whole time.' : 'The picture switches between normal and flipped only on a scene cut, so it never jumps mid-shot.'}</p>
      </div>`,
    ]),

    group('Subtitles', [
      swRow(d, 'auto_subtitles', 'Make subtitles automatically', 'Used when the movie has no subtitle file'),
      d.auto_subtitles ? segRow(d, 'whisper_model', 'Speech model', [['tiny', 'Tiny'], ['base', 'Base'], ['small', 'Small'], ['medium', 'Medium']], 'Bigger models hear more accurately but make each run slower.') : '',
    ]),

    group('Line', [
      swRow(d, 'line', 'Thin line across clip videos', 'Only on clips (Best stretch, Highlights, Whole movie, custom clips), never on narrated recaps'),
      d.line ? sliderRow(d, 'line_opacity', 'Darkness', { min: 0.1, max: 1, step: 0.05, ends: ['Faint', 'Solid black'] }) : '',
      d.line ? stepRow(d, 'line_thickness', 'Thickness', { min: 1, max: 12, step: 1 }) : '',
    ]),

    group('Cockroach', [
      swRow(d, 'cockroach', 'Cockroach runs across', 'Peeks in from the side, then runs off the top'),
      d.cockroach ? sliderRow(d, 'cockroach_chance', 'Chance per part', { min: 0, max: 1, step: 0.05, ends: ['Never', 'Every part'] }) : '',
      d.cockroach ? stepRow(d, 'cockroach_size', 'Size', { min: 30, max: 160, step: 10, sub: 'The screen is 1080 px wide' }) : '',
    ]),

    group('Laptop & internet', [
      swRow(d, 'pause_on_battery', 'Pause when on battery', 'The laptop waits until it\'s plugged in before sending or downloading'),
      stepRow(d, 'upload_limit_mbps', 'Upload speed limit', { values: UPLOAD_STEPS, sub: 'Keeps some internet free for you while a movie is sent' }),
      swRow(d, 'auto_update_laptop', 'Laptop helper updates itself', 'Gets new versions from GitHub by itself, so you never copy files again'),
    ], 'The big Pause switch is on Dashboard and Queue: it stops everything straight away.'),

    group('Previews', [
      swRow(d, 'previews', 'Make previews', 'A small locked copy of every part, so you can watch it here and pick scenes for clips'),
      d.previews ? stepRow(d, 'preview_keep_movies', 'Keep previews for', { min: 1, max: 10, step: 1, sub: 'The newest movies keep theirs; older ones are removed' }) : '',
    ], d.previews ? 'Locked with your ntfy topic, so only this app can play them. Needs the NTFY_TOPIC secret on GitHub.' : ''),

    group('Storage', [
      sliderRow(d, 'video_quality', 'Video quality', { min: 18, max: 28, step: 1, ends: ['Best, big files', 'Small files'], fmt: (v) => (v <= 20 ? `High (CRF ${v})` : v <= 24 ? `Balanced (CRF ${v})` : `Small (CRF ${v})`) }),
      segRow(d, 'delete_originals', 'After a movie is done, the original', [['permanent', 'Is deleted'], ['bin', 'Goes to the bin']],
        d.delete_originals === 'bin' ? 'When the laptop deletes an original, it goes to the Recycle Bin.' : 'When the laptop deletes an original, it is gone for good (frees disk space right away).'),
      stepRow(d, 'cleanup_parts_after_days', 'Delete old parts', { values: CLEANUP_STEPS, sub: 'Finished parts older than this are removed from the laptop\'s Ready to post folder' }),
    ]),

  ].join('');
}

// ---------- connection page ----------
function connForm(c) {
  return `
    ${group('', [swRow({ demo: c.demo }, 'demo', 'Demo mode', 'Sample movies and a pretend run. Nothing is sent anywhere.')])}
    <h3 class="group-title">GitHub</h3>
    <div class="group"><div class="row row--stack">
      <label class="field"><span>Owner (your GitHub user name)</span><input class="input" name="owner" value="${esc(c.owner)}" autocapitalize="off" autocomplete="off" spellcheck="false" placeholder="kimmeng"></label>
      <label class="field"><span>Repository</span><input class="input" name="repo" value="${esc(c.repo)}" autocapitalize="off" autocomplete="off" spellcheck="false" placeholder="movie-splitter-v2"></label>
      <label class="field"><span>Token</span>
        <span class="input-row" style="gap:8px"><input class="input" name="token" type="password" value="${esc(c.token)}" autocapitalize="off" autocomplete="off" spellcheck="false" placeholder="github_pat_…"><button type="button" class="btn btn--small btn--quiet" data-reveal>Show</button></span></label>
    </div></div>
    <p class="group-foot">Make a fine-grained token at github.com → Settings → Developer settings → Fine-grained tokens. Repository access: only this repo. Permissions: Contents read and write, Actions read and write. The token stays on this phone only.</p>
    <h3 class="group-title">Notifications</h3>
    <div class="group"><div class="row row--stack">
      <label class="field"><span>ntfy topic (the same name as in the ntfy app)</span><input class="input" name="topic" value="${esc(c.topic)}" autocapitalize="off" autocomplete="off" spellcheck="false" placeholder="my-secret-topic"></label>
    </div></div>
    <p class="group-foot">Used to show your phone notifications in Alerts. It\'s the NTFY_TOPIC name the laptop installer showed you (also in github-secrets.txt next to the laptop helper).</p>
    <div class="sheet-actions">
      <button class="btn btn--primary btn--block" data-conn-save>Save and connect</button>
      <button class="btn btn--quiet btn--block" data-conn-test>Test connection</button>
    </div>
    <div data-conn-out></div>`;
}

/** Settings, or (memory: true) the Memory tab: the same settings file, showing what the story writer follows. */
export function createSettingsView(ctx, { memory = false } = {}) {
  const page = makePage({ title: memory ? 'Memory' : 'Settings' });
  const { content } = page;
  const bar = document.createElement('div');
  bar.className = 'save-bar';
  bar.innerHTML = '<p data-count></p><button class="btn btn--quiet" data-discard>Discard</button><button class="btn btn--primary" data-review>Review</button>';
  page.el.appendChild(bar);

  let base = null;     // settings as on GitHub
  let baseSha = null;
  let draft = null;    // what the form shows
  let lastSt = null;
  let conn = null;     // connection sub-page

  const changes = () => (base && draft ? diffSettings(base, draft) : []);

  function updateBar() {
    const n = changes().length;
    bar.classList.toggle('show', n > 0 && !conn);
    page.el.classList.toggle('has-bar', n > 0);
    bar.querySelector('[data-count]').textContent = `${n} unsaved change${n === 1 ? '' : 's'}`;
  }

  function connCard(st) {
    const c = st.conn;
    const demo = ctx.store.isDemo();
    return `<a class="group row conn-card" href="#/settings/connection">
      <span class="row-ic ${demo ? 'tone-accent' : st.error ? 'tone-bad' : 'tone-ok'}">${icon(demo ? 'frames' : 'key')}</span>
      <span class="row-main"><span class="row-title">${demo ? 'Demo mode' : esc(`${c.owner}/${c.repo}`)}</span>
        <span class="row-sub">${demo ? 'Connect your GitHub repo to see your real movies' : st.error ? esc(st.error.message) : `Connected${c.topic ? ', notifications on' : ', no ntfy topic'}`}</span></span>
      ${icon('chevR', 'row-chev')}
    </a>`;
  }

  function renderForm() {
    if (!draft) return;
    const focusKey = document.activeElement && content.contains(document.activeElement) ? document.activeElement.dataset.k : null;
    const html = memory ? memoryForm(draft, lastSt) : buildForm(draft);
    content.querySelector('[data-form]').innerHTML = (memory ? '' : jumpNav(`${html}<h3 class="group-title" id="sec-laptop">Laptop</h3>`)) + html;
    if (focusKey) { const el = content.querySelector(`[data-k="${focusKey}"]`); if (el) el.focus({ preventScroll: true }); }
    updateBar();
  }

  function musicNote(st) {
    const m = (st.data.laptop || {}).music;
    if (!m) return '';
    const n = Object.values(m).reduce((a, b) => a + b, 0);
    const kinds = Object.entries(m).map(([k, v]) => `${v} ${k}`).join(', ');
    return `<div class="group"><div class="row"><span class="row-ic ${n ? 'tone-ok' : 'tone-warn'}">${icon('play')}</span><span class="row-main"><span class="row-title">${n ? `${n} songs on the laptop` : 'No songs on the laptop'}</span><span class="row-sub">${n ? esc(kinds) : 'Put songs in the Music folder next to Ready to post (hype and story sub-folders)'}</span></span></div></div>`;
  }

  function topHtml(st) {
    return banner(st) + (memory ? memoryIntro() : connCard(st));
  }

  function bottomHtml(st) {
    return memory ? '' : laptopGroup(st) + musicNote(st);
  }

  function memoryIntro() {
    return '<p class="group-foot" style="margin:0">What Claude remembers and follows when it writes each recap: who the story is about, your notes on tone and jokes, and how captions should sound.</p>';
  }

  function render(st) {
    lastSt = st;
    page.setSub(freshness(st));
    if (conn) conn.render(st);
    if (!st.data) { content.innerHTML = banner(st) + skeleton(3); return; }
    const s = st.data.settings || {};
    if (!s.json) {
      content.innerHTML = banner(st) + (memory ? '' : connCard(st)) + empty({ icon: 'settings', title: 'No settings file yet', text: 'config/settings.json is missing from the repo. Push the v2 pipeline first.' });
      base = null; draft = null; updateBar();
      return;
    }
    const fresh = s.sha !== baseSha;
    const dirty = changes().length > 0;
    if (fresh) { base = clone(s.json); baseSha = s.sha; if (!dirty) draft = clone(s.json); }
    if (!content.querySelector('[data-form]') || (fresh && !dirty)) {
      content.innerHTML = `<div data-top>${topHtml(st)}</div><div class="content" data-form></div><div data-bottom>${bottomHtml(st)}</div>`;
      renderForm();
    } else {
      content.querySelector('[data-top]').innerHTML = topHtml(st);
      const bottom = content.querySelector('[data-bottom]');
      if (bottom) bottom.innerHTML = bottomHtml(st);
      updateBar();
    }
  }

  function set(path, value, rerender = true) {
    setPath(draft, path, value);
    if (rerender) renderForm(); else updateBar();
  }

  // ---------- events ----------
  content.addEventListener('click', async (e) => {
    if (e.target.closest('[data-retry]')) { ctx.store.refresh(); return; }
    if (e.target.closest('[data-tiktok-login]')) {
      haptic();
      try {
        await ctx.store.sendCommand({ type: 'tiktok_login' });
        toast('TikTok login is opening on the laptop. Log in there once.', { kind: 'ok', ms: 4500 });
      } catch (err) { toast(err.message || "Couldn't reach the laptop.", { kind: 'err', ms: 4500 }); }
      return;
    }
    const jt = e.target.closest('[data-jump-to]');
    if (jt) {
      const el = content.querySelector(`#${jt.dataset.jumpTo}`);
      if (el) page.scroller.scrollTo({ top: el.getBoundingClientRect().top - page.scroller.getBoundingClientRect().top + page.scroller.scrollTop - 64, behavior: 'smooth' });
      return;
    }
    const st = e.target.closest('[data-step] button');
    if (st) {
      const box = st.closest('[data-step]');
      const path = box.dataset.step;
      const dir = Number(st.dataset.d);
      const cur = getPath(draft, path);
      const v = Number(cur == null && box.dataset.def !== '' ? box.dataset.def : cur);
      let nv;
      if (box.dataset.values) {
        const vals = box.dataset.values.split(',').map(Number);
        let i = vals.indexOf(v);
        if (i < 0) i = vals.findIndex((x) => x > v) - (dir > 0 ? 1 : 0);
        nv = vals[Math.max(0, Math.min(vals.length - 1, i + dir))];
      } else {
        const inc = Number(box.dataset.inc);
        nv = Math.max(Number(box.dataset.min), Math.min(Number(box.dataset.max), Math.round((v + dir * inc) / inc) * inc));
      }
      haptic();
      set(path, nv);
      return;
    }
    const vs = e.target.closest('[data-voice-sample]');
    if (vs) { haptic(); playSample(vs.dataset.voiceSample, vs.dataset.voiceKind); return; }
    const sg = e.target.closest('[data-seg]');
    if (sg) {
      haptic();
      
      set(sg.dataset.seg, sg.dataset.v);
      return;
    }
    const fk = e.target.closest('[data-flipkind]');
    if (fk) {
      haptic();
      const k = fk.dataset.flipkind;
      const cur = String(draft.flip_every);
      set('flip_every', k === 'cut' ? '-1' : k === 'always' ? '0' : (flipEveryKind(cur) === 'timed' ? cur : (flipEveryKind(base.flip_every) === 'timed' ? String(base.flip_every) : '25-30')));
      return;
    }
    const pm = e.target.closest('[data-pmode]');
    if (pm) {
      haptic();
      const m = pm.dataset.pmode;
      const cur = draft.posting.modes || [];
      set('posting.modes', cur.includes(m) ? cur.filter((x) => x !== m) : [...ALL_MODES, 'clips'].filter((x) => x === m || cur.includes(x)));
      return;
    }
    const td = e.target.closest('[data-time-del]');
    if (td) { haptic(); const t = [...draft.posting.times]; t.splice(Number(td.dataset.timeDel), 1); set('posting.times', t); return; }
    if (e.target.closest('[data-time-add]')) {
      haptic();
      const t = [...(draft.posting.times || [])];
      const last = t.length ? t[t.length - 1] : '09:00';
      const h = (Number(last.slice(0, 2)) + 3) % 24;
      let nt = `${String(h).padStart(2, '0')}:${last.slice(3, 5) || '00'}`;
      while (t.includes(nt)) nt = `${String((Number(nt.slice(0, 2)) + 1) % 24).padStart(2, '0')}:00`;
      t.push(nt);
      t.sort();
      set('posting.times', t);
    }
  });

  content.addEventListener('input', (e) => {
    const el = e.target;
    if (el.dataset.range2) {
      const k = el.dataset.range2;
      let v = Number(el.value);
      const other = k === 'stretch_from' ? Math.round(draft.stretch_to * 100) : Math.round(draft.stretch_from * 100);
      if (k === 'stretch_from' && v > other - 10) v = other - 10;
      if (k === 'stretch_to' && v < other + 10) v = other + 10;
      el.value = v;
      set(k, Math.round(v) / 100, false);
      const f = Math.round(draft.stretch_from * 100), t = Math.round(draft.stretch_to * 100);
      const fill = content.querySelector('[data-fill]');
      fill.style.left = `${f}%`; fill.style.width = `${t - f}%`;
      content.querySelector('[data-out="stretch_from"]').textContent = `${f}%`;
      content.querySelector('[data-out="stretch_to"]').textContent = `${t}%`;
      return;
    }
    const k = el.dataset.k;
    if (!k || el.dataset.t === 'bool') return;
    if (el.type === 'range') {
      const v = Number(el.value);
      el.style.setProperty('--p', `${((v - Number(el.min)) / (Number(el.max) - Number(el.min))) * 100}%`);
      set(k, Math.round(v * 100) / 100, false);
      const out = content.querySelector(`[data-out="${k}"]`);
      if (out) out.textContent = k === 'video_quality' ? (v <= 20 ? `High (CRF ${v})` : v <= 24 ? `Balanced (CRF ${v})` : `Small (CRF ${v})`) : formatSetting(k, v);
      return;
    }
    if (el.dataset.t === 'flipev') { const s = el.value.replace(/\s+/g, ''); if (/^\d+(-\d+)?$/.test(s)) set(k, s, false); return; }
    if (el.dataset.t === 'str') set(k, el.value, false);
  });

  content.addEventListener('change', (e) => {
    const el = e.target;
    if (el.matches('[data-pause]')) { togglePause(ctx, el); return; }
    if (el.dataset.modeToggle) {
      haptic();
      const m = el.dataset.modeToggle;
      const cur = draft.modes || [];
      set('modes', el.checked ? [...cur, m] : cur.filter((x) => x !== m));
      return;
    }
    if (el.dataset.time !== undefined) {
      const t = [...draft.posting.times];
      if (el.value) t[Number(el.dataset.time)] = el.value;
      t.sort();
      set('posting.times', t);
      return;
    }
    if (el.dataset.t === 'bool') { haptic(); set(el.dataset.k, el.checked); return; }
    if (el.dataset.t === 'flipev') { el.value = draft.flip_every; renderForm(); return; }
    if (el.tagName === 'SELECT') set(el.dataset.k, el.value);
  });

  // ---------- drag to reorder modes ----------
  content.addEventListener('pointerdown', (e) => {
    const grip = e.target.closest('[data-grip]');
    if (!grip) return;
    const row = grip.closest('.mode-row');
    if (row.classList.contains('is-off')) return;
    e.preventDefault();
    const list = row.parentElement;
    let anchor = e.clientY; // moves each time the row swaps places
    let moved = false;
    row.classList.add('is-dragging');
    try { grip.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    const move2 = (ev) => {
      const dy = ev.clientY - anchor;
      if (Math.abs(dy) > 4) moved = true;
      row.style.transform = `translateY(${dy}px) scale(1.02)`;
      const rows = [...list.querySelectorAll('.mode-row:not(.is-off)')];
      const i = rows.indexOf(row);
      const h = row.offsetHeight;
      if (dy > h * 0.6 && rows[i + 1]) { rows[i + 1].after(row); anchor += h; haptic(); }
      else if (dy < -h * 0.6 && rows[i - 1]) { rows[i - 1].before(row); anchor -= h; haptic(); }
      else return;
      row.style.transform = `translateY(${ev.clientY - anchor}px) scale(1.02)`;
    };
    const up = () => {
      grip.removeEventListener('pointermove', move2);
      grip.removeEventListener('pointerup', up);
      grip.removeEventListener('pointercancel', up);
      row.classList.remove('is-dragging');
      row.style.transform = '';
      const order = [...list.querySelectorAll('.mode-row:not(.is-off)')].map((r) => r.dataset.modeRow);
      if (moved) set('modes', order);
      else {
        // A tap (no drag) moves the mode up one place: keyboard / VoiceOver friendly.
        const cur = [...draft.modes];
        const i = cur.indexOf(row.dataset.modeRow);
        if (i > 0) { [cur[i - 1], cur[i]] = [cur[i], cur[i - 1]]; haptic(); set('modes', cur); }
      }
    };
    grip.addEventListener('pointermove', move2);
    grip.addEventListener('pointerup', up);
    grip.addEventListener('pointercancel', up);
  });
  content.addEventListener('keydown', (e) => {
    const grip = e.target.closest('[data-grip]');
    if (grip && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      const cur = [...draft.modes];
      const i = cur.indexOf(grip.dataset.grip);
      if (i > 0) { [cur[i - 1], cur[i]] = [cur[i], cur[i - 1]]; set('modes', cur); }
    }
  });

  // ---------- save bar / review sheet ----------
  bar.addEventListener('click', async (e) => {
    if (e.target.closest('[data-discard]')) {
      const ok = await confirmSheet({ title: 'Discard your changes?', message: 'Settings go back to what is saved on GitHub.', confirm: 'Discard changes', destructive: true });
      if (ok) { draft = clone(base); renderForm(); toast('Changes discarded', { kind: 'info' }); }
      return;
    }
    if (e.target.closest('[data-review]')) review();
  });

  async function review() {
    const list = changes();
    const errs = validateSettings(draft);
    const demo = ctx.store.isDemo();
    const msg = `App: change ${list.map((c) => c.label).slice(0, 4).join(', ')}${list.length > 4 ? ` and ${list.length - 4} more` : ''}`;
    const ok = await openSheet({
      title: `Review ${list.length} change${list.length === 1 ? '' : 's'}`,
      body: `
        <ul class="diff">${list.map((c) => `<li><span class="d-label">${esc(c.label)}</span><span class="d-vals"><del>${esc(c.from)}</del>${icon('chevR')}<ins>${esc(c.to)}</ins></span></li>`).join('')}</ul>
        ${errs.length ? `<div class="banner">${icon('alert')}<div>${errs.map((x) => `<p>${esc(x)}</p>`).join('')}</div></div>` : ''}
        <p>${demo ? 'Demo mode: changes are kept on this phone only.' : `Saves config/settings.json in ${esc(lastSt.conn.owner)}/${esc(lastSt.conn.repo)}. The next run uses the new settings.`}</p>
        <div class="sheet-actions">
          <button class="btn btn--primary btn--block" data-ok ${errs.length ? 'disabled' : ''}>${demo ? 'Save' : 'Save to GitHub'}</button>
          <button class="btn btn--quiet btn--block" data-close>Keep editing</button>
        </div>`,
      onMount: (el, close) => el.querySelector('[data-ok]').addEventListener('click', () => close(true)),
    });
    if (!ok) return;
    const btn = bar.querySelector('[data-review]');
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
      const toSave = clone(draft);
      const live = (ctx.store.state.data.settings || {}).json || {};
      toSave.paused = !!live.paused;             // the Pause switch is saved on its own; never undo it here
      if (live.posting && toSave.posting && !list.some((c) => c.path === 'posting.when')) toSave.posting.when = live.posting.when;
      await ctx.store.saveSettings(toSave, msg);
      base = clone(toSave); draft = clone(toSave);
      baseSha = ctx.store.state.data.settings.sha;
      renderForm();
      haptic();
      toast(demo ? 'Settings saved (demo)' : 'Settings saved');
    } catch (err) {
      if (err.kind === 'conflict') {
        toast('Settings changed on GitHub meanwhile. Your edits are kept: review and save again.', { kind: 'err', ms: 5000 });
        await ctx.store.refresh({ full: true });
      } else toast(err.message || 'Could not save settings.', { kind: 'err', ms: 4500 });
    } finally {
      btn.disabled = false; btn.textContent = 'Review';
      updateBar();
    }
  }

  // ---------- connection sub-page ----------
  function openConn() {
    if (conn) return;
    const cp = makePage({ title: 'Connection', back: 'Settings' });
    cp.el.classList.add('page--pushed');
    const c = { ...ctx.store.state.conn };
    cp.content.innerHTML = connForm(c);
    const read = () => {
      const f = (n) => cp.content.querySelector(`[name="${n}"]`).value.trim();
      return { owner: f('owner'), repo: f('repo').replace(/^.*github\.com\//, '').replace(/\.git$/, ''), token: f('token'), topic: f('topic'), demo: cp.content.querySelector('[data-k="demo"]').checked };
    };
    const out = cp.content.querySelector('[data-conn-out]');
    cp.el.addEventListener('click', async (e) => {
      if (e.target.closest('[data-back]')) { history.length > 1 ? history.back() : (location.hash = '#/settings'); return; }
      const rv = e.target.closest('[data-reveal]');
      if (rv) { const inp = cp.content.querySelector('[name="token"]'); const show = inp.type === 'password'; inp.type = show ? 'text' : 'password'; rv.textContent = show ? 'Hide' : 'Show'; return; }
      if (e.target.closest('[data-conn-test]')) {
        const v = read();
        if (!v.owner || !v.repo) { out.innerHTML = `<div class="banner">${icon('alert')}<div><p>Add the owner and repository first.</p></div></div>`; return; }
        out.innerHTML = '<p class="note" style="display:flex;gap:8px;align-items:center"><span class="spinner"></span>Checking GitHub…</p>';
        try {
          const r = await ctx.store.testConnection(v);
          out.innerHTML = `<div class="banner banner--info">${icon('check')}<div><dl class="kv">
            <dt>Repo</dt><dd>${esc(r.repo)}</dd>
            <dt>Can save</dt><dd>${r.canPush ? 'Yes' : 'No: give the token Contents read and write'}</dd>
            <dt>Runs</dt><dd>${r.actions === true ? 'Yes' : esc(r.actions)}</dd>
            ${r.ntfy ? `<dt>ntfy</dt><dd>${esc(r.ntfy)}</dd>` : ''}</dl></div></div>`;
        } catch (err) {
          out.innerHTML = `<div class="banner">${icon('alert')}<div><p>${esc(err.message)}</p></div></div>`;
        }
        return;
      }
      if (e.target.closest('[data-conn-save]')) {
        const v = read();
        if (!v.demo && (!v.owner || !v.repo || !v.token)) {
          out.innerHTML = `<div class="banner">${icon('alert')}<div><p>To leave demo mode, fill in the owner, repository and token.</p></div></div>`;
          return;
        }
        haptic();
        baseSha = null; base = null; draft = null;
        content.innerHTML = '';
        ctx.store.connect(v);
        toast(v.demo ? 'Demo mode on' : `Connecting to ${v.owner}/${v.repo}`, { kind: v.demo ? 'info' : 'ok' });
        location.hash = '#/settings';
      }
    });
    ctx.edgeSwipeBack(cp.el, () => history.back());
    page.el.after(cp.el);
    conn = { page: cp, render() {} };
    updateBar();
    requestAnimationFrame(() => requestAnimationFrame(() => { cp.el.classList.add('is-in'); page.el.classList.add('is-under'); }));
  }

  function closeConn() {
    if (!conn) return;
    const el = conn.page.el;
    conn = null;
    page.el.classList.remove('is-under');
    el.classList.remove('is-in');
    setTimeout(() => el.remove(), 360);
    updateBar();
  }

  return {
    page,
    render,
    route(parts) { if (memory) return; if (parts[0] === 'connection') openConn(); else closeConn(); },
  };
}
