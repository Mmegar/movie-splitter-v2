import { esc, icon, toast, haptic, openSheet, swipeRows, longPress } from '../ui.js';
import { laptopStatus } from '../api.js';
import { relTime, parseWishlist, serializeWishlist, wishTitles, wishEntryFor, formatWishTitle, titleKey, fileNameOf } from '../logic.js';
import { makePage, freshness, banner, notesHtml, chip, skeleton } from './common.js';

function sizeText(mb) {
  if (!mb && mb !== 0) return '';
  return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

export function createQueueView(ctx) {
  const page = makePage({ title: 'Queue' });
  const { content } = page;
  let lastSt = null;
  let saving = false;

  // ---------- wish list writes (read-modify-write, one retry on a sha conflict) ----------
  async function changeWishlist(op, message, okText) {
    if (saving) return false;
    saving = true;
    render(lastSt);
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        const lines = parseWishlist((ctx.store.state.data.wishlist || {}).text || '');
        const next = op(lines);
        if (!next) return false;
        try {
          await ctx.store.saveWishlist(serializeWishlist(next), message);
          toast(okText);
          return true;
        } catch (e) {
          if (e.kind === 'conflict' && attempt === 0) { await ctx.store.refresh({ full: true }); continue; }
          throw e;
        }
      }
    } catch (e) {
      toast(e.message || 'Could not save the wish list.', { kind: 'err', ms: 4500 });
    } finally {
      saving = false;
      render(lastSt);
    }
    return false;
  }

  function removeTitle(title) {
    haptic();
    return changeWishlist(
      (lines) => {
        const i = lines.findIndex((l) => l.kind === 'title' && l.title === title);
        if (i < 0) return null;
        return lines.filter((_, j) => j !== i);
      },
      `Wish list: remove "${title}"`, `Removed ${title}`);
  }

  async function addSheet() {
    const result = await openSheet({
      title: 'Add to wish list',
      body: `
        <p>Only public-domain films can be fetched (mostly before 1929, plus some later films that were never renewed). The laptop looks for each title on archive.org.</p>
        <form class="sheet-actions" data-form novalidate>
          <div class="input-row">
            <label class="field"><span>Title</span><input class="input" name="title" autocomplete="off" autocapitalize="words" enterkeyhint="done" placeholder="Nosferatu" required autofocus></label>
            <label class="field"><span>Year</span><input class="input" name="year" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" placeholder="1922"></label>
          </div>
          <p class="note" data-err role="alert"></p>
          <button class="btn btn--primary btn--block" type="submit">${icon('plus')}Add to wish list</button>
        </form>`,
      onMount(el, close) {
        const form = el.querySelector('[data-form]');
        form.addEventListener('submit', (e) => {
          e.preventDefault();
          const t = formatWishTitle(form.title.value, form.year.value);
          const err = el.querySelector('[data-err]');
          if (!t) { err.textContent = 'Type a movie title.'; err.classList.add('note--warn'); return; }
          if (form.year.value && !/^\d{4}$/.test(form.year.value.trim())) { err.textContent = 'The year needs 4 digits, like 1922.'; err.classList.add('note--warn'); return; }
          const existing = wishTitles(parseWishlist((ctx.store.state.data.wishlist || {}).text || ''));
          if (existing.some((x) => titleKey(x) === titleKey(t))) { err.textContent = `${t} is already on the wish list.`; err.classList.add('note--warn'); return; }
          close(t);
        });
      },
    });
    if (!result) return;
    await changeWishlist((lines) => [...lines, { kind: 'title', raw: result, title: result }], `Wish list: add "${result}"`, `Added ${result}`);
  }

  async function wishActions(title) {
    const entry = wishEntryFor(title, (ctx.store.state.data.laptop || {}).wishlist);
    const v = await openSheet({
      className: 'sheet--action',
      body: `<div class="action-card">
          <div class="action-msg"><strong>${esc(title)}</strong>${entry && entry.note ? `<p>${esc(entry.note)}</p>` : ''}</div>
          ${entry && entry.source_url ? `<a class="action-btn" style="display:flex;align-items:center;justify-content:center" href="${esc(entry.source_url)}" target="_blank" rel="noopener" data-close>Open on archive.org</a>` : ''}
          <button class="action-btn is-destructive" data-ok>Remove from wish list</button>
        </div>
        <button class="action-btn action-cancel" data-close>Cancel</button>`,
      onMount: (el, close) => el.querySelector('[data-ok]').addEventListener('click', () => close('remove')),
    });
    if (v === 'remove') await removeTitle(title);
  }

  content.addEventListener('click', (e) => {
    if (e.target.closest('[data-retry]')) { ctx.store.refresh(); return; }
    if (e.target.closest('[data-add]')) { haptic(); addSheet(); return; }
    const more = e.target.closest('[data-more]');
    if (more) { wishActions(more.closest('[data-title]').dataset.title); }
  });
  swipeRows(content, '.wish-row', (row) => removeTitle(row.dataset.title));
  longPress(content, '.wish-row .swipe-face', (face) => wishActions(face.closest('[data-title]').dataset.title));

  function queueGroup(st) {
    const q = (st.data.state && st.data.state.queue) || [];
    const run = (st.data.state && st.data.state.run) || {};
    const head = '<div class="section-head"><h3>Waiting in To edit</h3></div>';
    if (!q.length) {
      return head + `<div class="group"><div class="row"><span class="row-ic tone-muted">${icon('inbox')}</span><span class="row-main"><span class="row-title">Nothing waiting</span><span class="row-sub">${run.active ? 'The current movie is the last one for now.' : 'Drop a movie in the laptop folder or add one to the wish list.'}</span></span></div></div>`;
    }
    return head + `<div class="group">${q.map((m, i) => `<div class="row">
      <span class="row-ic ${i === 0 ? 'tone-accent' : 'tone-muted'}"><span class="num" style="font-weight:700;font-size:15px">${i + 1}</span></span>
      <span class="row-main"><span class="row-title">${esc(m.name)}</span>
        <span class="row-sub">${[m.source === 'wishlist' ? 'From the wish list' : 'From the laptop', sizeText(m.size_mb), m.added ? `added ${relTime(Date.parse(m.added))}` : ''].filter(Boolean).map(esc).join(', ')}</span></span>
      ${i === 0 ? '<span class="chip chip--accent">Next</span>' : ''}
    </div>`).join('')}</div>`
      + '<p class="group-foot">Runs take movies from the top. A run starts by itself once a day, or tap Start run on Now.</p>';
  }

  function laptopGroup(st) {
    const lp = st.data.laptop;
    const head = '<div class="section-head"><h3>Laptop</h3></div>';
    if (!lp) {
      return head + `<div class="group"><div class="row"><span class="row-ic tone-muted">${icon('laptop')}</span><span class="row-main"><span class="row-title">Not connected yet</span><span class="row-sub">Install and start the laptop agent. It reports here every 5 minutes.</span></span></div></div>`;
    }
    const ls = laptopStatus(lp);
    const rows = [`<div class="row">
      <span class="row-ic ${ls.online ? 'tone-ok' : 'tone-muted'}">${icon('laptop')}</span>
      <span class="row-main"><span class="row-title">${esc(lp.host || 'Laptop')} <span class="status-dot ${ls.online ? 'on' : ''}"></span></span>
        <span class="row-sub">${ls.online ? `Watching ${esc(lp.watch_folder || 'the folder')}` : `Offline, last seen ${ls.ageMs != null ? esc(relTime(Date.now() - ls.ageMs)) : 'never'}`}</span></span>
      <span class="row-val">${ls.online ? 'Online' : 'Offline'}</span>
    </div>`];
    if (lp.uploading) {
      rows.push(`<div class="row">
        <span class="row-ic tone-accent">${icon('cloud')}</span>
        <span class="row-main"><span class="row-title clamp1">${esc(lp.uploading.file)}</span>
          <span class="row-sub"><span>Uploading to To edit${ls.online ? '' : ' (paused while offline)'}</span><span class="bar" role="progressbar" aria-valuenow="${lp.uploading.percent}" aria-valuemin="0" aria-valuemax="100"><span style="width:${lp.uploading.percent}%"></span></span></span></span>
        <span class="row-val">${lp.uploading.percent}%</span>
      </div>`);
    }
    for (const u of (lp.uploaded || []).slice(0, 3)) {
      rows.push(`<div class="row">
        <span class="row-ic tone-ok">${icon('check')}</span>
        <span class="row-main"><span class="row-title clamp1">${esc(fileNameOf(u.file))}</span><span class="row-sub">Uploaded ${u.at ? esc(relTime(Date.parse(u.at))) : ''}${u.size_mb ? `, ${esc(sizeText(u.size_mb))}` : ''}</span></span>
      </div>`);
    }
    return head + `<div class="group">${rows.join('')}</div>`;
  }

  function wishGroup(st) {
    const lines = parseWishlist((st.data.wishlist || {}).text || '');
    const titles = wishTitles(lines);
    const lpWish = (st.data.laptop && st.data.laptop.wishlist) || [];
    const lpOnline = laptopStatus(st.data.laptop).online;
    const rows = titles.map((t) => {
      const e = wishEntryFor(t, lpWish);
      const status = e ? e.status : 'waiting';
      const note = e && e.note ? e.note : !e ? (lpOnline ? 'The laptop picks this up within 5 minutes.' : 'Starts when the laptop is online.') : '';
      return `<div class="swipe-row wish-row ${saving ? 'is-saving' : ''}" data-title="${esc(t)}">
        <button class="swipe-del" aria-label="Remove ${esc(t)}">${icon('trash')}Remove</button>
        <div class="swipe-face wish-face">
          <div class="wish-main"><span class="wish-title">${esc(t)}</span>${note ? `<span class="wish-note">${esc(note)}</span>` : ''}</div>
          ${chip(status === 'failed' ? 'failed' : status)}
          <button class="wish-more" data-more aria-label="Options for ${esc(t)}">${icon('chevR')}</button>
        </div>
      </div>`;
    }).join('');
    return `<div class="section-head"><h3>Wish list</h3>${saving ? '<span class="muted" style="display:inline-flex;gap:8px;align-items:center;font-size:15px"><span class="spinner"></span>Saving</span>' : ''}</div>
      <div class="explain">${icon('info')}<p><strong>Only public-domain films can be fetched.</strong> The laptop finds them on archive.org and uploads them to To edit. For any other movie, put the file in the laptop folder${st.data.laptop && st.data.laptop.watch_folder ? ` (<span style="overflow-wrap:anywhere">${esc(st.data.laptop.watch_folder)}</span>)` : ''}.</p></div>
      <div class="group" role="list">${rows}<button class="add-row" data-add ${saving ? 'disabled' : ''}>${icon('plus')}Add a movie</button></div>
      ${titles.length ? '<p class="group-foot">Swipe left or press and hold a title to remove it. Changes save to the wish list on GitHub.</p>' : ''}`;
  }

  function render(st) {
    if (!st) return;
    lastSt = st;
    page.setSub(freshness(st));
    if (!st.data) { content.innerHTML = banner(st) + skeleton(4); return; }
    // Don't redraw under a finger mid-swipe.
    if (content.querySelector('.wish-row.swiped')) return;
    content.innerHTML = banner(st) + notesHtml(st) + queueGroup(st) + laptopGroup(st) + wishGroup(st);
  }

  return { page, render };
}
