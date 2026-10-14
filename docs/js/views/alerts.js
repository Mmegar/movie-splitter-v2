import { esc, icon, toast, copyText, haptic } from '../ui.js';
import { mergeFeed, groupByDay, clock } from '../logic.js';
import { makePage, freshness, banner, empty, skeleton } from './common.js';
import { TYPE_IC, TYPE_TONE } from './now.js';

const FILTERS = [['all', 'All'], ['parts', 'Parts'], ['posts', 'Posts'], ['problems', 'Problems']];
const FILTER_TYPES = {
  parts: ['part_ready', 'planned', 'started', 'finished'],
  posts: ['posted', 'post_failed'],
  problems: ['failed', 'post_failed'],
};

/** Message text without the caption block (the caption gets its own copyable box). */
function bodyOf(it) {
  let m = String(it.message || '');
  if (it.caption) m = m.replace(/\n*📝?\s*Caption:\s*\n[\s\S]*$/, '').trim();
  return m;
}

function alertHtml(it) {
  const body = bodyOf(it);
  const src = it.sources.includes('ntfy') ? (it.sources.includes('status') ? 'Phone + status' : 'Phone notification') : 'Status';
  return `<article class="alert">
    <span class="row-ic tone-${TYPE_TONE[it.type] || 'muted'}">${icon(TYPE_IC[it.type] || 'info')}</span>
    <div class="alert-main">
      <div class="alert-top"><h4 class="alert-title">${esc(it.title || 'Notification')}</h4><time class="alert-time" datetime="${new Date(it.at).toISOString()}">${clock(it.at)}</time></div>
      ${body ? `<p class="alert-msg">${esc(body)}</p>` : ''}
      ${it.caption ? `<p class="caption" data-expand>${esc(it.caption)}</p>` : ''}
      <div class="alert-foot">
        ${it.caption ? `<button class="btn btn--small btn--accent" data-copy="${esc(it.caption)}">${icon('copy')}Copy caption</button>` : ''}
        ${it.movieId ? `<a class="btn btn--small btn--quiet" href="#/movies/${encodeURIComponent(it.movieId)}">Open movie</a>` : ''}
        <span class="src">${src}</span>
      </div>
    </div>
  </article>`;
}

export function createAlertsView(ctx) {
  const page = makePage({ title: 'Alerts' });
  let filter = 'all';
  let lastSt = null;
  let lastKey = '';

  page.content.addEventListener('click', async (e) => {
    const f = e.target.closest('[data-filter]');
    if (f) { filter = f.dataset.filter; haptic(); lastKey = ''; render(lastSt); return; }
    if (e.target.closest('[data-retry]')) { ctx.store.refresh(); return; }
    const cp = e.target.closest('[data-copy]');
    if (cp) {
      const ok = await copyText(cp.dataset.copy);
      haptic();
      if (ok) { cp.classList.add('is-copied'); cp.innerHTML = `${icon('check')}Copied`; toast('Caption copied'); }
      else toast("Couldn't copy. Press and hold the caption to copy it.", { kind: 'err' });
      return;
    }
    const ex = e.target.closest('[data-expand]');
    if (ex) ex.classList.toggle('is-open');
  });

  function render(st) {
    lastSt = st;
    page.setSub(freshness(st));
    if (!st.data) { page.content.innerHTML = banner(st) + skeleton(4); return; }
    const all = mergeFeed((st.data.state || {}).events, st.data.ntfy, st.data.state);
    const items = filter === 'all' ? all : all.filter((it) => FILTER_TYPES[filter].includes(it.type));
    // Skip re-rendering when nothing changed, so expanded captions and "Copied" states stay put.
    const key = `${filter}|${st.error ? st.error.kind : ''}|${items.length}|${items[0] ? items[0].key : ''}`;
    if (key === lastKey) return;
    lastKey = key;
    const noTopic = !ctx.store.isDemo() && !st.conn.topic;
    const head = banner(st)
      + (noTopic ? `<div class="banner banner--info">${icon('bell')}<div><p>Showing status events only. Add your ntfy topic in Settings → Connection to include phone notifications.</p></div><a class="banner-act" href="#/settings/connection">Add</a></div>` : '')
      + `<div class="segmented segmented--scroll" role="tablist" aria-label="Filter alerts">${FILTERS.map(([k, l]) => `<button role="tab" aria-selected="${k === filter}" data-filter="${k}">${l}</button>`).join('')}</div>`;
    if (!items.length) {
      page.content.innerHTML = head + empty(all.length
        ? { icon: 'bell', title: filter === 'problems' ? 'No problems' : 'Nothing here', text: filter === 'problems' ? 'Failed runs and failed posts show up here.' : 'Try another filter.' }
        : { icon: 'bell', title: 'No alerts yet', text: 'Run updates and recent phone notifications show up here.' });
      return;
    }
    page.content.innerHTML = head + groupByDay(items).map((g) => `<section class="feed-day" aria-label="${esc(g.label)}"><h3>${esc(g.label)}</h3>${g.items.map(alertHtml).join('')}</section>`).join('');
  }

  return { page, render };
}
