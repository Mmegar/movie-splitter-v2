import { esc, icon } from '../ui.js';
import { relTime } from '../logic.js';

/** A navigable page: compact nav bar + scroller with a large title, like UIKit. */
export function makePage({ title, back = null, right = '', large = true } = {}) {
  const el = document.createElement('div');
  el.className = 'page';
  el.innerHTML = `
    <header class="navbar ${large ? '' : 'navbar--solid'}">
      <div class="nav-left">${back ? `<button class="nav-back" data-back aria-label="Back to ${esc(back)}">${icon('chevL')}<span>${esc(back)}</span></button>` : ''}</div>
      <h2 class="nav-title">${esc(title)}</h2>
      <div class="nav-right">${right}</div>
    </header>
    <div class="scroller">
      <div class="ptr" aria-hidden="true"><div class="ptr-spin"></div></div>
      ${large ? `<div class="large-head"><h1 class="large-title">${esc(title)}</h1><p class="large-sub"></p></div>` : ''}
      <div class="content"></div>
    </div>`;
  const scroller = el.querySelector('.scroller');
  const navbar = el.querySelector('.navbar');
  const head = el.querySelector('.large-head');
  if (large) {
    const onScroll = () => {
      const past = scroller.scrollTop > (head ? head.offsetHeight - 8 : 30);
      navbar.classList.toggle('is-compact', past);
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
  } else navbar.classList.add('is-compact');
  return {
    el, scroller, navbar,
    content: el.querySelector('.content'),
    setTitle(t) {
      el.querySelector('.nav-title').textContent = t;
      const lt = el.querySelector('.large-title');
      if (lt) lt.textContent = t;
    },
    setSub(html) { const s = el.querySelector('.large-sub'); if (s) s.innerHTML = html; },
    setRight(html) { el.querySelector('.nav-right').innerHTML = html; },
  };
}

/** Status line under large titles: demo / live freshness / offline / error. */
export function freshness(st) {
  if (!st.data && st.loading) return '<span class="fresh">Loading…</span>';
  const demo = !st.conn || st.conn.demo || !st.conn.owner;
  if (demo) return '<span class="fresh fresh--demo"><span class="dot"></span>Demo data</span>';
  if (st.offline) return `<span class="fresh fresh--warn">${icon('offline')}Offline${st.data ? `, showing data from ${relTime(st.data.fetchedAt)}` : ''}</span>`;
  if (st.error) return `<span class="fresh fresh--warn">${icon('alert')}Couldn't update</span>`;
  if (st.data) return `<span class="fresh"><span class="dot dot--live"></span>Updated <span data-rel="${st.data.fetchedAt}">${relTime(st.data.fetchedAt)}</span></span>`;
  return '';
}

/** Error / setup banner shown at the top of every tab when something needs attention. */
export function banner(st) {
  const e = st.error;
  if (!e || st.offline) return '';
  const fix = ['auth', 'forbidden', 'notfound', 'setup'].includes(e.kind);
  return `<div class="banner banner--${e.kind === 'ratelimit' || e.kind === 'server' ? 'warn' : 'err'}" role="alert">
    ${icon('alert')}
    <div><p>${esc(e.message)}</p>${e.resetAt ? `<p class="muted">Resets at ${new Date(e.resetAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}.</p>` : ''}</div>
    ${fix ? '<a class="banner-act" href="#/settings/connection">Fix</a>' : '<button class="banner-act" data-retry>Retry</button>'}
  </div>`;
}

export function notesHtml(st) {
  const notes = (st.data && st.data.notes) || [];
  return notes.map((n) => `<div class="banner banner--info">${icon('info')}<div><p>${esc(n)}</p></div></div>`).join('');
}

export const STATUS_CHIP = {
  queued: ['Queued', 'muted'], processing: ['Processing', 'accent'], done: ['Done', 'ok'], failed: ['Failed', 'bad'],
  none: ['Not posted', 'muted'], scheduled: ['Scheduled', 'info'], posted: ['Posted', 'ok'], inbox: ['In TikTok drafts', 'info'],
  waiting: ['Waiting for laptop', 'muted'], searching: ['Searching', 'info'], found: ['Found', 'info'],
  downloading: ['Downloading', 'accent'], uploaded: ['In To edit', 'ok'], not_available: ['Not available', 'bad'],
};

export function chip(status, labelOverride) {
  const [label, tone] = STATUS_CHIP[status] || [status, 'muted'];
  return `<span class="chip chip--${tone}">${esc(labelOverride || label)}</span>`;
}

export function empty({ icon: ic = 'frames', title, text = '', action = '' }) {
  return `<div class="empty">${icon(ic, 'empty-ic')}<h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ''}${action}</div>`;
}

export function skeleton(rows = 3) {
  return `<div class="skel-card"><div class="skel skel--title"></div><div class="skel skel--line"></div><div class="skel skel--bar"></div></div>`
    + Array.from({ length: rows }, () => '<div class="skel-row"><div class="skel skel--line"></div><div class="skel skel--short"></div></div>').join('');
}
