// Preview player and the custom-clip editor (both bottom sheets).
import { esc, icon, toast, haptic, openSheet } from '../ui.js';
import { timecode, shortDuration, movieLength, MODE_LABELS, pendingCommands, commandState } from '../logic.js';

const MAX_CLIP = 600;   // TikTok's longest upload: 10 minutes
const MIN_CLIP = 3;
const CLOSE_UP = 300;   // the "close up" timeline shows 5 minutes

// ---------- encrypted files from the previews branch, kept as object URLs for this session ----------
const cache = new Map();
async function blobUrl(store, path, type) {
  if (cache.has(path)) return cache.get(path);
  const p = (async () => {
    const buf = await store.getPreview(path);
    return buf ? URL.createObjectURL(new Blob([buf], { type })) : null;
  })();
  cache.set(path, p);
  try { return await p; } catch (e) { cache.delete(path); throw e; }
}
async function jsonFile(store, path) {
  const url = await blobUrl(store, path, 'application/json');
  if (!url) return null;
  return (await fetch(url)).json();
}
function loadImage(url) {
  return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = url; });
}

function previewError(e, demo) {
  if (demo) return 'Previews play once the app is connected to your GitHub repo (demo mode has no videos).';
  if (e && e.kind === 'locked') return e.message;
  if (e && e.kind === 'offline') return "Couldn't reach GitHub. Check your connection and try again.";
  return e ? (e.message || String(e)) : 'This preview isn\'t available. Previews are kept for the newest few movies only (Settings → Previews).';
}

// ---------- preview player ----------
export async function openPreview(ctx, movie, mode, part) {
  const label = (movie.modes[mode] && movie.modes[mode].label) || MODE_LABELS[mode] || mode;
  let video = null;
  const choice = await openSheet({
    title: `${part.title || `${mode === 'clips' ? 'Clip' : 'Part'} ${part.n}`}`,
    className: 'sheet--media',
    body: `
      <p class="media-sub">${esc(movie.name)} · ${esc(label)} · ${timecode(part.start)}–${timecode(part.end)} (${esc(shortDuration(part.seconds))})</p>
      <div class="player" data-player>
        <video playsinline controls preload="auto" disablepictureinpicture></video>
        <div class="player-msg" data-msg><span class="spinner"></span><span>Unlocking the preview…</span></div>
      </div>
      <p class="note">A small, lower-quality copy so it loads fast. The full-quality part is on your laptop.</p>
      <div class="sheet-actions">
        <button class="btn btn--accent btn--block" data-clipfrom>${icon('scissors')}Make a clip from here</button>
      </div>`,
    async onMount(el, close) {
      video = el.querySelector('video');
      const msg = el.querySelector('[data-msg]');
      el.querySelector('[data-clipfrom]').addEventListener('click', () => close({ at: video && !Number.isNaN(video.currentTime) ? video.currentTime : 0 }));
      if (!part.preview) {
        msg.innerHTML = `${icon('info')}<span>${esc(previewError(null, ctx.store.isDemo()))}</span>`;
        return;
      }
      try {
        const url = await blobUrl(ctx.store, part.preview, 'video/mp4');
        if (!url) throw null;
        video.addEventListener('error', () => {
          msg.hidden = false;
          msg.innerHTML = `${icon('alert')}<span>This browser can't play the preview. Open the app in Safari on your iPhone.</span>`;
        }, { once: true });
        video.src = url;
        msg.hidden = true;
        video.play().catch(() => { /* iOS may want a tap first */ });
      } catch (e) {
        msg.innerHTML = `${icon('alert')}<span>${esc(previewError(e, ctx.store.isDemo()))}</span>`;
      }
    },
  });
  if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
  if (choice && choice.at !== undefined) {
    const s = part.start + choice.at;
    openClipEditor(ctx, movie, { start: s, end: Math.min(s + 30, part.end + 30) });
  }
}

// ---------- clip editor ----------
export async function openClipEditor(ctx, movie, { start = null, end = null } = {}) {
  const settings = (ctx.store.state.data.settings && ctx.store.state.data.settings.json) || {};
  const len = Math.max(10, Number(movie.duration) || movieLength(movie, settings.stretch_to) || 0);
  let a = start != null ? start : Math.round(len * 0.4);
  let b = end != null ? end : a + 30;
  let zoom = 'all';
  let win = [0, len];
  let sprite = null;     // { img, every, count, cols, w, h }
  let subs = [];         // [[a, b, text]]
  const laptop = ctx.store.state.data.laptop || {};
  const knowsOriginal = (laptop.originals || []).includes(movie.id);

  function clamp() {
    a = Math.max(0, Math.min(a, len - MIN_CLIP));
    b = Math.max(a + MIN_CLIP, Math.min(b, len, a + MAX_CLIP));
    a = Math.round(a * 10) / 10; b = Math.round(b * 10) / 10;
  }
  function setWindow() {
    if (zoom === 'all') win = [0, len];
    else {
      const mid = (a + b) / 2;
      const half = Math.max(CLOSE_UP, b - a + 40) / 2;
      let w0 = Math.max(0, mid - half), w1 = Math.min(len, mid + half);
      if (w1 - w0 < 2 * half) { if (w0 === 0) w1 = Math.min(len, 2 * half); else w0 = Math.max(0, len - 2 * half); }
      win = [w0, w1];
    }
  }
  clamp(); setWindow();

  const result = await openSheet({
    title: 'Make a clip',
    className: 'sheet--media sheet--tall',
    body: `
      <p class="media-sub">${esc(movie.name)} · ${timecode(len)} long</p>
      <div class="clip-frames">
        <figure><canvas width="320" height="180" data-frame="a"></canvas><figcaption>Starts <strong data-out="a"></strong></figcaption></figure>
        <figure><canvas width="320" height="180" data-frame="b"></canvas><figcaption>Ends <strong data-out="b"></strong></figcaption></figure>
      </div>
      <p class="note" data-frames-note hidden></p>
      <div class="seg-sm" role="group" aria-label="Timeline zoom">
        <button type="button" data-zoom="all" aria-pressed="true">Whole movie</button>
        <button type="button" data-zoom="close" aria-pressed="false">Close up (5 min)</button>
      </div>
      <div class="range2 clip-range">
        <div class="range2-track"><canvas class="clip-strip" data-strip></canvas><div class="range2-fill" data-fill></div></div>
        <input type="range" step="0.5" data-handle="a" aria-label="Clip start">
        <input type="range" step="0.5" data-handle="b" aria-label="Clip end">
      </div>
      <div class="range-ends"><span data-out="w0"></span><span data-out="len" class="clip-len"></span><span data-out="w1"></span></div>
      <div class="nudges">
        <div class="nudge-row"><span>Start</span>${[-5, -1, 1, 5].map((d) => `<button type="button" class="nudge" data-nudge="a:${d}">${d > 0 ? '+' : '−'}${Math.abs(d)}s</button>`).join('')}</div>
        <div class="nudge-row"><span>End</span>${[-5, -1, 1, 5].map((d) => `<button type="button" class="nudge" data-nudge="b:${d}">${d > 0 ? '+' : '−'}${Math.abs(d)}s</button>`).join('')}</div>
      </div>
      <div data-subs></div>
      <label class="field"><span>Name (optional, goes in the file name)</span><input class="input" name="title" maxlength="60" autocomplete="off" placeholder="The chase"></label>
      ${knowsOriginal || ctx.store.isDemo() ? '' : '<p class="note">The laptop looks for the original movie in Done, To edit and your Movies folder. If it was deleted, the clip can\'t be made.</p>'}
      <p class="note">The laptop sends the movie to GitHub again (this uses your upload), then the clip is made with the same look as the parts and lands in <strong>Custom clips</strong>.</p>
      <div class="sheet-actions">
        <button class="btn btn--primary btn--block" data-make>${icon('scissors')}Make this clip</button>
      </div>`,
    async onMount(el, close) {
      const ra = el.querySelector('[data-handle="a"]'), rb = el.querySelector('[data-handle="b"]');
      const strip = el.querySelector('[data-strip]');
      const out = (k, v) => { const o = el.querySelector(`[data-out="${k}"]`); if (o) o.textContent = v; };

      function drawFrame(canvas, t) {
        const g = canvas.getContext('2d');
        g.fillStyle = '#0c0a11'; g.fillRect(0, 0, canvas.width, canvas.height);
        if (!sprite) return;
        const i = Math.max(0, Math.min(sprite.count - 1, Math.floor(t / sprite.every)));
        const sx = (i % sprite.cols) * sprite.w, sy = Math.floor(i / sprite.cols) * sprite.h;
        g.drawImage(sprite.img, sx, sy, sprite.w, sprite.h, 0, 0, canvas.width, canvas.height);
      }
      function drawStrip() {
        const w = Math.max(100, Math.round(strip.clientWidth || 300)), h = Math.round(strip.clientHeight || 32);
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        strip.width = w * dpr; strip.height = h * dpr;
        const g = strip.getContext('2d');
        g.scale(dpr, dpr);
        g.fillStyle = '#0c0a11'; g.fillRect(0, 0, w, h);
        if (!sprite) return;
        const fw = h * 16 / 9, n = Math.ceil(w / fw);
        for (let k = 0; k < n; k++) {
          const t = win[0] + ((k + 0.5) / n) * (win[1] - win[0]);
          const i = Math.max(0, Math.min(sprite.count - 1, Math.floor(t / sprite.every)));
          g.drawImage(sprite.img, (i % sprite.cols) * sprite.w, Math.floor(i / sprite.cols) * sprite.h, sprite.w, sprite.h, k * fw, 0, fw, h);
        }
        g.fillStyle = 'rgba(12,10,17,0.35)'; g.fillRect(0, 0, w, h);
      }
      function subsHtml() {
        if (!subs.length) return '';
        const near = subs.filter(([x, y]) => y >= a - 25 && x <= b + 25).slice(0, 40);
        if (!near.length) return '<p class="note">No subtitles near this spot.</p>';
        return `<div class="clip-subs"><p class="clip-subs-head">What's said here · tap Start or End to snap to a line</p>${near.map(([x, y, t]) => `
          <div class="sub-line ${y > a && x < b ? 'is-in' : ''}"><span class="sub-t">${timecode(x)}</span><span class="sub-text">${esc(t)}</span>
            <button type="button" class="nudge" data-snap="a:${x}">Start</button><button type="button" class="nudge" data-snap="b:${y}">End</button></div>`).join('')}</div>`;
      }
      function sync(redrawStrip = false) {
        for (const r of [ra, rb]) { r.min = win[0]; r.max = win[1]; }
        ra.value = a; rb.value = b;
        const span = win[1] - win[0] || 1;
        const fill = el.querySelector('[data-fill]');
        fill.style.left = `${((Math.max(a, win[0]) - win[0]) / span) * 100}%`;
        fill.style.width = `${((Math.min(b, win[1]) - Math.max(a, win[0])) / span) * 100}%`;
        out('a', timecode(a)); out('b', timecode(b));
        out('w0', timecode(win[0])); out('w1', timecode(win[1]));
        const d = b - a;
        const lenEl = el.querySelector('[data-out="len"]');
        lenEl.textContent = `Clip ${shortDuration(d)}${d >= MAX_CLIP ? ' (10 min max)' : ''}`;
        drawFrame(el.querySelector('[data-frame="a"]'), a);
        drawFrame(el.querySelector('[data-frame="b"]'), Math.max(a, b - 0.5));
        el.querySelector('[data-subs]').innerHTML = subsHtml();
        if (redrawStrip) drawStrip();
      }

      ra.addEventListener('input', () => { a = Number(ra.value); if (a > b - MIN_CLIP) b = a + MIN_CLIP; clamp(); sync(); });
      rb.addEventListener('input', () => { b = Number(rb.value); if (b < a + MIN_CLIP) a = b - MIN_CLIP; clamp(); sync(); });
      for (const r of [ra, rb]) r.addEventListener('change', () => haptic());
      el.addEventListener('click', (e) => {
        const nz = e.target.closest('[data-nudge],[data-snap]');
        if (nz) {
          haptic();
          const [k, v] = (nz.dataset.nudge || nz.dataset.snap).split(':');
          const val = Number(v);
          if (nz.dataset.nudge) { if (k === 'a') a += val; else b += val; } else if (k === 'a') { a = val; if (b < a + MIN_CLIP) b = a + 15; } else { b = val; if (a > b - MIN_CLIP) a = b - 15; }
          clamp();
          if (a < win[0] || b > win[1]) setWindow();
          sync(true);
          return;
        }
        const zb = e.target.closest('[data-zoom]');
        if (zb) {
          haptic();
          zoom = zb.dataset.zoom;
          el.querySelectorAll('[data-zoom]').forEach((x) => x.setAttribute('aria-pressed', String(x === zb)));
          setWindow(); sync(true);
          return;
        }
        if (e.target.closest('[data-make]')) close({ start: a, end: b, title: el.querySelector('[name="title"]').value.trim() });
      });

      sync(true);
      requestAnimationFrame(() => drawStrip());
      // load the thumbnail strip and subtitles (encrypted) in the background
      const note = el.querySelector('[data-frames-note]');
      if (movie.thumbs && movie.thumbs.path) {
        try {
          const url = await blobUrl(ctx.store, movie.thumbs.path, 'image/jpeg');
          if (url) { sprite = { img: await loadImage(url), ...movie.thumbs }; sync(true); }
        } catch (e) { note.hidden = false; note.textContent = previewError(e, ctx.store.isDemo()); }
      } else {
        note.hidden = false;
        note.textContent = ctx.store.isDemo()
          ? 'In your real app, frames from the movie show here so you can find the scene.'
          : 'No frames for this movie yet (only movies cut after previews were switched on have them). Use the times, or a part\'s preview → "Make a clip from here".';
      }
      if (movie.subs_preview) {
        try { subs = (await jsonFile(ctx.store, movie.subs_preview)) || []; sync(); } catch { /* subtitles are a bonus */ }
      }
    },
  });
  if (!result) return;
  try {
    await ctx.store.sendCommand({ type: 'clip', movie_id: movie.id, movie: movie.name, start: result.start, end: result.end, title: result.title });
    haptic();
    toast(ctx.store.isDemo() ? 'Clip requested (demo)' : 'Clip requested. The laptop starts within 30 seconds.');
  } catch (e) {
    toast(e.message || "Couldn't send the request.", { kind: 'err', ms: 4500 });
  }
}

/** Clip requests for a movie that aren't finished yet (shown above its Custom clips). */
export function pendingClipsHtml(st, movie) {
  const all = (st.data.commands && st.data.commands.json && st.data.commands.json.commands) || [];
  const dayAgo = Date.now() - 24 * 3600 * 1000;
  const pending = new Set(pendingCommands(st.data.commands, st.data.laptop, 'clip').map((c) => c.id));
  const cmds = all.filter((c) => c.type === 'clip' && c.movie_id === movie.id
    && (pending.has(c.id) || (commandState(c, st.data.laptop).status === 'failed' && Date.parse(c.at || 0) > dayAgo)));
  if (!cmds.length) return '';
  return `<div class="group">${cmds.map((c) => {
    const s = commandState(c, st.data.laptop);
    const bad = s.status === 'failed';
    return `<div class="row"><span class="row-ic ${bad ? 'tone-bad' : 'tone-accent'}">${bad ? icon('alert') : '<span class="spinner"></span>'}</span>
      <span class="row-main"><span class="row-title">${esc(c.title || 'Custom clip')} <span class="muted">${timecode(c.start)}–${timecode(c.end)}</span></span>
      <span class="row-sub ${bad ? 'tone-bad-text' : ''}">${esc(bad ? `Couldn't make it: ${s.note}` : s.note)}</span></span></div>`;
  }).join('')}</div>`;
}
