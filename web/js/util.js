const P = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  filter: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  back: '<path d="m15 5-7 7 7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  left: '<path d="m15 6-6 6 6 6"/>',
  right: '<path d="m9 6 6 6-6 6"/>',
  play: '<path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.4-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z" class="f"/>',
  pause: '<rect x="6.5" y="5" width="4" height="14" rx="1.2" class="f"/><rect x="13.5" y="5" width="4" height="14" rx="1.2" class="f"/>',
  vol: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
  mute: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="m16 10 5 5M21 10l-5 5"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  info: '<path d="M8 7h12M8 12h12M8 17h8M4 7h.01M4 12h.01M4 17h.01"/>',
  retry: '<path d="M20 11.5a8 8 0 1 1-2.6-5.9M20 4v5h-5"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h7a1.5 1.5 0 0 0 1.5-1.5L18 7M9 7V4.5h6V7"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  edit: '<path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z"/>',
  reel: '<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="M3.5 8.5h17M9 3.5l2.5 5M14.5 3.5l2.5 5"/><path d="m10.5 12 4 2.5-4 2.5z" class="f"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2.5"/><path d="m16 10.5 5-3v9l-5-3z"/>',
  photo: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="m20.5 16-5-5-8.5 8.5"/>',
  carousel: '<rect x="7.5" y="3.5" width="13" height="13" rx="2.5"/><path d="M16.5 20.5h-10a3 3 0 0 1-3-3v-10"/>',
  select: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/>',
  folderPlus: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/><path d="M12 10.5v5M9.5 13h5"/>',
  grip: '<circle cx="9" cy="6" r="1.3" class="f"/><circle cx="15" cy="6" r="1.3" class="f"/><circle cx="9" cy="12" r="1.3" class="f"/><circle cx="15" cy="12" r="1.3" class="f"/><circle cx="9" cy="18" r="1.3" class="f"/><circle cx="15" cy="18" r="1.3" class="f"/>',
  copy: '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.5"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
  page: '<rect x="4" y="3.5" width="16" height="17" rx="2.5"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
  ghost: '<circle cx="12" cy="12" r="8.5"/><path d="m6 6 12 12"/>',
  alert: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2h.01"/>',
  sort: '<path d="M7 4v16M3.5 16.5 7 20l3.5-3.5M17 20V4M13.5 7.5 17 4l3.5 3.5"/>',
  logout: '<path d="M15 4h3.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H15M10 16l-4-4 4-4M6 12h10"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
};

export function icon(name, cls = '') {
  const body = (P[name] || '').replace(/class="f"/g, 'fill="currentColor" stroke="none"');
  return `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${body}</svg>`;
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

export function $(sel, root = document) { return root.querySelector(sel); }
export function $$(sel, root = document) { return [...root.querySelectorAll(sel)]; }

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

export const bus = new EventTarget();
export const emit = (type, detail) => bus.dispatchEvent(new CustomEvent(type, { detail }));
export const on = (type, fn) => {
  const h = (e) => fn(e.detail);
  bus.addEventListener(type, h);
  return () => bus.removeEventListener(type, h);
};

export function fmtTime(s) {
  if (s == null || !isFinite(s)) return '';
  s = Math.max(0, Math.floor(s));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}

export function fmtBytes(n) {
  if (n == null) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`;
}

export function fmtDate(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (isNaN(d)) return '–';
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function fmtAgo(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export const PROCESSING = new Set(['fetched', 'extracted', 'enriched']);
export const STATUSES = ['queued', 'fetched', 'extracted', 'enriched', 'indexed', 'failed', 'needs_login', 'private', 'gone'];
export const RETRYABLE = new Set(['failed', 'needs_login']);
export function statusClass(s) { return PROCESSING.has(s) ? 'st-processing' : `st-${s}`; }
export function statusLabel(s) { return PROCESSING.has(s) ? `processing · ${s}` : String(s).replace('_', ' '); }
export function statusPill(s, label) {
  return `<span class="status-pill ${statusClass(s)}">${esc(label || statusLabel(s))}</span>`;
}

export function avatarColor(name) {
  let h = 0;
  for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 65% 45%)`;
}

/* ---------------------------------------------------------------- toasts */
export function toast(msg, { error = false, ms = 2800 } = {}) {
  const root = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast${error ? ' err' : ''}`;
  el.textContent = msg;
  root.append(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 220);
  }, ms);
}
export const toastErr = (e) => toast(e?.message || String(e), { error: true, ms: 4200 });

/* ---------------------------------------------------------------- dialogs */
function openDialog(html, setup) {
  const dlg = document.getElementById('dlg');
  return new Promise((resolve) => {
    dlg.innerHTML = html;
    const form = dlg.querySelector('form');
    let result;
    const done = (v) => { result = v; dlg.close(); };
    setup?.(dlg, done);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const btn = e.submitter;
      if (btn?.value === 'cancel') return done(undefined);
      done(form.dataset.collect ? new FormData(form).get('v') : true);
    });
    dlg.addEventListener('close', () => resolve(result), { once: true });
    dlg.showModal();
    dlg.querySelector('[autofocus]')?.focus();
  });
}

export function promptDialog(title, { value = '', label = 'Name', ok = 'Save', placeholder = '' } = {}) {
  return openDialog(`<form data-collect="1"><h2>${esc(title)}</h2>
    <label class="field"><span>${esc(label)}</span><input class="input" name="v" required maxlength="80" value="${esc(value)}" placeholder="${esc(placeholder)}" autofocus autocomplete="off"></label>
    <div class="acts"><button class="btn ghost" value="cancel" formnovalidate>Cancel</button><button class="btn primary" value="ok">${esc(ok)}</button></div></form>`)
    .then((v) => (typeof v === 'string' ? v.trim() : null) || null);
}

export function confirmDialog(title, msg, { ok = 'Delete', danger = true } = {}) {
  return openDialog(`<form><h2>${esc(title)}</h2>${msg ? `<p>${esc(msg)}</p>` : ''}
    <div class="acts"><button class="btn ghost" value="cancel" autofocus>Cancel</button><button class="btn ${danger ? 'primary' : 'primary'}" value="ok">${esc(ok)}</button></div></form>`)
    .then((v) => v === true);
}

/** options: [{value, label, hint}] → resolves value or undefined */
export function pickDialog(title, options, { extra } = {}) {
  const opts = options.map((o, i) => `<button type="button" class="opt" data-i="${i}">${o.icon ? icon(o.icon, 'sm') : ''}<span>${esc(o.label)}</span>${o.hint != null ? `<span class="muted">${esc(o.hint)}</span>` : ''}</button>`).join('');
  return openDialog(`<form><h2>${esc(title)}</h2><div class="opts">${opts || '<p>Nothing here yet.</p>'}</div>
    <div class="acts">${extra ? `<button type="button" class="btn" data-extra="1">${icon('plus', 'sm')}${esc(extra)}</button>` : ''}<button class="btn ghost" value="cancel">Cancel</button></div></form>`,
  (dlg, done) => {
    dlg.querySelectorAll('.opt').forEach((b) => b.addEventListener('click', () => done(options[+b.dataset.i].value)));
    dlg.querySelector('[data-extra]')?.addEventListener('click', () => done('__extra__'));
  });
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied');
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.append(ta);
    ta.select();
    try { document.execCommand('copy'); toast('Copied'); } catch { toast('Copy failed', { error: true }); }
    ta.remove();
  }
}

/* ---------------------------------------------------------------- bottom-sheet drag-to-close */
export function sheetDrag(handle, sheet, onClose) {
  let y0 = null, dy = 0, id = null;
  handle.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    y0 = e.clientY; dy = 0; id = e.pointerId;
    handle.setPointerCapture(id);
    sheet.classList.add('dragging');
  });
  handle.addEventListener('pointermove', (e) => {
    if (y0 == null || e.pointerId !== id) return;
    dy = Math.max(0, e.clientY - y0);
    sheet.style.setProperty('--drag', `${dy}px`);
  });
  const end = (e) => {
    if (y0 == null || e.pointerId !== id) return;
    y0 = null;
    sheet.classList.remove('dragging');
    sheet.style.removeProperty('--drag');
    if (dy > 70) onClose();
  };
  handle.addEventListener('pointerup', end);
  handle.addEventListener('pointercancel', end);
}
