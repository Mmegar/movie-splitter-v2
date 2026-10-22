import { esc, icon, toast, confirmSheet, haptic } from '../ui.js';
import { laptopStatus, activeMakeRun } from '../api.js';
import { STEPS, stepIndex, STAGE_TEXT, MODE_LABELS, elapsed, relTime, clock, mergeFeed, cutPercent } from '../logic.js';
import { makePage, freshness, banner, notesHtml, skeleton, chip } from './common.js';
import { pauseRow, togglePause } from './queue.js';

export function frameStrip(done, total, current) {
  if (!total) return '';
  const cells = Array.from({ length: total }, (_, i) => {
    const n = i + 1;
    const cls = n <= done ? 'is-done' : n === current ? 'is-now' : '';
    return `<span class="frame ${cls}"></span>`;
  }).join('');
  return `<div class="frames" style="--n:${total}" role="img" aria-label="${done} of ${total} parts made">${cells}</div>`;
}

function stepper(stage) {
  const idx = stepIndex(stage);
  return `<ol class="stepper" aria-label="Run stages">${STEPS.map((s, i) => {
    const cls = i < idx ? 'is-done' : i === idx ? 'is-now' : '';
    return `<li class="${cls}" ${i === idx ? 'aria-current="step"' : ''}><span class="step-dot">${i < idx ? icon('check') : ''}</span><span class="step-label">${s.label}</span></li>`;
  }).join('')}</ol>`;
}

function runCard(st) {
  const s = st.data.state || {};
  const run = s.run || {};
  const runs = st.data.runs || [];
  const gh = activeMakeRun(runs);
  const queue = s.queue || [];

  if (run.active) {
    const movie = (s.movies || []).find((m) => m.name === run.movie);
    const modes = movie ? Object.keys(movie.modes) : [run.mode];
    const ghRun = runs.find((r) => r.id === run.run_id);
    const ended = ghRun && ghRun.status === 'completed';
    const pausing = !ended && !!((st.data.settings && st.data.settings.json) || {}).paused;
    const done = run.stage === 'rendering' ? Math.max(0, (run.part || 1) - 1) : run.stage === 'uploading' ? run.parts_total : 0;
    const partLine = run.stage === 'rendering' && run.parts_total
      ? `${run.mode === 'highlights' ? 'Highlight' : 'Part'} ${run.part} of ${run.parts_total}` : STAGE_TEXT[run.stage] || run.stage;
    return `<article class="run-card is-live" aria-live="polite">
      <div class="run-top">
        <div class="mode-track">${modes.map((m) => `<span class="mode-pill ${m === run.mode ? 'is-on' : ''}">${esc(MODE_LABELS[m] || m)}</span>`).join('')}</div>
        <span class="run-clock" title="Time since the run started">${icon('clock')}<span data-elapsed="${Date.parse(run.started)}">${elapsed(Date.now() - Date.parse(run.started))}</span></span>
      </div>
      <h2 class="run-movie">${esc(run.movie)}</h2>
      ${pausing
        ? `<p class="run-stage"><span class="live-dot"></span>Pausing: stops within about a minute</p>`
        : `<p class="run-stage"><span class="live-dot"></span>${esc(STAGE_TEXT[run.stage] || run.stage)}${run.stage === 'rendering' ? ` · <strong>${partLine}</strong>` : ''}</p>`}
      ${frameStrip(done, run.parts_total || 0, run.stage === 'rendering' ? run.part : 0)}
      ${stepper(run.stage)}
      ${ended ? `<p class="run-warn">${icon('alert')}GitHub shows this run as ${esc(ghRun.conclusion || 'ended')}. Status may be out of date.</p>` : ''}
      <div class="run-actions">
        ${movie ? `<a class="btn btn--quiet" href="#/movies/${encodeURIComponent(movie.id)}">See parts</a>` : ''}
        <button class="btn btn--stop" data-stop ${st.stopping ? 'disabled' : ''}>${icon('stop')}${st.stopping ? 'Stopping…' : 'Stop run'}</button>
      </div>
    </article>`;
  }

  const onLaptop = (((st.data.settings && st.data.settings.json) || {}).cut_on || 'laptop') === 'laptop';
  if ((st.starting && !onLaptop) || gh) {
    return `<article class="run-card">
      <div class="run-top"><span class="mode-pill is-on">Starting</span></div>
      <h2 class="run-movie">Waiting for GitHub</h2>
      <p class="run-stage"><span class="spinner"></span>${gh && gh.status === 'in_progress' ? 'The run has started and is getting the next movie.' : 'GitHub usually starts the run within a minute.'}</p>
      <div class="run-actions"><button class="btn btn--stop" data-stop ${gh ? '' : 'disabled'}>${icon('stop')}Stop run</button></div>
    </article>`;
  }

  const failed = run.stage === 'failed';
  const next = queue[0];
  return `<article class="run-card is-idle">
    <div class="run-top"><span class="mode-pill">${failed ? 'Last run failed' : 'Idle'}</span></div>
    <h2 class="run-movie">${next ? 'Ready when you are' : 'Nothing to cut'}</h2>
    <p class="run-stage">${next
      ? `Next up: <strong>${esc(next.name)}</strong>${queue.length > 1 ? ` and ${queue.length - 1} more` : ''}.`
      : 'Add a movie on the laptop or the wish list. A run also starts by itself once a day.'}</p>
    <div class="run-actions">
      <button class="btn btn--primary" data-start>${icon('play')}${onLaptop ? 'Start cutting on the laptop' : 'Start run'}</button>
    </div>
  </article>`;
}

function glanceRows(st) {
  const s = st.data.state || {};
  const laptop = st.data.laptop;
  const settings = (st.data.settings && st.data.settings.json) || {};
  const posting = s.posting || {};
  const lst = laptopStatus(laptop);
  const rows = [];

  // Laptop
  let lapDetail;
  const cut = laptop && laptop.cutting ? cutPercent(s.run) : null;
  if (!laptop) lapDetail = '<span class="muted">Not set up yet</span>';
  else if (lst.online && laptop.cutting) {
    lapDetail = `<span>Cutting ${esc(laptop.cutting.file)}</span><span class="bar"><span style="width:${cut}%"></span></span>`;
  } else if (lst.online) {
    lapDetail = laptop.uploading
      ? `<span>Sending ${esc(laptop.uploading.file)} to GitHub</span><span class="bar"><span style="width:${laptop.uploading.percent}%"></span></span>`
      : '<span class="muted">Watching the folder</span>';
  } else lapDetail = `<span class="muted">Last seen ${lst.ageMs != null ? relTime(Date.now() - lst.ageMs) : 'never'}</span>`;
  rows.push(`<a class="row" href="#/queue">
    <span class="row-ic ${lst.online ? 'tone-ok' : 'tone-muted'}">${icon('laptop')}</span>
    <span class="row-main"><span class="row-title">${esc((laptop && laptop.host) || 'Laptop')} <span class="status-dot ${lst.online ? 'on' : ''}"></span><span class="sr">${lst.online ? 'online' : 'offline'}</span></span><span class="row-sub">${lapDetail}</span></span>
    ${laptop && laptop.cutting && lst.online ? `<span class="row-val">${cut}%</span>`
      : laptop && laptop.uploading && lst.online ? `<span class="row-val">${laptop.uploading.percent}%</span>` : `<span class="row-val">${lst.online ? 'Online' : 'Offline'}</span>`}
  </a>`);

  // Posting
  const asReady = !!(settings.posting && settings.posting.enabled && settings.posting.when === 'as_ready');
  rows.push(`<label class="row row--ctl ${asReady ? 'is-on-row' : ''}" for="asready-now">
    <span class="row-ic ${asReady ? 'tone-accent' : 'tone-muted'}">${icon('send')}</span>
    <span class="row-main"><span class="row-title">Post each part as soon as it's ready</span>
      <span class="row-sub">${asReady ? 'On: every new part goes to TikTok when it reaches the laptop (one every 5 min, up to 15 a day).' : 'Off: parts go at your post times.'}</span></span>
    <input type="checkbox" class="switch" role="switch" id="asready-now" data-asready ${asReady ? 'checked' : ''}>
  </label>`);
  const enabled = posting.enabled ?? (settings.posting && settings.posting.enabled);
  const perDay = settings.posting ? settings.posting.per_day : null;
  if (enabled) {
    const nextAt = posting.next_at ? Date.parse(posting.next_at) : null;
    rows.push(`<a class="row" href="#/settings">
      <span class="row-ic tone-info">${icon('send')}</span>
      <span class="row-main"><span class="row-title">Next post</span><span class="row-sub">${asReady ? 'As soon as the next part is ready' : nextAt ? `${relTime(nextAt)}${settings.posting && settings.posting.method === 'direct' && settings.posting.privacy === 'self_only' ? ', posted privately' : ''}` : 'Nothing scheduled'}</span></span>
      <span class="row-val">${!asReady && nextAt ? clock(nextAt) : '—'}</span>
    </a>`);
  } else {
    rows.push(`<a class="row" href="#/settings">
      <span class="row-ic tone-muted">${icon('send')}</span>
      <span class="row-main"><span class="row-title">Auto-posting is off</span><span class="row-sub">Copy captions and post parts yourself, or turn it on in Settings.</span></span>
      ${icon('chevR', 'row-chev')}
    </a>`);
  }
  rows.push(`<div class="row">
    <span class="row-ic tone-ok">${icon('check')}</span>
    <span class="row-main"><span class="row-title">Sent to TikTok today</span>${enabled && perDay ? `<span class="row-sub">${perDay} a day at your post times, plus any you send yourself</span>` : ''}</span>
    <span class="row-val">${posting.posted_today ?? 0}</span>
  </div>`);
  const q = (s.queue || []).length;
  rows.push(`<a class="row" href="#/queue">
    <span class="row-ic tone-accent">${icon('inbox')}</span>
    <span class="row-main"><span class="row-title">Waiting in To edit</span></span>
    <span class="row-val">${q === 0 ? 'None' : `${q} movie${q === 1 ? '' : 's'}`}</span>${icon('chevR', 'row-chev')}
  </a>`);

  // Storage hint (Drive free tier is 15 GB)
  const done = (s.movies || []).filter((m) => m.status === 'done').length;
  if (settings.delete_originals === 'bin') {
    rows.push(`<a class="row" href="#/settings"><span class="row-ic tone-warn">${icon('cloud')}</span><span class="row-main"><span class="row-title">Deleted originals go to the Recycle Bin</span><span class="row-sub">They still use laptop space until the bin is emptied. "Delete permanently" frees it right away.</span></span>${icon('chevR', 'row-chev')}</a>`);
  } else if (Number(settings.cleanup_parts_after_days) === 0 && done >= 5) {
    rows.push(`<a class="row" href="#/settings"><span class="row-ic tone-warn">${icon('cloud')}</span><span class="row-main"><span class="row-title">${done} finished movies on the laptop</span><span class="row-sub">Old parts are kept forever. Set "Clean up old parts" to save space.</span></span>${icon('chevR', 'row-chev')}</a>`);
  }
  rows.push(pauseRow(st, 'now'));
  return `<div class="group">${rows.join('')}</div>`;
}

function pausedBanner(st) {
  const s = (st.data.settings && st.data.settings.json) || {};
  const lp = st.data.laptop || {};
  if (!s.paused && !lp.paused) return '';
  return `<div class="banner banner--warn" role="status">${icon('pause')}<div><p><strong>${s.paused ? 'Everything is paused' : esc(lp.paused)}</strong></p>
    <p class="muted">${s.paused ? 'The laptop isn\'t sending or downloading, and GitHub won\'t start cutting.' : 'The laptop carries on by itself when it\'s plugged in.'}</p></div>
    ${s.paused ? '<button class="banner-act" data-resume>Resume</button>' : ''}</div>`;
}

const TYPE_IC = { part_ready: 'frames', planned: 'scissors', started: 'play', finished: 'flag', failed: 'alert', posted: 'send', post_failed: 'alert', paused: 'pause', new_file: 'inbox', info: 'info' };
const TYPE_TONE = { part_ready: 'accent', planned: 'muted', started: 'accent', finished: 'ok', failed: 'bad', posted: 'ok', post_failed: 'bad', paused: 'muted', new_file: 'info', info: 'muted' };
export { TYPE_IC, TYPE_TONE };

function latest(st) {
  const items = mergeFeed((st.data.state || {}).events, st.data.ntfy, st.data.state).slice(0, 3);
  if (!items.length) return '';
  return `<div class="section-head"><h3>Latest</h3><a href="#/alerts" class="link">See all</a></div>
    <div class="group">${items.map((it) => `<a class="row row--alert" href="#/alerts">
      <span class="row-ic tone-${TYPE_TONE[it.type] || 'muted'}">${icon(TYPE_IC[it.type] || 'info')}</span>
      <span class="row-main"><span class="row-title clamp1">${esc(it.title)}</span><span class="row-sub">${relTime(it.at)}</span></span>
    </a>`).join('')}</div>`;
}

export function createNowView(ctx) {
  const page = makePage({ title: 'Now' });
  const { content } = page;

  content.addEventListener('change', async (e) => {
    if (e.target.matches('[data-pause]')) { togglePause(ctx, e.target); return; }
    if (e.target.matches('[data-asready]')) {
      const el = e.target, on = el.checked;
      haptic(); el.disabled = true;
      try {
        await ctx.store.setAsReady(on);
        toast(on ? 'On: each new part goes to TikTok as soon as it\'s ready' : 'Back to your post times', { kind: on ? 'ok' : 'info' });
      } catch (err) { el.checked = !on; toast(err.message || "Couldn't save that.", { kind: 'err', ms: 4500 }); }
      finally { el.disabled = false; }
    }
  });
  content.addEventListener('click', async (e) => {
    if (e.target.closest('[data-retry]')) { ctx.store.refresh(); return; }
    if (e.target.closest('[data-resume]')) {
      haptic();
      try { await ctx.store.setPaused(false); toast('Resumed'); } catch (err) { toast(err.message, { kind: 'err', ms: 4000 }); }
      return;
    }
    if (e.target.closest('[data-start]')) {
      haptic();
      const btn = e.target.closest('[data-start]');
      btn.disabled = true;
      const s = (ctx.store.state.data.settings && ctx.store.state.data.settings.json) || {};
      try {
        if ((s.cut_on || 'laptop') === 'laptop' && !ctx.store.isDemo()) {
          await ctx.store.sendCommand({ type: 'cut_now' });
          toast('Sent to the laptop. It starts the next movie within 30 seconds.');
          btn.disabled = false;
          return;
        }
        await ctx.store.startRun();
        toast(ctx.store.isDemo() ? 'Run started (demo)' : 'Run requested. GitHub usually starts it within a minute.');
      } catch (err) {
        toast(err.message, { kind: 'err', ms: 4000 });
        btn.disabled = false;
      }
      return;
    }
    if (e.target.closest('[data-stop]')) {
      const run = (ctx.store.state.data.state || {}).run || {};
      const ok = await confirmSheet({
        title: `Stop ${run.movie ? `“${run.movie}”` : 'this run'}?`,
        message: run.run_id ? 'The part being made right now is lost. Parts already sent back stay on the laptop, and the movie can be run again later.'
          : 'The part being made right now is lost. Finished parts stay in Ready to post, and the movie stays in To edit until you tap Start.',
        confirm: 'Stop run', destructive: true,
      });
      if (!ok) return;
      haptic();
      const s = (ctx.store.state.data.settings && ctx.store.state.data.settings.json) || {};
      try {
        // a cut on the laptop has no GitHub run: the laptop stops it (it checks every 30 s)
        if (!run.run_id && (run.active || (s.cut_on || 'laptop') === 'laptop') && !ctx.store.isDemo()) {
          await ctx.store.sendCommand({ type: 'stop_cut' });
          toast('Sent to the laptop. It stops cutting within 30 seconds.');
          return;
        }
        await ctx.store.stopRun();
        toast(ctx.store.isDemo() ? 'Run stopped (demo)' : 'Stop requested. GitHub stops the run in a few seconds.');
      } catch (err) { toast(err.message, { kind: 'err', ms: 4000 }); }
    }
  });

  return {
    page,
    render(st) {
      page.setSub(freshness(st));
      if (!st.data) { content.innerHTML = banner(st) + skeleton(4); return; }
      content.innerHTML = banner(st) + notesHtml(st) + pausedBanner(st) + runCard(st) + glanceRows(st) + latest(st);
    },
  };
}

export { chip };
