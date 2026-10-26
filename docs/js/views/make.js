// Make one now: pick a movie, how to cut it and who to follow, and the laptop starts on it next.
import { esc, icon, toast, haptic } from '../ui.js';
import { laptopStatus } from '../api.js';
import { pendingCommands } from '../logic.js';
import { makePage, freshness, banner, skeleton, empty } from './common.js';

const cleanTitle = (f) => String(f || '').replace(/\.[a-z0-9]{2,4}$/i, '').replace(/[._]+/g, ' ');

export function createMakeView(ctx) {
  const page = makePage({ title: 'Make one now' });
  const { content } = page;
  let pick = null;      // { name, from }
  let style = null;     // null = the default from Settings
  let character = '';
  let sending = false;
  let lastSt = null;

  function movies(st) {
    const lp = st.data.laptop || {};
    return [
      ...(lp.queue || []).filter((f) => !f.sending).map((f) => ({ ...f, from: 'queue', where: 'In To edit' })),
      ...(lp.library || []).map((f) => ({ ...f, from: 'library', where: 'Movies folder' })),
    ];
  }

  function render(st) {
    lastSt = st;
    page.setSub(freshness(st));
    if (!st.data) { content.innerHTML = banner(st) + skeleton(3); return; }
    const s = (st.data.settings && st.data.settings.json) || {};
    const lp = st.data.laptop;
    if (!lp || (s.cut_on || 'laptop') !== 'laptop') {
      content.innerHTML = banner(st) + empty({ icon: 'laptop', title: 'Needs the laptop', text: 'Make one now starts a movie on the laptop. Set up the laptop helper, with cutting on the laptop.' });
      return;
    }
    const list = movies(st);
    if (pick && !list.some((f) => f.name === pick.name)) pick = null;
    const def = s.cut_style === 'classic' ? 'classic' : 'zoom';
    const cur = style || (pick && list.find((f) => f.name === pick.name).style) || def;
    const online = laptopStatus(lp).online;
    const waiting = pendingCommands(st.data.commands, st.data.laptop, 'make_now').pop();
    const focus = document.activeElement && document.activeElement.matches('[data-who]');
    content.innerHTML = banner(st) + `
      <h3 class="group-title">1 · Movie</h3>
      <div class="group">${list.length ? list.map((f) => `<label class="row row--ctl pick-row">
          <input type="radio" name="make-pick" value="${esc(f.name)}" data-from="${f.from}" ${pick && pick.name === f.name ? 'checked' : ''}>
          <span class="row-main"><span class="row-title clamp1">${esc(cleanTitle(f.name))}</span><span class="row-sub">${f.where}${f.character ? ` · following ${esc(f.character)}` : ''}</span></span>
        </label>`).join('') : `<div class="row"><span class="row-main"><span class="row-title">No movies on the laptop</span><span class="row-sub">Download one into your Movies folder and it shows up here.</span></span></div>`}</div>
      <h3 class="group-title">2 · How to cut it</h3>
      <div class="group"><div class="row row--stack">
        <div class="seg-sm" role="group" aria-label="Cutting style">
          <button type="button" data-style="zoom" aria-pressed="${cur === 'zoom'}">Zoomed + tracking</button>
          <button type="button" data-style="classic" aria-pressed="${cur === 'classic'}">Classic</button>
        </div>
        <p class="note">${cur === 'zoom' ? 'Zoomed in on the characters: the picture follows whoever is talking or the main person in the shot, never the background.' : 'The whole picture on a blurred background.'}</p>
      </div></div>
      <h3 class="group-title">3 · Character to follow</h3>
      <div class="group"><label class="row row--stack">
        <input class="input" data-who value="${esc(character)}" autocomplete="off" spellcheck="false" placeholder="${esc(s.character ? `Blank = ${s.character} (Memory)` : 'Blank = the main character')}">
        <span class="row-sub">For Character story and Hype edit. Type the name the way the movie says it.</span></label></div>
      <button class="btn btn--primary btn--block" data-make ${!pick || sending ? 'disabled' : ''}>${icon('play')}${sending ? 'Sending…' : 'Make it now'}</button>
      <p class="group-foot">${waiting ? `<span class="busy"><span class="spinner"></span>Waiting for the laptop to start ${esc(cleanTitle(waiting.file))}…</span>`
        : online ? 'It goes to the top of the queue and the laptop starts on it within 30 seconds (after the one it is cutting now, if any).'
          : 'The laptop is offline: it starts on it as soon as it\'s back.'}</p>`;
    if (focus) { const el = content.querySelector('[data-who]'); el.focus({ preventScroll: true }); el.setSelectionRange(el.value.length, el.value.length); }
  }

  content.addEventListener('change', (e) => {
    if (e.target.name === 'make-pick') {
      haptic();
      const f = movies(lastSt).find((x) => x.name === e.target.value);
      pick = { name: e.target.value, from: e.target.dataset.from };
      style = null;
      character = (f && f.character) || '';
      render(lastSt);
    }
  });
  content.addEventListener('input', (e) => { if (e.target.matches('[data-who]')) character = e.target.value; });
  content.addEventListener('click', async (e) => {
    if (e.target.closest('[data-retry]')) { ctx.store.refresh(); return; }
    const sb = e.target.closest('[data-style]');
    if (sb) { haptic(); style = sb.dataset.style; render(lastSt); return; }
    if (e.target.closest('[data-make]') && pick && !sending) {
      haptic();
      const s = (lastSt.data.settings && lastSt.data.settings.json) || {};
      const f = movies(lastSt).find((x) => x.name === pick.name) || {};
      sending = true;
      render(lastSt);
      try {
        await ctx.store.sendCommand({ type: 'make_now', file: pick.name, from: pick.from,
          style: style || f.style || (s.cut_style === 'classic' ? 'classic' : 'zoom'), character: character.trim() });
        toast(`Sent: ${cleanTitle(pick.name)} is next`, { kind: 'ok' });
        pick = null; style = null; character = '';
      } catch (err) { toast(err.message || "Couldn't send that.", { kind: 'err', ms: 4500 }); }
      finally { sending = false; render(lastSt); }
    }
  });

  return { page, render };
}
