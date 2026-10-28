import { esc, icon, toast, haptic, openSheet, confirmSheet } from '../ui.js';
import { laptopStatus } from '../api.js';
import { relTime, fileNameOf, pendingCommands, cutPercent } from '../logic.js';
import { makePage, freshness, banner, notesHtml, skeleton } from './common.js';

function sizeText(mb) {
  if (!mb && mb !== 0) return '';
  return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

/** The big Pause switch, used on Now and Queue. */
export function pauseRow(st, where = 'queue') {
  const s = (st.data.settings && st.data.settings.json) || {};
  const lp = st.data.laptop || {};
  const on = !!s.paused;
  const why = on ? 'Nothing is sent, downloaded, cut or posted until you switch it back.'
    : lp.paused ? lp.paused : 'Stops the laptop using the internet and GitHub from cutting.';
  return `<label class="row row--ctl pause-row ${on || lp.paused ? 'is-paused' : ''}" for="pause-${where}">
    <span class="row-ic ${on || lp.paused ? 'tone-warn' : 'tone-muted'}">${icon('pause')}</span>
    <span class="row-main"><span class="row-title">Pause everything</span><span class="row-sub">${esc(why)}</span></span>
    <input type="checkbox" class="switch" role="switch" id="pause-${where}" data-pause ${on ? 'checked' : ''}>
  </label>`;
}

export async function togglePause(ctx, el) {
  haptic();
  const on = el.checked;
  el.disabled = true;
  try {
    await ctx.store.setPaused(on);
    toast(on ? 'Paused. The laptop stops within a minute.' : 'Resumed', { kind: on ? 'info' : 'ok' });
  } catch (e) {
    el.checked = !on;
    toast(e.message || "Couldn't save the pause switch.", { kind: 'err', ms: 4500 });
  } finally { el.disabled = false; }
}

/** The laptop's live status card (shown in Settings > Laptop). */
export function laptopGroup(st) {
  const lp = st.data.laptop;
  const head = '<h3 class="group-title" id="sec-laptop">Laptop</h3>';
  if (!lp) {
    return head + `<div class="group"><div class="row"><span class="row-ic tone-muted">${icon('laptop')}</span><span class="row-main"><span class="row-title">Not connected yet</span><span class="row-sub">Install and start the laptop agent. It reports here every 5 minutes.</span></span></div></div>`;
  }
  const ls = laptopStatus(lp);
  const rows = [`<div class="row">
    <span class="row-ic ${ls.online ? 'tone-ok' : 'tone-muted'}">${icon('laptop')}</span>
    <span class="row-main"><span class="row-title">${esc(lp.host || 'Laptop')} <span class="status-dot ${ls.online ? 'on' : ''}"></span></span>
      <span class="row-sub" style="overflow-wrap:anywhere">${ls.online ? `Watching ${esc(lp.watch_folder || 'the folder')}` : `Offline, last seen ${ls.ageMs != null ? esc(relTime(Date.now() - ls.ageMs)) : 'never'}`}</span></span>
    <span class="row-val">${ls.online ? 'Online' : 'Offline'}</span>
  </div>`];
  if (lp.cutting) {
    const pc = cutPercent(st.data.state && st.data.state.run);
    rows.push(`<div class="row">
      <span class="row-ic tone-accent">${icon('scissors')}</span>
      <span class="row-main"><span class="row-title clamp1">${esc(fileNameOf(lp.cutting.file))}</span>
        <span class="row-sub"><span>Cutting on the laptop</span><span class="bar" role="progressbar" aria-valuenow="${pc}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pc}%"></span></span></span></span>
      <span class="row-val">${pc}%</span>
    </div>`);
  }
  if (lp.uploading) {
    rows.push(`<div class="row">
      <span class="row-ic tone-accent">${icon('cloud')}</span>
      <span class="row-main"><span class="row-title clamp1">${esc(lp.uploading.file)}</span>
        <span class="row-sub"><span>Sending to GitHub${ls.online ? '' : ' (paused while offline)'}</span><span class="bar" role="progressbar" aria-valuenow="${lp.uploading.percent}" aria-valuemin="0" aria-valuemax="100"><span style="width:${lp.uploading.percent}%"></span></span></span></span>
      <span class="row-val">${lp.uploading.percent}%</span>
    </div>`);
  }
  for (const u of (lp.uploaded || []).slice(0, 1)) {
    rows.push(`<div class="row">
      <span class="row-ic tone-ok">${icon('check')}</span>
      <span class="row-main"><span class="row-title clamp1">${esc(fileNameOf(u.file))}</span><span class="row-sub">Uploaded ${u.at ? esc(relTime(Date.parse(u.at))) : ''}${u.size_mb ? `, ${esc(sizeText(u.size_mb))}` : ''}</span></span>
    </div>`);
  }
  rows.push(pauseRow(st));
  return head + `<div class="group">${rows.join('')}</div>`;
}

const cleanTitle = (f) => String(f || '').replace(/\.[a-z0-9]{2,4}$/i, '').replace(/[._]+/g, ' ');

export function createQueueView(ctx) {
  const page = makePage({ title: 'Queue' });
  const { content } = page;
  let lastSt = null;

  content.addEventListener('change', (e) => { if (e.target.matches('[data-pause]')) togglePause(ctx, e.target); });
  content.addEventListener('click', (e) => {
    if (e.target.closest('[data-retry]')) { ctx.store.refresh(); return; }
    const up = e.target.closest('[data-up]');
    if (up) { haptic(); const i = queueOf(lastSt).findIndex((f) => f.name === up.dataset.up); reorder(lastSt, up.dataset.up, i - 1); return; }
    const sc = e.target.closest('[data-style]');
    if (sc) { haptic(); styleActions(sc.dataset.style, sc.dataset.cur); return; }
    const qm = e.target.closest('[data-qmore]');
    if (qm) { queueActions(qm.dataset.qmore, Number(qm.dataset.i), Number(qm.dataset.n)); return; }
    const al = e.target.closest('[data-add-lib]');
    if (al) { libraryActions(al.dataset.addLib); return; }
    const bm = e.target.closest('[data-bin-more]');
    if (bm) { binActions(bm.dataset.binMore, bm.dataset.folder); return; }
    if (e.target.closest('[data-bin-toggle]')) { haptic(); binOpen = !binOpen; render(lastSt); }
  });

  // ---------- laptop folders (commands to the laptop) ----------
  let localOrder = null;            // shown while a reorder is on its way to the laptop
  async function send(cmd, okText) {
    try {
      await ctx.store.sendCommand(cmd);
      haptic();
      if (okText) toast(okText);
    } catch (e) { toast(e.message || "Couldn't send that to the laptop.", { kind: 'err', ms: 4500 }); }
  }
  function busyFiles(st) {
    const m = new Map();
    for (const c of pendingCommands(st.data.commands, st.data.laptop)) if (c.file) m.set(c.file, c.type);
    return m;
  }
  function queueOf(st) {
    const q = ((st.data.laptop || {}).queue || []).slice();
    const pendingOrder = pendingCommands(st.data.commands, st.data.laptop, 'queue_order').pop();
    const order = pendingOrder ? pendingOrder.order : localOrder;
    if (order) q.sort((x, y) => (order.indexOf(x.name) + 1 || 1e6) - (order.indexOf(y.name) + 1 || 1e6));
    if (!pendingOrder) localOrder = null;
    return q;
  }
  async function reorder(st, name, to) {
    const names = queueOf(st).map((f) => f.name);
    const i = names.indexOf(name);
    if (i < 0) return;
    names.splice(i, 1);
    names.splice(Math.max(0, Math.min(names.length, to)), 0, name);
    localOrder = names;
    render(lastSt);
    await send({ type: 'queue_order', order: names });
  }

  async function queueActions(name, i, total) {
    const v = await openSheet({
      className: 'sheet--action',
      body: `<div class="action-card">
          <div class="action-msg"><strong>${esc(cleanTitle(name))}</strong><p>In To edit on the laptop, waiting to be cut.</p></div>
          <button class="action-btn" data-v="character">Character to follow…</button>
          ${i > 0 ? '<button class="action-btn" data-v="top">Do this one next</button>' : ''}
          ${i < total - 1 ? '<button class="action-btn" data-v="bottom">Move to the end</button>' : ''}
          <button class="action-btn is-destructive" data-v="out">Take it out of To edit</button>
        </div>
        <button class="action-btn action-cancel" data-close>Cancel</button>`,
      onMount: (el, close) => el.addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (b) close(b.dataset.v); }),
    });
    if (v === 'top') await reorder(lastSt, name, 0);
    if (v === 'bottom') await reorder(lastSt, name, 1e6);
    if (v === 'out') await send({ type: 'to_library', file: name, from: 'queue' }, 'Moving it back to your Movies folder');
    if (v === 'character') await pickCharacter(name);
  }

  /** Character story and Hype edit follow this character for this movie only (blank = the main character). */
  async function pickCharacter(name) {
    const f = ((lastSt && lastSt.data.laptop && lastSt.data.laptop.queue) || []).find((q) => q.name === name) || {};
    const who = await openSheet({
      title: 'Character to follow',
      body: `<p class="note">For ${esc(cleanTitle(name))} only. Type the name the way the movie says it, or leave it blank for the main character.</p>
        <input class="input" data-who value="${esc(f.character || '')}" autocomplete="off" spellcheck="false" placeholder="e.g. Benoit Blanc">
        <button class="btn btn--primary btn--block" data-save style="margin-top:12px">Save</button>`,
      onMount: (el, close) => {
        const input = el.querySelector('[data-who]');
        el.querySelector('[data-save]').addEventListener('click', () => close(input.value.trim() || ' '));
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') close(input.value.trim() || ' '); });
        setTimeout(() => input.focus(), 300);
      },
    });
    if (who === undefined) return;
    const character = who.trim();
    await send({ type: 'set_character', file: name, character },
      character ? `It will follow ${character}` : 'It will follow the main character');
  }

  async function libraryActions(name) {
    const v = await openSheet({
      className: 'sheet--action',
      body: `<div class="action-card">
          <div class="action-msg"><strong>${esc(cleanTitle(name))}</strong><p>Move it into To edit so it's sent to GitHub and cut.</p></div>
          <button class="action-btn" data-v="end">Add to the queue</button>
          <button class="action-btn" data-v="first">Add and do it first</button>
        </div>
        <button class="action-btn action-cancel" data-close>Cancel</button>`,
      onMount: (el, close) => el.addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (b) close(b.dataset.v); }),
    });
    if (v) await send({ type: 'to_queue', file: name, from: 'library', first: v === 'first' }, 'Sent to the laptop');
  }

  async function binActions(name, folder) {
    const v = await openSheet({
      className: 'sheet--action',
      body: `<div class="action-card">
          <div class="action-msg"><strong>${esc(cleanTitle(name))}</strong><p>${folder === 'failed' ? 'Cutting this one failed.' : 'Already cut. Its parts are in Ready to post.'}</p></div>
          <button class="action-btn" data-v="again">${folder === 'failed' ? 'Try again' : 'Cut it again'}</button>
          <button class="action-btn is-destructive" data-v="bin">Move to the Recycle Bin</button>
        </div>
        <button class="action-btn action-cancel" data-close>Cancel</button>`,
      onMount: (el, close) => el.addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (b) close(b.dataset.v); }),
    });
    if (v === 'again') await send({ type: 'to_queue', file: name, from: folder }, 'Moving it back into To edit');
    if (v === 'bin') {
      const ok = await confirmSheet({ title: `Move "${cleanTitle(name)}" to the Recycle Bin?`, message: "You can still get it back from the laptop's Recycle Bin. Custom clips of this movie can't be made after it's gone.", confirm: 'Move to Recycle Bin', destructive: true });
      if (ok) await send({ type: 'to_bin', file: name, from: folder }, 'Moving it to the Recycle Bin');
    }
  }

  function fileRow(f, { busy, lead = '', action = '', sub = '', extra = '' }) {
    return `<div class="row file-row ${busy ? 'is-busy' : ''}">
      ${lead}
      <span class="row-main"><span class="row-title clamp1">${esc(cleanTitle(f.name))}</span>
        <span class="row-sub">${busy ? `<span class="busy"><span class="spinner"></span>${esc(busy)}</span>` : esc(sub)}</span>${extra}</span>
      ${action}
    </div>`;
  }

  function laptopQueueGroup(st) {
    const lp = st.data.laptop;
    if (!lp || !lp.queue) return '';
    const q = queueOf(st);
    const busy = busyFiles(st);
    const head = `<div class="section-head"><h3>Up next on the laptop</h3>${q.length ? `<span class="muted">${q.length} in To edit</span>` : ''}</div>`;
    if (!q.length) {
      return head + `<div class="group"><div class="row"><span class="row-ic tone-muted">${icon('inbox')}</span><span class="row-main"><span class="row-title">To edit is empty</span><span class="row-sub">Add a movie from your Movies folder below, or drop one in ${esc(lp.watch_folder || 'To edit')}.</span></span></div></div>`;
    }
    return head + `<div class="group">${q.map((f, i) => fileRow(f, {
      busy: busy.get(f.name) === 'to_library' ? 'Taking it out…' : f.sending ? '' : '',
      lead: `<span class="row-ic ${i === 0 ? 'tone-accent' : 'tone-muted'}"><span class="num" style="font-weight:700;font-size:15px">${i + 1}</span></span>`,
      sub: [f.sending ? 'Sending to GitHub now' : i === 0 ? 'Next to cut' : '', f.character ? `Following ${f.character}` : '', sizeText(f.size_mb)].filter(Boolean).join(' · '),
      extra: styleChip(st, f),
      action: `<span class="reorder">
        <button class="icon-btn" data-up="${esc(f.name)}" ${i === 0 ? 'disabled' : ''} aria-label="Move ${esc(cleanTitle(f.name))} up">${icon('chevL', 'rot90')}</button>
        <button class="icon-btn" data-qmore="${esc(f.name)}" data-i="${i}" data-n="${q.length}" aria-label="Options for ${esc(cleanTitle(f.name))}">${icon('more')}</button>
      </span>`,
    })).join('')}</div>
    <p class="group-foot">The laptop sends one movie at a time, from the top. Tap the arrow to move a movie up.</p>`;
  }

  /** The cutting style of a movie in To edit: a small button that opens the choice. */
  function styleChip(st, f) {
    const pending = pendingCommands(st.data.commands, st.data.laptop, 'set_style').filter((c) => c.file === f.name).pop();
    const style = pending ? pending.style : (f.style || 'classic');
    const label = style === 'zoom' ? 'Zoomed + tracking' : 'Classic';
    if (f.sending) return `<span class="style-chip is-fixed">${icon(style === 'zoom' ? 'scissors' : 'frames')}${label}</span>`;
    return `<button class="style-chip ${style === 'zoom' ? 'is-zoom' : ''}" data-style="${esc(f.name)}" data-cur="${style}" aria-label="Cutting style: ${label}. Change">${icon(style === 'zoom' ? 'scissors' : 'frames')}${label}${pending ? ' …' : ''}<span class="chev">▾</span></button>`;
  }

  async function styleActions(name, cur) {
    const v = await openSheet({
      className: 'sheet--action',
      body: `<div class="action-card">
          <div class="action-msg"><strong>How should ${esc(cleanTitle(name))} be cut?</strong></div>
          <button class="action-btn ${cur === 'classic' ? 'is-on' : ''}" data-v="classic">${cur === 'classic' ? '✓ ' : ''}Classic<span class="action-sub">Whole picture on a blurred background · shorter parts</span></button>
          <button class="action-btn ${cur === 'zoom' ? 'is-on' : ''}" data-v="zoom">${cur === 'zoom' ? '✓ ' : ''}Zoomed + tracking<span class="action-sub">Zoomed in, follows who's talking or moving · 3–5 min parts, fewer of them</span></button>
        </div>
        <button class="action-btn action-cancel" data-close>Cancel</button>`,
      onMount: (el, close) => el.addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (b) close(b.dataset.v); }),
    });
    if (!v || v === cur) return;
    await send({ type: 'set_style', file: name, style: v }, v === 'zoom' ? 'It will be cut zoomed in, following faces' : 'It will be cut the classic way');
  }

  function libraryGroup(st) {
    const lp = st.data.laptop;
    if (!lp || !lp.library) return '';
    const busy = busyFiles(st);
    const lib = lp.library;
    const head = `<div class="section-head"><h3>Your Movies folder</h3>${lib.length ? `<span class="muted">${lib.length}</span>` : ''}</div>`;
    const rows = lib.length ? lib.map((f) => fileRow(f, {
      busy: busy.get(f.name) === 'to_queue' ? 'Moving to To edit…' : '',
      lead: `<span class="row-ic tone-muted">${icon('frames')}</span>`,
      sub: [sizeText(f.size_mb), f.modified ? `added ${relTime(Date.parse(f.modified))}` : ''].filter(Boolean).join(' · '),
      action: busy.has(f.name) ? '' : `<button class="btn btn--small btn--accent" data-add-lib="${esc(f.name)}">${icon('plus')}To edit</button>`,
    })).join('') : `<div class="row"><span class="row-ic tone-muted">${icon('frames')}</span><span class="row-main"><span class="row-title">No movies here</span><span class="row-sub">Movies you download into ${esc(lp.library_folder || 'your Movies folder')} show up here.</span></span></div>`;
    return head + `<div class="group">${rows}</div>${lib.length ? `<p class="group-foot">Movies in <span style="overflow-wrap:anywhere">${esc(lp.library_folder || '')}</span>. Tap To edit and the laptop moves it into the queue.</p>` : ''}`;
  }

  let binOpen = false;
  function binGroup(st) {
    const lp = st.data.laptop;
    if (!lp || !lp.bin) return '';
    const busy = busyFiles(st);
    const bin = lp.bin;
    const total = bin.reduce((n, f) => n + (f.size_mb || 0), 0);
    const head = `<div class="section-head"><h3>Bin</h3><button class="link" data-bin-toggle>${binOpen ? 'Hide' : bin.length ? `Show ${bin.length}` : ''}</button></div>`;
    if (!bin.length) return head + `<div class="group"><div class="row"><span class="row-ic tone-muted">${icon('trash')}</span><span class="row-main"><span class="row-title">Empty</span><span class="row-sub">Originals land here (the Done and Failed folders) after they're cut.</span></span></div></div>`;
    if (!binOpen) {
      return head + `<button class="group row bin-sum" data-bin-toggle><span class="row-ic tone-muted">${icon('trash')}</span><span class="row-main"><span class="row-title">${bin.length} original${bin.length === 1 ? '' : 's'}, ${esc(sizeText(total))}</span><span class="row-sub">Already-cut movies in Done${bin.some((f) => f.folder === 'failed') ? ', plus ones that failed' : ''}. Cut again or move to the Recycle Bin.</span></span>${icon('chevR', 'row-chev')}</button>`;
    }
    return head + `<div class="group">${bin.map((f) => fileRow(f, {
      busy: busy.get(f.name) === 'to_bin' ? 'Moving to the Recycle Bin…' : busy.get(f.name) === 'to_queue' ? 'Moving to To edit…' : '',
      lead: `<span class="row-ic ${f.folder === 'failed' ? 'tone-bad' : 'tone-ok'}">${icon(f.folder === 'failed' ? 'alert' : 'check')}</span>`,
      sub: [f.folder === 'failed' ? 'Failed' : 'Done', sizeText(f.size_mb), f.modified ? relTime(Date.parse(f.modified)) : ''].filter(Boolean).join(' · '),
      action: busy.has(f.name) ? '' : `<button class="icon-btn" data-bin-more="${esc(f.name)}" data-folder="${f.folder}" aria-label="Options for ${esc(cleanTitle(f.name))}">${icon('more')}</button>`,
    })).join('')}</div><p class="group-foot">Moving to the Recycle Bin is not permanent: empty the laptop's Recycle Bin to free the space.</p>`;
  }

  function queueGroup(st) {
    const q = (st.data.state && st.data.state.queue) || [];
    const run = (st.data.state && st.data.state.run) || {};
    const head = '<div class="section-head"><h3>On GitHub, waiting to be cut</h3></div>';
    if (!q.length) {
      return head + `<div class="group"><div class="row"><span class="row-ic tone-muted">${icon('cloud')}</span><span class="row-main"><span class="row-title">Nothing waiting</span><span class="row-sub">${run.active ? 'The current movie is the last one for now.' : 'Movies arrive here once the laptop has sent them.'}</span></span></div></div>`;
    }
    return head + `<div class="group">${q.map((m, i) => `<div class="row">
      <span class="row-ic ${i === 0 ? 'tone-accent' : 'tone-muted'}"><span class="num" style="font-weight:700;font-size:15px">${i + 1}</span></span>
      <span class="row-main"><span class="row-title">${esc(m.name)}</span>
        <span class="row-sub">${[m.source === 'wishlist' ? 'From the wish list' : 'From the laptop', sizeText(m.size_mb), m.added ? `added ${relTime(Date.parse(m.added))}` : ''].filter(Boolean).map(esc).join(', ')}</span></span>
      ${i === 0 ? '<span class="chip chip--accent">Next</span>' : ''}
    </div>`).join('')}</div>`
      + '<p class="group-foot">Runs take movies from the top. A run starts by itself once a day, or tap Start run on Now.</p>';
  }

  function render(st) {
    if (!st) return;
    lastSt = st;
    page.setSub(freshness(st));
    if (!st.data) { content.innerHTML = banner(st) + skeleton(4); return; }
    content.innerHTML = banner(st) + notesHtml(st) + `<div class="group">${pauseRow(st)}</div>` + laptopQueueGroup(st)
      + libraryGroup(st) + binGroup(st);
  }

  return { page, render };
}
