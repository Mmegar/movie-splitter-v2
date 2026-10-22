import { esc, icon, toast, copyText, haptic } from '../ui.js';
import { partLabel, MODE_LABELS, timecode, shortDuration, relTime, dayLabel, clock, postCounts, movieLength } from '../logic.js';
import { makePage, freshness, banner, notesHtml, chip, empty, skeleton } from './common.js';
import { openPreview, openClipEditor, pendingClipsHtml, openPartActions, partPendingHtml, sendAllParts, sendAllHtml, saveAllParts, cancelSending, saveCover } from './media.js';

const FILTERS = [['all', 'All'], ['processing', 'Working'], ['done', 'Done'], ['failed', 'Failed']];

function when(t) {
  if (!t) return '';
  const ms = Date.parse(t);
  return `${dayLabel(ms)}, ${clock(ms)}`;
}

/** The cut map: the whole movie as a strip, each part placed where it comes from. */
export function cutMap(movie, mode, settings, { mini = false } = {}) {
  const len = movieLength(movie, settings.stretch_to);
  if (!len) return '';
  const md = movie.modes[mode] || { parts: [] };
  const segs = (md.parts || []).map((p) => {
    const l = (p.start / len) * 100, w = Math.max(0.6, ((p.end - p.start) / len) * 100);
    const st = (p.post && p.post.status) || 'none';
    return `<span class="seg seg--${st}" style="left:${l.toFixed(2)}%;width:${w.toFixed(2)}%" ${mini ? '' : `data-jump="${p.n}" title="Part ${p.n}"`}></span>`;
  }).join('');
  const band = mode === 'stretch' && settings.stretch_from != null
    ? `<span class="band" style="left:${settings.stretch_from * 100}%;width:${(settings.stretch_to - settings.stretch_from) * 100}%"></span>` : '';
  if (mini) return `<div class="cutmap cutmap--mini" aria-hidden="true">${band}${segs}</div>`;
  return `<figure class="cutmap-wrap">
    <div class="cutmap" role="img" aria-label="Where the ${md.parts.length} parts come from in the movie">${band}${segs}</div>
    <figcaption class="cutmap-axis"><span>0:00</span>${mode === 'stretch' && band ? '<span class="band-key">Best stretch window</span>' : ''}<span>${timecode(len)}</span></figcaption>
  </figure>`;
}

function movieCard(m, settings) {
  const c = postCounts(m);
  const modes = Object.entries(m.modes || {});
  const firstMode = modes.find(([, md]) => md.parts && md.parts.length) || modes[0];
  return `<a class="movie-card" href="#/movies/${encodeURIComponent(m.id)}">
    <div class="movie-top">
      <h3 class="movie-name">${esc(m.name)}</h3>
      ${chip(m.status)}
    </div>
    ${firstMode ? cutMap(m, firstMode[0], settings, { mini: true }) : ''}
    <div class="movie-meta">
      <span>${esc(when(m.finished || m.started))}</span>
      ${modes.map(([k, md]) => `<span>${esc(md.label || MODE_LABELS[k])} <strong>${(md.parts || []).length}${md.planned && (md.parts || []).length < md.planned ? `/${md.planned}` : ''}</strong></span>`).join('')}
      ${c.posted ? `<span class="tone-ok-text">${c.posted} posted</span>` : ''}
    </div>
  </a>`;
}

export function createMoviesView(ctx) {
  const list = makePage({ title: 'Movies' });
  let filter = 'all';
  let detail = null; // { page, id, mode }
  let lastSt = null;

  list.content.addEventListener('click', (e) => {
    const f = e.target.closest('[data-filter]');
    if (f) { filter = f.dataset.filter; haptic(); renderList(lastSt); }
    if (e.target.closest('[data-retry]')) ctx.store.refresh();
  });

  function renderList(st) {
    list.setSub(freshness(st));
    if (!st.data) { list.content.innerHTML = banner(st) + skeleton(5); return; }
    const settings = (st.data.settings && st.data.settings.json) || {};
    const movies = (st.data.state && st.data.state.movies) || [];
    const counts = Object.fromEntries(FILTERS.map(([k]) => [k, k === 'all' ? movies.length : movies.filter((m) => m.status === k || (k === 'processing' && m.status === 'queued')).length]));
    const shown = movies.filter((m) => filter === 'all' || m.status === filter || (filter === 'processing' && m.status === 'queued'));
    list.content.innerHTML = banner(st) + notesHtml(st)
      + `<div class="segmented segmented--scroll" role="tablist" aria-label="Filter movies">${FILTERS.map(([k, l]) => `<button role="tab" aria-selected="${k === filter}" data-filter="${k}">${l}${counts[k] ? ` <span class="count">${counts[k]}</span>` : ''}</button>`).join('')}</div>`
      + (shown.length ? `<div class="movie-list">${shown.map((m) => movieCard(m, settings)).join('')}</div>`
        : empty(movies.length
          ? { icon: 'frames', title: 'No movies here', text: 'Try another filter.' }
          : { icon: 'frames', title: 'No movies yet', text: 'Movies show up here as soon as a run picks them up from To edit.', action: '<a class="btn btn--quiet" href="#/queue">Open the queue</a>' }));
  }

  let demoMode = false;
  function partRow(p, movie, mode) {
    const post = p.post || { status: 'none' };
    const postWhen = post.at ? ` ${post.status === 'scheduled' ? relTime(Date.parse(post.at)) : `${dayLabel(Date.parse(post.at))} ${clock(Date.parse(post.at))}`}` : '';
    return `<li class="part" id="part-${mode}-${p.n}">
      <div class="part-head">
        <span class="part-n">${partLabel(mode, p.n)}${p.title ? ` <span class="part-title">${esc(p.title)}</span>` : ''}</span>
        <span class="part-time">${timecode(p.start)}–${timecode(p.end)} <span class="muted">· ${shortDuration(p.seconds)}</span></span>
        ${chip(post.status)}
      </div>
      ${post.status !== 'none' && (postWhen || post.error) ? `<p class="part-post ${post.status === 'failed' ? 'tone-bad-text' : ''}">${post.error ? esc(post.error) : `${post.status === 'posted' ? 'Posted' : post.status === 'inbox' ? 'Sent to your TikTok drafts' : 'Posting'}${esc(postWhen)}`}${post.url ? ` · <a href="${esc(post.url)}" target="_blank" rel="noopener">Open on TikTok</a>` : ''}</p>` : ''}
      ${lastSt ? partPendingHtml(lastSt, movie, mode, p) : ''}
      ${p.caption ? `<p class="caption" data-expand>${esc(p.caption)}</p>` : ''}
      <div class="part-actions">
        <button class="btn btn--small btn--accent" data-preview="${p.n}" ${p.preview || demoMode ? '' : 'data-nopreview'}>${icon('play')}Preview</button>
        <button class="btn btn--small btn--quiet" data-clip="${p.n}">${icon('scissors')}Clip</button>
        ${p.cover ? `<button class="btn btn--small btn--quiet" data-cover="${p.n}">${icon('download')}Thumbnail</button>` : ''}
        ${p.caption ? `<button class="btn btn--small btn--quiet" data-copy="${esc(p.caption)}">${icon('copy')}Copy caption</button>` : ''}
        <button class="btn btn--small btn--quiet" data-partmore="${p.n}" aria-label="More for ${mode === 'clips' ? 'clip' : 'part'} ${p.n}">${icon('more')}More</button>
      </div>
    </li>`;
  }

  function renderDetail(st) {
    if (!detail || !st || !st.data) return;
    const movies = (st.data.state && st.data.state.movies) || [];
    const m = movies.find((x) => x.id === detail.id);
    const { page } = detail;
    if (!m) { page.content.innerHTML = empty({ icon: 'frames', title: 'Movie not found', text: 'It may have dropped off the list (only the last 50 movies are kept).' }); return; }
    page.setTitle(m.name);
    const settings = (st.data.settings && st.data.settings.json) || {};
    const modes = Object.keys(m.modes || {});
    if (!detail.mode || !modes.includes(detail.mode)) detail.mode = modes.find((k) => (m.modes[k].parts || []).length) || modes[0];
    const md = m.modes[detail.mode] || { parts: [] };
    const run = (st.data.state && st.data.state.run) || {};
    const live = run.active && run.movie === m.name && run.mode === detail.mode;
    const c = postCounts(m);
    page.setSub(`${chip(m.status)} <span class="muted">${m.source === 'wishlist' ? 'From the wish list' : 'From the laptop'}</span>`);
    demoMode = ctx.store.isDemo();
    page.content.innerHTML = `
      ${m.error ? `<div class="banner banner--err">${icon('alert')}<div><p>${esc(m.error)}</p></div></div>` : ''}
      ${m.note && m.status === 'queued' ? `<div class="banner banner--warn">${icon('pause')}<div><p><strong>${esc(m.note)}</strong></p><p class="muted">Parts made so far are kept. It carries on from the next part.</p></div></div>` : ''}
      <dl class="facts">
        <div><dt>Started</dt><dd>${esc(when(m.started)) || '—'}</dd></div>
        <div><dt>${m.status === 'failed' ? 'Stopped' : 'Finished'}</dt><dd>${m.finished ? esc(when(m.finished)) : m.status === 'processing' ? 'Working on it' : '—'}</dd></div>
        <div><dt>Subtitles</dt><dd>${esc(m.subtitles || 'None')}</dd></div>
        <div><dt>Posted</dt><dd>${c.posted} of ${c.parts}</dd></div>
      </dl>
      <button class="btn btn--accent btn--block" data-makeclip>${icon('scissors')}Make a clip of any scene</button>
      ${pendingClipsHtml(st, m)}
      ${modes.length > 1 ? `<div class="segmented" role="tablist" aria-label="Mode">${modes.map((k) => `<button role="tab" aria-selected="${k === detail.mode}" data-mode="${k}">${esc(m.modes[k].label || MODE_LABELS[k])} <span class="count">${(m.modes[k].parts || []).length}</span></button>`).join('')}</div>` : ''}
      ${cutMap(m, detail.mode, settings)}
      ${sendAllHtml(st, m)}
      <div class="section-head"><h3>${(md.parts || []).length} part${(md.parts || []).length === 1 ? '' : 's'}${md.planned ? ` of ${md.planned}` : ''}</h3>
        ${(() => { const left = (md.parts || []).filter((p) => !(p.post && ['posted', 'inbox'].includes(p.post.status))).length;
          return left ? `<button class="btn btn--small btn--accent" data-sendall>${icon('send')}Send all ${left} to TikTok</button>` : '<span class="muted">All sent</span>'; })()}</div>
      ${(md.parts || []).length ? `<button class="btn btn--quiet btn--block" data-saveall>${icon('inbox')}Save all ${(md.parts || []).length} to phone</button>` : ''}
      ${(md.parts || []).length ? `<ol class="parts">${md.parts.map((p) => partRow(p, m, detail.mode)).join('')}${live ? `<li class="part part--pending"><span class="spinner"></span>Making part ${run.part} of ${run.parts_total}…</li>` : ''}</ol>`
        : empty({ icon: 'scissors', title: live ? 'Making the first part' : m.status === 'failed' ? 'No parts were made' : 'No parts yet', text: live ? 'Parts appear here as soon as each one is made; they arrive on your laptop shortly after.' : m.status === 'processing' ? 'This mode starts after the current one.' : '' })}
    `;
  }

  async function openDetail(id) {
    if (detail && detail.id === id) { renderDetail(lastSt); return; }
    closeDetail(true);
    const page = makePage({ title: 'Movie', back: 'Movies' });
    page.el.classList.add('page--pushed');
    detail = { page, id, mode: null };
    page.el.addEventListener('click', async (e) => {
      if (e.target.closest('[data-back]')) { history.length > 1 ? history.back() : (location.hash = '#/movies'); return; }
      const mb = e.target.closest('[data-mode]');
      if (mb) { detail.mode = mb.dataset.mode; haptic(); renderDetail(lastSt); return; }
      const cp = e.target.closest('[data-copy]');
      if (cp) {
        const ok = await copyText(cp.dataset.copy);
        haptic();
        if (ok) { cp.classList.add('is-copied'); cp.innerHTML = `${icon('check')}Copied`; setTimeout(() => { cp.classList.remove('is-copied'); cp.innerHTML = `${icon('copy')}Copy caption`; }, 1600); toast('Caption copied'); }
        else toast("Couldn't copy. Press and hold the caption to copy it.", { kind: 'err' });
        return;
      }
      const movieNow = () => ((lastSt.data.state && lastSt.data.state.movies) || []).find((x) => x.id === detail.id);
      const pv = e.target.closest('[data-preview]');
      if (pv) {
        haptic();
        const m = movieNow();
        const part = m && (m.modes[detail.mode].parts || []).find((x) => String(x.n) === pv.dataset.preview);
        if (part) openPreview(ctx, m, detail.mode, part);
        return;
      }
      const cv = e.target.closest('[data-cover]');
      if (cv) {
        haptic();
        const m = movieNow();
        const part = m && (m.modes[detail.mode].parts || []).find((x) => String(x.n) === cv.dataset.cover);
        if (part) { try { await saveCover(ctx, m, part); } catch (err) { toast(err.message || "Couldn't get the thumbnail.", { kind: 'err' }); } }
        return;
      }
      const cl = e.target.closest('[data-clip]');
      if (cl) {
        haptic();
        const m = movieNow();
        const part = m && (m.modes[detail.mode].parts || []).find((x) => String(x.n) === cl.dataset.clip);
        if (part) openClipEditor(ctx, m, { start: part.start, end: Math.min(part.end, part.start + 600) });
        return;
      }
      const pm = e.target.closest('[data-partmore]');
      if (pm) {
        haptic();
        const m = movieNow();
        const part = m && (m.modes[detail.mode].parts || []).find((x) => String(x.n) === pm.dataset.partmore);
        if (part) openPartActions(ctx, m, detail.mode, part);
        return;
      }
      const cancelBtn = e.target.closest('[data-cancelsend]');
      if (cancelBtn) { haptic(); cancelSending(ctx, cancelBtn.dataset.cancelsend); return; }
      if (e.target.closest('[data-saveall]')) {
        haptic();
        const m = movieNow();
        if (m) saveAllParts(ctx, m, detail.mode);
        return;
      }
      if (e.target.closest('[data-sendall]')) {
        haptic();
        const m = movieNow();
        if (m) sendAllParts(ctx, m, detail.mode);
        return;
      }
      if (e.target.closest('[data-makeclip]')) {
        haptic();
        const m = movieNow();
        if (m) openClipEditor(ctx, m);
        return;
      }
      const j = e.target.closest('[data-jump]');
      if (j) {
        const row = page.el.querySelector(`#part-${detail.mode}-${j.dataset.jump}`);
        if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); row.classList.add('flash'); setTimeout(() => row.classList.remove('flash'), 1200); }
        return;
      }
      const ex = e.target.closest('[data-expand]');
      if (ex) ex.classList.toggle('is-open');
    });
    ctx.edgeSwipeBack(page.el, () => history.back());
    list.el.after(page.el);
    renderDetail(lastSt);
    requestAnimationFrame(() => requestAnimationFrame(() => { page.el.classList.add('is-in'); list.el.classList.add('is-under'); }));
  }

  function closeDetail(instant = false) {
    if (!detail) return;
    const el = detail.page.el;
    detail = null;
    list.el.classList.remove('is-under');
    if (instant) { el.remove(); return; }
    el.classList.remove('is-in');
    setTimeout(() => el.remove(), 360);
  }

  return {
    page: list,
    render(st) { lastSt = st; renderList(st); renderDetail(st); },
    route(parts) {
      if (parts[0]) openDetail(decodeURIComponent(parts[0]));
      else closeDetail();
    },
  };
}
