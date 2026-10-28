// Recommendations: the movies Claude says to download next (Insights' picks), as one list you can edit.
// A pick drops off by itself once the movie is in your movie list, or after a day if it wasn't downloaded
// (the laptop then asks Claude for a fresh one). Your own additions stay until you remove them.
import { esc, icon, toast, haptic, openSheet } from '../ui.js';
import { relTime } from '../logic.js';
import { makePage, freshness, banner, skeleton, empty } from './common.js';
import { genreColor } from './insights.js';

const DAY = 24 * 3600 * 1000;
const DONE_LABEL = { have: 'In your movies', skipped: 'Removed', expired: 'Not downloaded in a day' };

// Same matching as the laptop (has_movie): 'Gone Girl' matches 'Gone Girl (2014)' and 'Gone.Girl.2014.1080p'.
const norm = (t) => String(t || '').toLowerCase().replace(/#\S+/g, ' ').replace(/[^a-z0-9]+/g, ' ').trim();
const titleKey = (t) => norm(String(t || '').replace(/\(?\b(19|20)\d{2}\b\)?/g, ' '));
export function hasMovie(title, library) {
  const k = titleKey(title);
  if (!k) return false;
  return library.some((n) => { const x = titleKey(n); return x === k || (k.length >= 6 && x.startsWith(`${k} `)); });
}

/** Every movie the laptop has or made: To edit, Downloads, plus the movies already cut. */
function libraryOf(st) {
  const lp = st.data.laptop || {};
  const ins = st.data.insights || {};
  const names = [...(lp.queue || []), ...(lp.library || [])].map((f) => f.name)
    .concat(((st.data.state || {}).movies || []).map((m) => m.name), ins.library || []);
  return names.filter(Boolean);
}

/** The picks as the app shows them: waiting (newest first) and recently done, with the checks done here too. */
export function picksOf(st, now = Date.now()) {
  const ins = (st.data && st.data.insights) || {};
  const lib = libraryOf(st);
  const all = (ins.picks || []).map((p) => {
    let status = p.status;
    if (status === 'new' && hasMovie(p.title, lib)) status = 'have';
    else if (status === 'new' && p.by !== 'you' && p.added && now - Date.parse(p.added) >= DAY) status = 'expired';
    return { ...p, status };
  });
  return { waiting: all.filter((p) => p.status === 'new'), done: all.filter((p) => p.status !== 'new').reverse().slice(0, 12) };
}

function leftText(p, now) {
  if (p.by === 'you') return 'Added by you';
  if (!p.added) return '';
  const h = Math.max(0, (Date.parse(p.added) + DAY - now) / 3600000);
  return h < 1 ? 'Goes in under an hour' : `${Math.round(h)}h left`;
}

export function createRecommendView(ctx) {
  const page = makePage({ title: 'Recommended' });
  const { content } = page;
  let lastSt = null;
  const pending = new Map();   // title -> { status, at }: a tap the laptop may not have saved yet (kept 10 minutes)

  function tile(p) {
    const c = (p.genres && p.genres[0]) ? genreColor(p.genres[0]) : 'var(--c-recommend)';
    const letter = (String(p.title || '?').replace(/^(the|a|an)\s+/i, '')[0] || '?').toUpperCase();
    return `<span class="rec-tile" style="--g:${c}" aria-hidden="true">${esc(letter)}</span>`;
  }

  function card(p, now) {
    const left = leftText(p, now);
    const urgent = p.by !== 'you' && p.added && Date.parse(p.added) + DAY - now < 4 * 3600000;
    return `<div class="rec" data-title="${esc(p.title)}">
      ${tile(p)}
      <div class="rec-main">
        <div class="rec-head">
          <span class="rec-title">${esc(p.title || '')}${p.year ? ` <span class="muted">${esc(p.year)}</span>` : ''}</span>
          <button class="icon-btn rec-more" data-edit="${esc(p.title)}" aria-label="Edit ${esc(p.title)}">${icon('more')}</button>
        </div>
        ${(p.genres || []).length ? `<span class="genre-chips">${p.genres.map((g) => `<span class="gchip" style="--g:${genreColor(g)}">${esc(g)}</span>`).join('')}</span>` : ''}
        ${p.why ? `<p class="rec-why">${esc(p.why)}</p>` : ''}
        <div class="rec-meta">
          ${p.trend ? `<span class="pick-trend">${icon('insights')}${esc(p.trend)}</span>` : ''}
          ${left ? `<span class="rec-left${urgent ? ' rec-left--soon' : ''}${p.by === 'you' ? ' rec-left--you' : ''}">${icon(p.by === 'you' ? 'flag' : 'clock')}${esc(left)}</span>` : ''}
        </div>
        <div class="rec-actions">
          <button class="btn btn--small rec-got" data-pick="have" data-title="${esc(p.title)}">${icon('check')}Got it</button>
          <button class="btn btn--quiet btn--small" data-pick="skipped" data-title="${esc(p.title)}">${icon('trash')}Remove</button>
        </div>
      </div>
    </div>`;
  }

  function doneRow(p) {
    return `<div class="row pick pick--done">
      <span class="row-ic ${p.status === 'have' ? 'tone-ok' : 'tone-muted'}">${icon(p.status === 'have' ? 'check' : p.status === 'expired' ? 'clock' : 'x')}</span>
      <span class="row-main"><span class="row-title clamp1">${esc(p.title || '')}${p.year ? ` <span class="muted">(${esc(p.year)})</span>` : ''}</span>
        <span class="row-sub">${esc(DONE_LABEL[p.status] || p.status)}${p.at ? ` · <span data-rel="${Date.parse(p.at)}">${esc(relTime(Date.parse(p.at)))}</span>` : ''}</span></span>
      ${p.status !== 'have' ? `<button class="btn btn--quiet btn--small" data-pick="new" data-title="${esc(p.title)}">Bring back</button>` : ''}
    </div>`;
  }

  function listOf(st, now = Date.now()) {
    const { waiting, done } = picksOf(st, now);
    const all = waiting.concat(done);
    for (const p of all) {                      // a tap still on its way to the laptop
      const s = pending.get(p.title);
      if (s && now - s.at > 10 * 60000) pending.delete(p.title); else if (s) p.status = s.status;
    }
    return { shown: all.filter((p) => p.status === 'new'), doneAll: all.filter((p) => p.status !== 'new') };
  }

  function render(st) {
    lastSt = st;
    page.setSub(freshness(st));
    if (!st.data) { content.innerHTML = banner(st) + skeleton(3); return; }
    const ins = st.data.insights;
    const now = Date.now();
    const { shown, doneAll } = listOf(st, now);
    const tr = (ins && ins.trends) || {};
    const chips = (tr.season || []).map((x) => `<span class="trend-chip trend-chip--season">${esc(x.moment)}: ${esc((x.genres || []).join(', '))}</span>`)
      .concat((tr.trending || []).slice(0, 6).map((x) => `<span class="trend-chip" title="${esc(x.about || '')}">${icon('insights')}${esc(x.title)}</span>`));
    const add = `<button class="btn btn--quiet btn--small" data-add>${icon('plus')}Add a movie</button>`;
    const more = `<button class="btn btn--quiet btn--small" data-more-picks>${icon('refresh')}New picks now</button>`;
    content.innerHTML = banner(st)
      + `<div class="rec-summary"><span class="rec-count">${shown.length}</span><span class="rec-summary-text"><strong>${shown.length === 1 ? 'movie' : 'movies'} to download</strong><span class="muted">Picked by Claude from your numbers, the season and what's trending${ins && ins.updated ? `, <span data-rel="${Date.parse(ins.updated)}">${esc(relTime(Date.parse(ins.updated)))}</span>` : ''}</span></span></div>`
      + `<div class="rec-tools">${add}${more}</div>`
      + (chips.length ? `<h3 class="group-title">Right now</h3><div class="trend-chips" aria-label="What people watch right now">${chips.join('')}</div>` : '')
      + (shown.length ? `<div class="rec-list">${shown.map((p) => card(p, now)).join('')}</div>`
        : empty({ icon: 'download', title: 'Nothing to download right now', text: 'Claude picks new movies every few hours, and again as soon as one runs out. Add your own with Add a movie.' }))
      + '<p class="group-foot">A movie drops off by itself once it\'s in To edit, Downloads or your movies. One that isn\'t downloaded within a day makes room for a fresh pick. Your own stay until you remove them.</p>'
      + (doneAll.length ? `<details class="picks-done"><summary>Recently done (${doneAll.length})</summary><div class="group">${doneAll.map(doneRow).join('')}</div></details>` : '');
  }

  async function send(cmd, ok, title, status) {
    const p = title && ((lastSt.data.insights || {}).picks || []).find((x) => x.title === title);
    const was = p && { status: p.status, added: p.added };
    if (title && status) {
      pending.set(title, { status, at: Date.now() });
      if (p) Object.assign(p, { status }, status === 'new' ? { added: new Date().toISOString() } : {});
      render(lastSt);
    }
    try {
      await ctx.store.sendCommand(cmd);
      toast(ok, { kind: 'ok', ms: 3500 });
    } catch (err) {
      if (title) { pending.delete(title); if (p) Object.assign(p, was); render(lastSt); }
      toast(err.message || "Couldn't reach the laptop.", { kind: 'err', ms: 4500 });
    }
  }

  async function form(title, p = {}) {
    return openSheet({
      title,
      body: `<label class="field"><span>Movie or show</span><input class="input" data-t value="${esc(p.title || '')}" autocomplete="off" spellcheck="false" placeholder="e.g. Knives Out"></label>
        <label class="field"><span>Year (optional)</span><input class="input" data-y inputmode="numeric" maxlength="4" value="${esc(p.year || '')}" placeholder="2019"></label>
        <div class="sheet-actions"><button class="btn btn--primary btn--block" data-save>Save</button>
        ${p.title ? `<button class="btn btn--quiet btn--block rec-del" data-remove>${icon('trash')}Remove from the list</button>` : ''}</div>`,
      onMount: (el, close) => {
        const t = el.querySelector('[data-t]'), y = el.querySelector('[data-y]');
        const done = () => { if (t.value.trim()) close({ title: t.value.trim(), year: Number(y.value) || null }); };
        el.querySelector('[data-save]').addEventListener('click', done);
        [t, y].forEach((i) => i.addEventListener('keydown', (e) => { if (e.key === 'Enter') done(); }));
        const rm = el.querySelector('[data-remove]');
        if (rm) rm.addEventListener('click', () => close({ remove: true }));
        setTimeout(() => t.focus(), 300);
      },
    });
  }

  content.addEventListener('click', async (e) => {
    if (e.target.closest('[data-retry]')) { ctx.store.refresh(); return; }
    const pk = e.target.closest('[data-pick]');
    if (pk) {
      haptic();
      const { pick, title } = pk.dataset;
      await send({ type: 'pick', title, status: pick },
        pick === 'have' ? `Ticked off ${title}.` : pick === 'skipped' ? `Removed ${title}. Claude picks another next time.` : `${title} is back for another day.`, title, pick);
      return;
    }
    const ed = e.target.closest('[data-edit]');
    if (ed) {
      haptic();
      const title = ed.dataset.edit;
      const p = (((lastSt.data.insights || {}).picks) || []).find((x) => x.title === title) || { title };
      const v = await form('Edit recommendation', p);
      if (!v) return;
      if (v.remove) { await send({ type: 'pick', title, status: 'skipped' }, `Removed ${title}.`, title, 'skipped'); return; }
      if (v.title === p.title && (v.year || null) === (p.year || null)) return;
      p.title = v.title; p.year = v.year;
      await send({ type: 'pick_edit', title, new_title: v.title, year: v.year }, `Changed to ${v.title}.`);
      render(lastSt);
      return;
    }
    if (e.target.closest('[data-add]')) {
      haptic();
      const v = await form('Add a movie');
      if (!v || !v.title) return;
      const ins = lastSt.data.insights || (lastSt.data.insights = {});
      ins.picks = [{ title: v.title, year: v.year, genres: [], status: 'new', by: 'you', added: new Date().toISOString() }, ...(ins.picks || [])];
      render(lastSt);
      await send({ type: 'pick_add', title: v.title, year: v.year }, `Added ${v.title}.`);
      return;
    }
    const mp = e.target.closest('[data-more-picks]');
    if (mp && !mp.disabled) {
      haptic();
      mp.disabled = true;
      await send({ type: 'insights_now' }, 'Claude is picking fresh movies. Give it a few minutes.');
    }
  });

  return { page, render, count: (st) => (st.data ? listOf(st).shown.length : 0) };
}
