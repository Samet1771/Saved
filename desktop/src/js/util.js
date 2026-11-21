const SVG = 'http://www.w3.org/2000/svg';

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') Object.assign(el.style, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k in el && k !== 'list') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  el.append(...kids.flat().filter((c) => c != null && c !== false));
  return el;
}

export function svg(tag, attrs) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

const ICONS = {
  dashboard: '<rect x="4" y="4" width="7" height="9" rx="1.5"/><rect x="13" y="4" width="7" height="5" rx="1.5"/><rect x="13" y="11" width="7" height="9" rx="1.5"/><rect x="4" y="15" width="7" height="5" rx="1.5"/>',
  library: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  activity: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
};

export function icon(name) {
  const s = svg('svg', { class: 'i', viewBox: '0 0 24 24', 'aria-hidden': 'true' });
  s.innerHTML = ICONS[name];
  return s;
}

export function fmtBytes(n) {
  if (n == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}

export function fmtDuration(sec) {
  if (sec == null) return '—';
  sec = Math.round(sec);
  const d = Math.floor(sec / 86400);
  const hr = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d) return `${d} d ${hr} h`;
  if (hr) return `${hr} h ${m} min`;
  if (m) return `${m} min`;
  return `${sec} s`;
}

export function ago(ms) {
  if (!ms) return '—';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 5) return 'just now';
  return `${fmtDuration(s)} ago`;
}

export const sinceIso = (iso) => (iso ? ago(Date.parse(iso)) : '—');
export const pct = (a, b) => (b ? Math.min(100, Math.max(0, (a / b) * 100)) : 0);
export const clock = (ms) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

export function bar(value, tone) {
  const fill = h('i');
  const el = h('div', { class: 'bar' }, fill);
  setBar(el, value, tone);
  return el;
}

export function setBar(el, value, tone) {
  el.firstChild.style.width = `${value}%`;
  el.className = `bar ${tone || ''}`.trim();
}

export function meter(label) {
  const val = h('span', { class: 'num' });
  const b = bar(0);
  const el = h('div', { class: 'meter' }, h('div', { class: 'meter-top' }, h('span', null, label), val), b);
  return {
    el,
    set(text, value, tone) {
      val.textContent = text;
      setBar(b, value, tone);
    },
  };
}

export function sparkline(tone) {
  const line = svg('polyline', { points: '' });
  const base = svg('line', { class: 'base', x1: 0, x2: 100, y1: 29.5, y2: 29.5 });
  const el = svg('svg', { class: `spark ${tone || ''}`.trim(), viewBox: '0 0 100 30', preserveAspectRatio: 'none', 'aria-hidden': 'true' });
  el.append(base, line);
  return {
    el,
    set(points, windowMs, max = 100) {
      const now = Date.now();
      line.setAttribute(
        'points',
        points.map((p) => `${(100 - ((now - p.t) / windowMs) * 100).toFixed(2)},${(29 - (Math.min(p.v, max) / max) * 28).toFixed(2)}`).join(' '),
      );
    },
  };
}

export function toast(message, kind) {
  const t = h('div', { class: `toast ${kind || ''}`.trim(), role: kind === 'err' ? 'alert' : 'status' }, message);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), kind === 'err' ? 7000 : 3800);
}

export function confirmDialog(title, text, okLabel = 'Confirm') {
  return new Promise((resolve) => {
    const dlg = document.getElementById('dlg');
    const done = (v) => { dlg.close(); resolve(v); };
    dlg.replaceChildren(
      h('h2', null, title),
      h('p', { class: 'muted' }, text),
      h('div', { class: 'actions' },
        h('button', { class: 'btn ghost', onclick: () => done(false) }, 'Cancel'),
        h('button', { class: 'btn primary', onclick: () => done(true) }, okLabel)),
    );
    dlg.oncancel = () => resolve(false);
    dlg.showModal();
  });
}

export function infoDialog(title, body) {
  const dlg = document.getElementById('dlg');
  dlg.replaceChildren(
    h('h2', null, title),
    body,
    h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: () => dlg.close() }, 'Close')),
  );
  dlg.oncancel = null;
  dlg.showModal();
}

export async function guarded(buttons, fn) {
  const list = [].concat(buttons).filter(Boolean);
  list.forEach((b) => { b.disabled = true; });
  try {
    return await fn();
  } finally {
    list.forEach((b) => { b.disabled = false; });
  }
}
