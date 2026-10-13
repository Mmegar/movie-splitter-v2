// Small UI kit: icons, escaping, haptics, toasts, bottom sheets, pull-to-refresh, swipe-to-delete.

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const P = {
  now: '<rect x="3" y="5" width="18" height="14" rx="3.5"/><path d="M10 9.3v5.4l4.6-2.7z" fill="currentColor" stroke="none"/>',
  movies: '<rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M8 3v18M16 3v18M4 8h4M4 12h4M4 16h4M16 8h4M16 12h4M16 16h4"/>',
  alerts: '<path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2H4.5z"/><path d="M10 21h4"/>',
  queue: '<path d="M4 7h16M4 12h16M4 17h10"/><circle cx="18.5" cy="17" r="1.6" fill="currentColor" stroke="none"/>',
  settings: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2.2"/><circle cx="9" cy="17" r="2.2"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6.5A2.5 2.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 4 6.5v7A2.5 2.5 0 0 0 6.5 16H8"/>',
  check: '<path d="M5 12.5l4.2 4.2L19 7"/>',
  chevR: '<path d="M9.5 5.5L16 12l-6.5 6.5"/>',
  chevL: '<path d="M15 5L8 12l7 7"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M6 12h12"/>',
  trash: '<path d="M4.5 7h15M10 11v6M14 11v6M6.5 7l1 12.5h9l1-12.5M9.5 7V4.5h5V7"/>',
  drive: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor"/>',
  laptop: '<rect x="5" y="5" width="14" height="10" rx="1.5"/><path d="M2.5 18.5h19"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  grip: '<path d="M5 9h14M5 15h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  cloud: '<path d="M7 18.5h10a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7 9.5a4.5 4.5 0 0 0 0 9z"/>',
  offline: '<path d="M3 3l18 18M7 18.5h10M5.2 10.6A4.5 4.5 0 0 0 7 18.5M9.5 6a5.5 5.5 0 0 1 8.1 4.55 4 4 0 0 1 2.3 6.3"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l2.5 2.5M14 9l2 2"/>',
  bell: '<path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2H4.5z"/>',
  scissors: '<circle cx="6.5" cy="7" r="2.5"/><circle cx="6.5" cy="17" r="2.5"/><path d="M8.6 8.4L20 17M8.6 15.6L20 7"/>',
  frames: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M9 5v14M15 5v14"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  pause: '<path d="M9 6v12M15 6v12"/>',
  inbox: '<path d="M4 13l2.5-7h11L20 13v5.5H4z"/><path d="M4 13h4.5l1 2h5l1-2H20"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
  send: '<path d="M4 12l16-8-6 16-3-6.5z"/>',
  refresh: '<path d="M19 12a7 7 0 1 1-2.05-4.95M19 5v4h-4"/>',
};

export function icon(name, cls = '') {
  return `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${P[name] || ''}</svg>`;
}

// ---------- haptics ----------
// iOS Safari 18+ ticks the Taptic Engine when a <input switch> is toggled via its label.
let hapticLabel = null;
export function haptic() {
  try {
    if (navigator.vibrate) { navigator.vibrate(8); return; }
    if (!/iP(hone|ad|od)/.test(navigator.userAgent)) return;
    if (!hapticLabel) {
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.setAttribute('switch', '');
      input.id = 'haptic-switch';
      input.hidden = true;
      hapticLabel = document.createElement('label');
      hapticLabel.htmlFor = 'haptic-switch';
      hapticLabel.hidden = true;
      document.body.append(input, hapticLabel);
    }
    hapticLabel.click();
  } catch { /* no haptics, no problem */ }
}

// ---------- clipboard ----------
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

// ---------- toast ----------
let toastTimer = null;
export function toast(text, { kind = 'ok', ms = 2200 } = {}) {
  const host = document.getElementById('toast');
  host.innerHTML = `<div class="toast toast--${kind}" role="status">${icon(kind === 'ok' ? 'check' : kind === 'err' ? 'alert' : 'info')}<span>${esc(text)}</span></div>`;
  host.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => host.classList.remove('show'), ms);
}

// ---------- sheets ----------
const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Opens a bottom sheet. `body` is HTML; `onMount(el, close)` wires it up.
 * Resolves with whatever close(value) was called with (undefined when dismissed).
 */
export function openSheet({ title = '', body = '', onMount, className = '' } = {}) {
  return new Promise((resolve) => {
    const root = document.getElementById('sheets');
    const wrap = document.createElement('div');
    wrap.className = 'sheet-wrap';
    wrap.innerHTML = `
      <div class="sheet-backdrop" data-close></div>
      <section class="sheet ${className}" role="dialog" aria-modal="true" aria-label="${esc(title)}" tabindex="-1">
        <div class="sheet-grab" aria-hidden="true"></div>
        ${title ? `<header class="sheet-head"><h2>${esc(title)}</h2><button class="icon-btn sheet-x" data-close aria-label="Close">${icon('x')}</button></header>` : ''}
        <div class="sheet-body">${body}</div>
      </section>`;
    root.appendChild(wrap);
    const sheet = wrap.querySelector('.sheet');
    const prevFocus = document.activeElement;
    let done = false;
    const close = (value) => {
      if (done) return;
      done = true;
      wrap.classList.remove('open');
      document.removeEventListener('keydown', onKey);
      const fin = () => { wrap.remove(); if (prevFocus && prevFocus.focus) prevFocus.focus({ preventScroll: true }); resolve(value); };
      if (reduceMotion()) fin(); else setTimeout(fin, 280);
    };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    wrap.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) close(); });

    // drag the grabber/header down to dismiss
    let y0 = null, dy = 0;
    const dragStart = (e) => {
      if (!e.target.closest('.sheet-grab, .sheet-head')) return;
      y0 = e.touches[0].clientY; dy = 0; sheet.style.transition = 'none';
    };
    const dragMove = (e) => {
      if (y0 === null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      sheet.style.transform = `translateY(${dy}px)`;
    };
    const dragEnd = () => {
      if (y0 === null) return;
      sheet.style.transition = ''; sheet.style.transform = '';
      if (dy > 110) close();
      y0 = null;
    };
    sheet.addEventListener('touchstart', dragStart, { passive: true });
    sheet.addEventListener('touchmove', dragMove, { passive: true });
    sheet.addEventListener('touchend', dragEnd);

    if (onMount) onMount(sheet, close);
    requestAnimationFrame(() => {
      wrap.classList.add('open');
      const first = sheet.querySelector('[autofocus]');
      (first || sheet).focus({ preventScroll: true });
    });
  });
}

/** iOS-style action sheet confirm. Resolves true/false. */
export async function confirmSheet({ title, message, confirm = 'Confirm', destructive = false, cancel = 'Cancel' }) {
  const v = await openSheet({
    className: 'sheet--action',
    body: `
      <div class="action-card">
        <div class="action-msg"><strong>${esc(title)}</strong>${message ? `<p>${esc(message)}</p>` : ''}</div>
        <button class="action-btn ${destructive ? 'is-destructive' : ''}" data-ok>${esc(confirm)}</button>
      </div>
      <button class="action-btn action-cancel" data-close>${esc(cancel)}</button>`,
    onMount: (el, close) => el.querySelector('[data-ok]').addEventListener('click', () => close(true)),
  });
  return v === true;
}

// ---------- pull to refresh ----------
export function pullToRefresh(scroller, onRefresh) {
  const ptr = scroller.querySelector('.ptr');
  let y0 = null, dist = 0, busy = false;
  const MAX = 110, TRIGGER = 72;
  scroller.addEventListener('touchstart', (e) => {
    if (busy || scroller.scrollTop > 0 || e.touches.length !== 1) return;
    y0 = e.touches[0].clientY; dist = 0;
  }, { passive: true });
  scroller.addEventListener('touchmove', (e) => {
    if (y0 === null) return;
    dist = Math.min(MAX, Math.max(0, (e.touches[0].clientY - y0) * 0.5));
    if (scroller.scrollTop > 0) dist = 0;
    ptr.style.setProperty('--pull', dist);
    ptr.classList.toggle('armed', dist >= TRIGGER);
  }, { passive: true });
  const end = async () => {
    if (y0 === null) return;
    y0 = null;
    if (dist >= TRIGGER) {
      busy = true; haptic();
      ptr.classList.add('busy');
      ptr.style.setProperty('--pull', 56);
      try { await onRefresh(); } finally {
        busy = false; ptr.classList.remove('busy', 'armed'); ptr.style.setProperty('--pull', 0);
      }
    } else { ptr.classList.remove('armed'); ptr.style.setProperty('--pull', 0); }
  };
  scroller.addEventListener('touchend', end);
  scroller.addEventListener('touchcancel', end);
}

// ---------- swipe-to-reveal delete on list rows ----------
export function swipeRows(container, selector, onDelete) {
  let row = null, x0 = 0, y0 = 0, dx = 0, locked = null;
  const closeAll = (except) => container.querySelectorAll(`${selector}.swiped`).forEach((r) => { if (r !== except) r.classList.remove('swiped'); });
  container.addEventListener('touchstart', (e) => {
    const r = e.target.closest(selector);
    if (!r || e.target.closest('.swipe-del')) return;
    row = r; x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = 0; locked = null;
    closeAll(r);
  }, { passive: true });
  container.addEventListener('touchmove', (e) => {
    if (!row) return;
    const mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
    if (locked === null && (Math.abs(mx) > 8 || Math.abs(my) > 8)) locked = Math.abs(mx) > Math.abs(my) ? 'x' : 'y';
    if (locked !== 'x') return;
    const startOpen = row.classList.contains('swiped') ? -88 : 0;
    dx = Math.min(0, Math.max(-120, startOpen + mx));
    row.querySelector('.swipe-face').style.transform = `translateX(${dx}px)`;
  }, { passive: true });
  container.addEventListener('touchend', () => {
    if (!row) return;
    const face = row.querySelector('.swipe-face');
    face.style.transform = '';
    if (locked === 'x') row.classList.toggle('swiped', dx < -44);
    row = null;
  });
  container.addEventListener('click', (e) => {
    const del = e.target.closest('.swipe-del');
    if (del) { onDelete(del.closest(selector)); return; }
    if (!e.target.closest(`${selector}.swiped`)) closeAll();
  });
}

/** Long-press (≈500 ms) on matching elements. */
export function longPress(container, selector, fn) {
  let t = null, x = 0, y = 0;
  const cancel = () => { clearTimeout(t); t = null; };
  container.addEventListener('touchstart', (e) => {
    const el = e.target.closest(selector);
    if (!el) return;
    x = e.touches[0].clientX; y = e.touches[0].clientY;
    t = setTimeout(() => { t = null; haptic(); fn(el); }, 520);
  }, { passive: true });
  container.addEventListener('touchmove', (e) => {
    if (t && (Math.abs(e.touches[0].clientX - x) > 10 || Math.abs(e.touches[0].clientY - y) > 10)) cancel();
  }, { passive: true });
  container.addEventListener('touchend', cancel);
  container.addEventListener('contextmenu', (e) => {
    const el = e.target.closest(selector);
    if (el) { e.preventDefault(); fn(el); }
  });
}
