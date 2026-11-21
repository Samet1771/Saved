import * as API from './api.js';
import { esc, fmtAgo, icon } from './util.js';

const STAGES = ['fetch', 'extract', 'enrich', 'index'];
const STAGE_LABEL = { fetch: 'Downloaded', extract: 'Transcribed', enrich: 'Tagged', index: 'Indexed' };
const PENDING = ['queued', 'fetched', 'extracted', 'enriched'];
const MAX_LINES = 500;

let root = null, es = null, retryTimer = null, lastId = 0, follow = true, queue = [], flushing = false;

export function activityMarkup() {
  return `<section class="panel wide activity" id="s-activity" aria-labelledby="h-activity">
    <h2 id="h-activity">Activity<span class="live" data-conn="off"><i></i><span>offline</span></span></h2>
    <div class="act-now" aria-live="polite"></div>
    <div class="act-bars"></div>
    <div class="log-head"><h3 class="label">Live log</h3>
      <div class="seg" role="group" aria-label="Log filter">
        <button type="button" class="chip plain" data-logf="all" aria-pressed="true">All</button>
        <button type="button" class="chip plain" data-logf="err" aria-pressed="false">Errors</button></div></div>
    <div class="log-wrap">
      <ol class="log" role="log" aria-label="Live log" tabindex="0"></ol>
      <button type="button" class="btn sm primary jump" hidden>${icon('down', 'sm')}Jump to latest</button>
    </div>
  </section>`;
}

const pad = (n) => String(n).padStart(2, '0');
function hms(iso) {
  const d = new Date(iso);
  return isNaN(d) ? '--:--:--' : `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fmtEta(s) {
  if (s < 90) return 'about a minute left';
  if (s < 5400) return `about ${Math.round(s / 60)} min left`;
  if (s < 48 * 3600) return `about ${Math.round(s / 3600)} h left`;
  return `about ${Math.round(s / 86400)} days left`;
}

function fmtDur(s) {
  if (s == null) return '';
  return s < 100 ? `${s.toFixed(1)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
}

export function backoffUntil(s) {
  const t = s?.worker?.fetch_backoff_until;
  return t && new Date(t) > Date.now() ? new Date(t) : null;
}

function llmHTML(w) {
  const r = w.llm_remote || {};
  if (!r.url) return '';
  if (w.llm_backend === 'local') return `<div class="alert warn"><b>PC GPU unreachable.</b> Tagging a Siri link on the server CPU (slow); exported items wait for <code>${esc(r.url)}</code>.</div>`;
  return r.healthy
    ? `<div class="act-llm muted small">Tagging on the PC GPU <code>${esc(r.url)}</code>${r.model ? ` · ${esc(r.model)}` : ''}</div>`
    : `<div class="alert warn"><b>PC GPU unreachable</b> (<code>${esc(r.url)}</code>). Exported items wait for it; Siri links are tagged on the server CPU.</div>`;
}

export function renderActivity(s) {
  if (!root || !s) return;
  const p = s.progress || {};
  const w = s.worker || {};
  const c = s.counts || {};
  const busy = w.stage && w.stage !== 'idle';
  const remaining = PENDING.reduce((n, k) => n + (c[k] || 0), 0);
  const perHour = p.index?.done_last_hour ?? 0;
  const eta = !remaining ? 'All caught up' : s.eta_seconds == null ? 'Estimating time left…' : fmtEta(s.eta_seconds);
  const until = backoffUntil(s);
  root.querySelector('.act-now').innerHTML = `
    <div class="act-cur">${busy
      ? `<span class="status-pill st-processing">${esc(w.stage)}</span>${w.item_id ? `<a href="#/item/${+w.item_id}">${esc(w.shortcode || `#${w.item_id}`)}</a>` : ''}<span class="muted">${esc(fmtAgo(w.since))}</span>`
      : `<span class="status-pill st-private">idle</span><span class="muted">${remaining ? 'waiting for the next item' : 'nothing to do'}</span>`}</div>
    <div class="act-kpis">
      <span><b>${perHour}</b> items/h</span>
      <span><b>${p.fetch?.done_last_hour ?? 0}</b> fetched/h</span>
      <span class="eta">${esc(eta)}</span></div>
    ${llmHTML(w)}
    ${until ? `<div class="alert warn"><b>Instagram is rate-limiting.</b> Downloads paused until ${esc(until.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))}; already downloaded items keep processing.</div>` : ''}
    ${c.needs_login ? `<div class="alert warn"><b>${+c.needs_login} photo post${c.needs_login === 1 ? '' : 's'} need${c.needs_login === 1 ? 's' : ''} login.</b> Add <code>secrets/burner_cookies.txt</code> on the server; they are retried automatically.</div>` : ''}`;
  root.querySelector('.act-bars').innerHTML = STAGES.map((k) => {
    const x = p[k] || { done: 0, total: 0 };
    const pct = x.total ? Math.min(100, (x.done / x.total) * 100) : 0;
    return `<div class="bar"><div class="bar-l"><span>${STAGE_LABEL[k]}</span><span class="muted">${x.done}/${x.total}${x.avg_s != null ? ` · ${fmtDur(x.avg_s)} each` : ''}</span></div>
      <div class="meter" role="progressbar" aria-label="${STAGE_LABEL[k]}" aria-valuemin="0" aria-valuemax="${x.total}" aria-valuenow="${x.done}"><i class="m-media" style="width:${pct}%"></i></div></div>`;
  }).join('');
}

function lineHTML(e) {
  const sc = e.shortcode ? (e.item_id ? `<a class="ev-sc" href="#/item/${+e.item_id}">${esc(e.shortcode)}</a>` : `<span class="ev-sc">${esc(e.shortcode)}</span>`) : '';
  return `<time datetime="${esc(e.at)}">${hms(e.at)}</time><span class="ev-stage">${esc(e.stage || '')}</span>${sc}<span class="ev-msg">${esc(e.message)}</span>${e.duration_s != null ? `<span class="ev-dur">(${fmtDur(e.duration_s)})</span>` : ''}`;
}

function atBottom(log) { return log.scrollHeight - log.scrollTop - log.clientHeight < 24; }

function flush() {
  flushing = false;
  if (!root || !queue.length) return;
  const log = root.querySelector('.log');
  const frag = document.createDocumentFragment();
  for (const e of queue) {
    const li = document.createElement('li');
    li.className = `ev lv-${e.level === 'error' || e.level === 'warn' ? e.level : 'info'}`;
    li.innerHTML = lineHTML(e);
    frag.append(li);
  }
  queue = [];
  log.append(frag);
  while (log.childElementCount > MAX_LINES) log.firstElementChild.remove();
  if (follow) log.scrollTop = log.scrollHeight;
  else root.querySelector('.jump').hidden = false;
}

function push(e) {
  if (e.id <= lastId) return;
  lastId = e.id;
  queue.push(e);
  if (!flushing) { flushing = true; setTimeout(flush, 30); }
}

function setConn(state) {
  const el = root?.querySelector('.live');
  if (!el) return;
  el.dataset.conn = state;
  el.querySelector('span').textContent = { live: 'live', wait: 'reconnecting', off: 'offline' }[state];
}

function connect() {
  clearTimeout(retryTimer);
  const q = `t=${encodeURIComponent(API.getToken() || '')}${lastId ? `&after_id=${lastId}` : ''}`;
  es = new EventSource(`/api/events/stream?${q}`);
  setConn('wait');
  es.onopen = () => setConn('live');
  es.onmessage = (m) => { try { push(JSON.parse(m.data)); } catch { /* bad frame */ } };
  es.addEventListener('status', (m) => {
    try {
      const s = JSON.parse(m.data);
      renderActivity(s);
      updateBusy(s);
    } catch { /* bad frame */ }
  });
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      es.close();
      setConn('off');
      retryTimer = setTimeout(connect, 5000);
    } else setConn('wait');
  };
}

export function initActivity(el) {
  root = el.querySelector('#s-activity');
  const log = root.querySelector('.log');
  const jump = root.querySelector('.jump');
  log.addEventListener('scroll', () => {
    follow = atBottom(log);
    if (follow) jump.hidden = true;
  }, { passive: true });
  jump.addEventListener('click', () => {
    follow = true;
    jump.hidden = true;
    log.scrollTop = log.scrollHeight;
  });
  root.querySelector('.seg').addEventListener('click', (e) => {
    const b = e.target.closest('[data-logf]');
    if (!b) return;
    root.querySelectorAll('[data-logf]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    log.classList.toggle('only-err', b.dataset.logf === 'err');
    if (follow) log.scrollTop = log.scrollHeight;
  });
}

export function startActivity() {
  stopActivity();
  lastId = 0;
  follow = true;
  queue = [];
  root.querySelector('.log').innerHTML = '';
  root.querySelector('.jump').hidden = true;
  connect();
}

export function stopActivity() {
  clearTimeout(retryTimer);
  retryTimer = null;
  if (es) { es.close(); es = null; }
  setConn('off');
}

/* ---------------------------------------------------------------- header indicator */
let busyEl = null, busyTimer = null;

export function updateBusy(s) {
  if (!busyEl || !s) return;
  const c = s.counts || {};
  const w = s.worker || {};
  const pending = PENDING.reduce((n, k) => n + (c[k] || 0), 0);
  const on = pending > 0 || (w.stage && w.stage !== 'idle');
  busyEl.hidden = !on;
  if (!on) return;
  const x = s.progress?.index || { done: 0, total: 0 };
  const wait = !!backoffUntil(s);
  busyEl.classList.toggle('wait', wait);
  busyEl.querySelector('.busy-n').textContent = `${x.done}/${x.total}`;
  busyEl.title = wait ? 'Downloads paused by rate limiting' : `Processing: ${x.done} of ${x.total} items done`;
  busyEl.setAttribute('aria-label', `${busyEl.title}. Open status`);
}

async function pollBusy() {
  if (document.hidden || !API.getToken()) return;
  try { updateBusy(await API.get('/status')); } catch { /* offline: keep the last state */ }
}

export function initBusy() {
  busyEl = document.getElementById('busy');
  if (!busyEl || busyTimer) return;
  pollBusy();
  busyTimer = setInterval(pollBusy, 15000);
  document.addEventListener('visibilitychange', pollBusy);
}
