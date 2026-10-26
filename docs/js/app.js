// App shell: tabs, hash router, live tickers, service worker.
import { createStore } from './store.js';
import { icon, pullToRefresh, haptic } from './ui.js';
import { relTime, elapsed, mergeFeed } from './logic.js';
import { createNowView } from './views/now.js';
import { createMoviesView } from './views/movies.js';
import { createAlertsView } from './views/alerts.js';
import { createQueueView } from './views/queue.js';
import { createSettingsView } from './views/settings.js';
import { createMakeView } from './views/make.js';

// Tabs in the tab bar (Dashboard, Queue, Make one now, Settings, Memory), plus pages opened from the Dashboard
// (all movies, alerts) that light up the Dashboard tab.
const TABS = ['now', 'queue', 'make', 'settings', 'memory', 'movies', 'alerts'];
const PARENT = { movies: 'now', alerts: 'now' };
const TITLES = { now: 'Dashboard', queue: 'Queue', make: 'Make one now', settings: 'Settings', memory: 'Memory', movies: 'Movies', alerts: 'Alerts' };
const LS_SEEN = 'ms.alerts.seen';

const store = createStore();
const stage = document.getElementById('stage');

/** Swipe from the left edge to go back (pushed pages), like UINavigationController. */
function edgeSwipeBack(el, onBack) {
  let x0 = null, y0 = 0, dx = 0, w = 1;
  el.addEventListener('touchstart', (e) => {
    const t = e.touches[0];
    if (t.clientX > 24) return;
    x0 = t.clientX; y0 = t.clientY; dx = 0; w = el.offsetWidth || 390;
    el.style.transition = 'none';
  }, { passive: true });
  el.addEventListener('touchmove', (e) => {
    if (x0 === null) return;
    const t = e.touches[0];
    dx = Math.max(0, t.clientX - x0);
    if (Math.abs(t.clientY - y0) > 40 && dx < 30) { x0 = null; el.style.transform = ''; el.style.transition = ''; return; }
    el.style.transform = `translateX(${dx}px)`;
  }, { passive: true });
  el.addEventListener('touchend', () => {
    if (x0 === null) return;
    x0 = null;
    el.style.transition = '';
    el.style.transform = '';
    if (dx > w * 0.33) onBack();
  });
}

const ctx = { store, edgeSwipeBack, go: (h) => { location.hash = h; } };

const views = {
  now: createNowView(ctx),
  movies: createMoviesView(ctx),
  alerts: createAlertsView(ctx),
  queue: createQueueView(ctx),
  settings: createSettingsView(ctx),
  make: createMakeView(ctx),
  memory: createSettingsView(ctx, { memory: true }),
};

// Mount each tab in its own container so pushed pages can stack inside it.
const holders = {};
for (const name of TABS) {
  const h = document.createElement('section');
  h.className = 'tabview';
  h.dataset.view = name;
  h.hidden = true;
  h.appendChild(views[name].page.el);
  stage.appendChild(h);
  holders[name] = h;
  pullToRefresh(views[name].page.scroller, () => store.refresh({ full: true }));
}

// Tab bar icons
document.querySelectorAll('[data-icon]').forEach((el) => { el.innerHTML = icon(el.dataset.icon); });

let current = null;
function route() {
  const raw = (location.hash || '#/now').replace(/^#\/?/, '');
  const parts = raw.split('/').filter(Boolean);
  const tab = TABS.includes(parts[0]) ? parts[0] : 'now';
  if (tab !== current) {
    for (const name of TABS) holders[name].hidden = name !== tab;
    document.querySelectorAll('.tab').forEach((a) => {
      const on = a.dataset.tab === (PARENT[tab] || tab);
      a.classList.toggle('is-on', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    current = tab;
    document.title = `${TITLES[tab]} · Splitter`;
  }
  if (views[tab].route) views[tab].route(parts.slice(1));
  if (tab === 'alerts') markSeen();
}

document.querySelector('.tabbar').addEventListener('click', (e) => {
  const a = e.target.closest('.tab');
  if (!a) return;
  haptic();
  // Re-tapping the active tab pops to its root and scrolls to the top, like iOS.
  if (a.dataset.tab === (PARENT[current] || current)) {
    e.preventDefault();
    const root = `#/${a.dataset.tab}`;
    if (location.hash !== root) location.hash = root;
    else views[current].page.scroller.scrollTo({ top: 0, behavior: 'smooth' });
  }
});

// ---------- unseen alerts badge ----------
function feedOf(st) {
  if (!st.data) return [];
  return mergeFeed((st.data.state || {}).events, st.data.ntfy, st.data.state);
}
function seenAt() { try { return Number(localStorage.getItem(LS_SEEN)) || 0; } catch { return 0; } }
function markSeen() {
  const feed = feedOf(store.state);
  const newest = feed.length ? feed[0].at : Date.now();
  try { localStorage.setItem(LS_SEEN, String(Math.max(newest, seenAt()))); } catch { /* ignore */ }
  updateBadge(store.state);
}
function updateBadge(st) {
  const badge = document.querySelector('.tab-badge');
  const seen = seenAt();
  // First launch: everything counts as seen, so the badge starts clean.
  if (!seen && st.data) { markSeen(); return; }
  const n = current === 'alerts' ? 0 : feedOf(st).filter((it) => it.at > seen).length;
  badge.hidden = n === 0;
  badge.textContent = n > 9 ? '9+' : String(n);
  badge.setAttribute('aria-label', `${n} new`);
}

// ---------- render loop ----------
store.subscribe((st) => {
  for (const name of TABS) views[name].render(st);
  if (current === 'alerts' && st.data) markSeen(); else updateBadge(st);
  document.documentElement.classList.toggle('is-demo', store.isDemo());
});

// Live clocks: elapsed run time + "x min ago" labels tick without a re-render.
setInterval(() => {
  if (document.hidden) return;
  const now = Date.now();
  document.querySelectorAll('[data-elapsed]').forEach((el) => { el.textContent = elapsed(now - Number(el.dataset.elapsed)); });
  document.querySelectorAll('[data-rel]').forEach((el) => { el.textContent = relTime(Number(el.dataset.rel), now); });
}, 1000);

window.addEventListener('hashchange', route);
if (!location.hash) history.replaceState(null, '', '#/now');
route();
store.start();

// ---------- service worker (offline shell) ----------
if ('serviceWorker' in navigator && location.protocol !== 'file:') {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => { /* offline support is a bonus */ });
  });
}
