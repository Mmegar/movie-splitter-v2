// Insights: how the TikTok channel is doing (read by the laptop, explained by Claude), and which genres,
// movies and cast do best, colour-coded so it's easy to see what to download next.
import { esc, icon, toast, haptic } from '../ui.js';
import { relTime } from '../logic.js';
import { makePage, freshness, banner, skeleton, empty } from './common.js';

const GENRE_COLORS = {
  mystery: '#7c3aed', thriller: '#dc2626', crime: '#b45309', horror: '#be123c', drama: '#2563eb',
  action: '#ea580c', comedy: '#ca8a04', 'sci-fi': '#0891b2', 'science fiction': '#0891b2', romance: '#db2777',
  fantasy: '#16a34a', animation: '#65a30d', adventure: '#0d9488', documentary: '#64748b', war: '#78716c',
  western: '#a16207', family: '#84cc16', musical: '#c026d3', biography: '#475569', history: '#57534e',
  superhero: '#e11d48', psychological: '#6d28d9', suspense: '#9333ea', 'k-drama': '#f43f5e', anime: '#06b6d4',
};
const SPARE = ['#0ea5e9', '#f97316', '#8b5cf6', '#14b8a6', '#f43f5e', '#22c55e', '#eab308', '#6366f1'];
export function genreColor(g) {
  const k = String(g || '').toLowerCase().trim();
  if (GENRE_COLORS[k]) return GENRE_COLORS[k];
  let h = 0;
  for (const c of k) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return SPARE[h % SPARE.length];
}

// Performance against the channel's own average views per video.
const TIERS = [
  [1.5, 'hot', 'Doing great', 'Great'], [1, 'good', 'Above average', 'Good'], [0.5, 'ok', 'Below average', 'Low'], [-1, 'cold', 'Weak', 'Weak'],
];
function tierOf(ratio) { return TIERS.find(([min]) => ratio >= min) || TIERS[TIERS.length - 1]; }

const fmt = (n) => {
  if (n == null || Number.isNaN(n)) return '–';
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
  return String(Math.round(n));
};
const sec = (v) => (v == null ? '–' : `${Number(v).toFixed(1)}s`);
const pct = (v) => (v == null ? '–' : `${Number(v).toFixed(1)}%`);
const LS_SORT = 'ms.insights.sort';
function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } }

const SORTS = [['avg', 'Views per part'], ['views', 'Total views'], ['watch', 'Watch time'], ['full', 'Watched to end'], ['name', 'A–Z']];

export function createInsightsView(ctx) {
  const page = makePage({ title: 'Insights' });
  const { content } = page;
  let sort = lsGet(LS_SORT) || 'avg';
  let lastSt = null;

  function movieRows(ins) {
    const movies = Object.entries(ins.movies || {}).map(([name, m]) => ({ name, ...m, avg: m.videos ? m.views / m.videos : 0 }));
    const total = ins.totals || {};
    const base = total.videos ? total.views / total.videos : 0;
    return { movies, base };
  }

  function kpis(t) {
    const eng = t.views ? ((t.likes + t.shares + t.comments + (t.saves || 0)) / t.views) * 100 : null;
    const tile = (label, val, tone, hint) => `<div class="kpi kpi--${tone}"><span class="kpi-val">${val}</span><span class="kpi-label">${esc(label)}</span>${hint ? `<span class="kpi-hint">${esc(hint)}</span>` : ''}</div>`;
    return `<div class="kpis">
      ${tile('Views', fmt(t.views), 'blue', `${t.videos || 0} videos`)}
      ${tile('Avg watch time', sec(t.avg_watch), 'violet', 'per view')}
      ${tile('Watched to end', pct(t.full_watch), 'green', 'retention')}
      ${tile('Engagement', eng == null ? '–' : `${eng.toFixed(1)}%`, 'amber', 'likes, comments, shares, saves')}
    </div>`;
  }

  function aiCard(ai, ins) {
    if (!ai) return '';
    const list = (items, tone, title) => (items && items.length ? `<div class="ai-list ai-list--${tone}"><h4>${esc(title)}</h4><ul>${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '');
    const tuned = (ins.tuning || []).slice(-3).reverse();
    return `<div class="group ai-card">
      <div class="ai-head">${icon('memory')}<span>Claude's read</span>${ai.at ? `<span class="muted" data-rel="${Date.parse(ai.at)}">${esc(relTime(Date.parse(ai.at)))}</span>` : ''}</div>
      ${ai.summary ? `<p class="ai-summary">${esc(ai.summary)}</p>` : ''}
      ${ai.error ? `<p class="note note--warn">Claude couldn't look this time: ${esc(ai.error)}</p>` : ''}
      ${list(ai.working, 'good', 'Working')}${list(ai.not_working, 'bad', 'Not working')}${list(ai.actions, 'info', 'Do next')}
      ${tuned.length ? `<div class="ai-list ai-list--tune"><h4>Changed by itself</h4><ul>${tuned.map((t) => `<li><strong>${esc(Object.entries(t.changes).map(([k, v]) => `${k.replace(/_/g, ' ')} → ${typeof v === 'string' && v.length > 40 ? `${v.slice(0, 40)}…` : v}`).join(', '))}</strong>${t.why ? `<br><span class="muted">${esc(t.why)}</span>` : ''}</li>`).join('')}</ul></div>` : ''}
    </div>`;
  }

  function genresCard(movies, base) {
    const g = {};
    for (const m of movies) {
      for (const name of (m.genres && m.genres.length ? m.genres : ['Unknown'])) {
        const e = g[name] || (g[name] = { name, views: 0, videos: 0, movies: 0, watch: [] });
        e.views += m.views; e.videos += m.videos; e.movies += 1;
        if (m.avg_watch != null) e.watch.push(m.avg_watch);
      }
    }
    const rows = Object.values(g).map((e) => ({ ...e, avg: e.videos ? e.views / e.videos : 0 })).sort((a, b) => b.avg - a.avg);
    if (!rows.length) return '';
    const max = Math.max(...rows.map((r) => r.avg), 1);
    return `<h3 class="group-title">Genres</h3><div class="group genre-card">${rows.map((r) => {
      const [, tier] = tierOf(base ? r.avg / base : 0);
      const w = r.watch.length ? r.watch.reduce((a, b) => a + b, 0) / r.watch.length : null;
      return `<div class="genre-row">
        <span class="genre-name"><span class="gdot" style="background:${genreColor(r.name)}"></span>${esc(r.name)}</span>
        <span class="genre-bar"><span style="width:${Math.max(4, (r.avg / max) * 100)}%;background:${genreColor(r.name)}"></span></span>
        <span class="genre-val tier-${tier}">${fmt(r.avg)}</span>
        <span class="genre-sub">${r.movies} movie${r.movies === 1 ? '' : 's'} · ${r.videos} parts${w != null ? ` · ${sec(w)} watch` : ''}</span>
      </div>`;
    }).join('')}</div><p class="group-foot">Average views per part, by genre. The longest bar is the genre to download more of.</p>`;
  }

  function moviesCard(movies, base) {
    if (!movies.length) return '';
    const key = { avg: (m) => -m.avg, views: (m) => -m.views, watch: (m) => -(m.avg_watch ?? -1), full: (m) => -(m.full_watch ?? -1), name: (m) => m.name.toLowerCase() }[sort] || ((m) => -m.avg);
    const sorted = movies.slice().sort((a, b) => { const x = key(a), y = key(b); return x < y ? -1 : x > y ? 1 : 0; });
    return `<div class="section-head"><h3>Movies</h3></div>
      <div class="sort-chips" role="group" aria-label="Sort movies">${SORTS.map(([k, l]) => `<button type="button" data-sort="${k}" aria-pressed="${sort === k}">${esc(l)}</button>`).join('')}</div>
      <div class="legend">${TIERS.map(([, t, l]) => `<span><span class="tdot tier-bg-${t}"></span>${esc(l)}</span>`).join('')}</div>
      <div class="group">${sorted.map((m) => {
        const [, tier, label, short] = tierOf(base ? m.avg / base : 0);
        return `<div class="row movie-perf tier-edge-${tier}">
          <span class="row-main">
            <span class="row-title">${esc(m.name)}${m.year ? ` <span class="muted">(${esc(m.year)})</span>` : ''}</span>
            <span class="genre-chips">${(m.genres || []).map((g) => `<span class="gchip" style="--g:${genreColor(g)}">${esc(g)}</span>`).join('')}</span>
            ${m.cast && m.cast.length ? `<span class="row-sub clamp1">${esc(m.cast.join(', '))}</span>` : ''}
            <span class="perf-stats"><span>${fmt(m.views)} views</span><span>${fmt(m.avg)}/part</span><span>${sec(m.avg_watch)} watch</span><span>${pct(m.full_watch)} to end</span>${m.best_part ? `<span>best: Part ${esc(m.best_part[0])}</span>` : ''}</span>
          </span>
          <span class="perf-badge tier-bg-${tier}" title="${esc(label)}">${esc(short)}</span>
        </div>`;
      }).join('')}</div>`;
  }

  function castCard(movies, base) {
    const c = {};
    for (const m of movies) for (const a of m.cast || []) {
      const e = c[a] || (c[a] = { name: a, views: 0, videos: 0, movies: [] });
      e.views += m.views; e.videos += m.videos; e.movies.push(m.name);
    }
    const rows = Object.values(c).map((e) => ({ ...e, avg: e.videos ? e.views / e.videos : 0 })).sort((a, b) => b.avg - a.avg).slice(0, 12);
    if (!rows.length) return '';
    return `<h3 class="group-title">Cast</h3><div class="group">${rows.map((r) => {
      const [, tier] = tierOf(base ? r.avg / base : 0);
      return `<div class="row"><span class="tdot tier-bg-${tier}"></span><span class="row-main"><span class="row-title">${esc(r.name)}</span><span class="row-sub clamp1">${esc(r.movies.join(', '))}</span></span><span class="row-val">${fmt(r.avg)}/part</span></div>`;
    }).join('')}</div><p class="group-foot">Actors whose movies get the most views per part on your channel.</p>`;
  }

  function nextCard(ai) {
    const n = (ai && ai.next_movies) || [];
    if (!n.length) return '';
    return `<h3 class="group-title">Download next</h3><div class="group">${n.map((x) => `<div class="row"><span class="row-ic tone-ok">${icon('download')}</span><span class="row-main"><span class="row-title">${esc(x.title || '')}</span><span class="row-sub">${esc(x.why || '')}</span></span></div>`).join('')}</div>`;
  }

  function render(st) {
    lastSt = st;
    page.setSub(freshness(st));
    if (!st.data) { content.innerHTML = banner(st) + skeleton(3); return; }
    const ins = st.data.insights;
    const refresh = `<button class="btn btn--quiet btn--block" data-insights-now>${icon('refresh')}Read my TikTok numbers now</button>`;
    if (!ins) {
      content.innerHTML = banner(st) + empty({ icon: 'insights', title: 'No numbers yet',
        text: 'Once the laptop is logged in to TikTok (Settings › TikTok › Log in) and your username is set, it reads your views and watch time every few hours and Claude explains what works.', action: refresh });
      return;
    }
    const { movies, base } = movieRows(ins);
    content.innerHTML = banner(st)
      + `<p class="group-foot" style="margin:0 0 8px">Updated <span data-rel="${Date.parse(ins.updated)}">${esc(relTime(Date.parse(ins.updated)))}</span> from ${ins.totals.videos || 0} TikTok videos.</p>`
      + kpis(ins.totals || {}) + aiCard(ins.ai, ins) + genresCard(movies, base) + moviesCard(movies, base)
      + castCard(movies, base) + nextCard(ins.ai) + refresh;
  }

  content.addEventListener('click', async (e) => {
    if (e.target.closest('[data-retry]')) { ctx.store.refresh(); return; }
    const s = e.target.closest('[data-sort]');
    if (s) { haptic(); sort = s.dataset.sort; lsSet(LS_SORT, sort); render(lastSt); return; }
    if (e.target.closest('[data-insights-now]')) {
      haptic();
      try {
        await ctx.store.sendCommand({ type: 'insights_now' });
        toast('The laptop is reading your TikTok numbers. Give it a few minutes.', { kind: 'ok', ms: 4500 });
      } catch (err) { toast(err.message || "Couldn't reach the laptop.", { kind: 'err', ms: 4500 }); }
    }
  });

  return { page, render };
}
