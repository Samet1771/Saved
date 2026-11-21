import { app, on, emit, configured } from './state.js';
import { native } from './native.js';
import { startStream } from './stream.js';
import { startPolling } from './poll.js';
import { initNotify } from './notify.js';
import { initTray } from './tray.js';
import { initCompute, toggleCompute } from './compute.js';
import { putWorker } from './api.js';
import { applyStatus } from './stream.js';
import { h, icon, toast, ago } from './util.js';
import * as dashboard from './views/dashboard.js';
import * as library from './views/library.js';
import * as activity from './views/activity.js';
import * as settings from './views/settings.js';

const VIEWS = [
  { id: 'dashboard', label: 'Dashboard', mod: dashboard },
  { id: 'library', label: 'Library', mod: library },
  { id: 'activity', label: 'Activity', mod: activity },
  { id: 'settings', label: 'Settings', mod: settings },
];

let current = null;
let streamStartedAt = Date.now();
const railButtons = {};
const dot = h('div', { class: 'dot', role: 'img', 'aria-label': 'Server connection' });
const banner = document.getElementById('banner');

export function showView(id) {
  if (current === id) return;
  current = id;
  for (const v of VIEWS) {
    document.getElementById(`v-${v.id}`).hidden = v.id !== id;
    if (v.id === id) railButtons[v.id].setAttribute('aria-current', 'page');
    else railButtons[v.id].removeAttribute('aria-current');
  }
  VIEWS.find((v) => v.id === id).mod.onShow?.();
  if (app.config && app.config.last_view !== id) {
    app.config.last_view = id;
    native.configSet(app.config).catch(() => {});
  }
}

function buildRail() {
  const rail = document.getElementById('rail');
  rail.append(h('div', { class: 'brand-mark', 'aria-hidden': 'true' }));
  VIEWS.forEach((v, i) => {
    const b = h('button', { title: `${v.label} (Ctrl+${i + 1})`, 'aria-label': v.label, onclick: () => showView(v.id) }, icon(v.id));
    railButtons[v.id] = b;
    rail.append(b);
    if (v.id === 'activity') rail.append(h('div', { class: 'grow' }));
  });
  rail.append(dot);
}

function updateBanner() {
  let text = null;
  let tone = '';
  if (!app.config?.server_url || !app.token) {
    text = 'Set the server URL and sign in to get started.';
    tone = 'warn';
  } else if (app.authError) {
    text = 'You were signed out (401). Sign in again.';
  } else if (!app.online && (app.everConnected || Date.now() - streamStartedAt > 6000)) {
    text = app.statusAt ? `Server unreachable. Last status ${ago(app.statusAt)}.` : 'Server unreachable.';
  }
  banner.hidden = !text;
  if (text) {
    banner.className = `banner ${tone}`.trim();
    banner.replaceChildren(text, h('a', { href: '#', onclick: (e) => { e.preventDefault(); showView('settings'); } }, 'Open Settings'));
  }
  const ok = configured() && app.online && !app.authError;
  dot.className = `dot ${ok ? 'ok' : configured() ? 'err' : ''}`.trim();
  dot.title = ok ? 'Server online' : configured() ? 'Server offline' : 'Not configured';
}

async function toggleWorker() {
  if (!app.status) return toast('No status from the server yet', 'err');
  const paused = !app.status.controls.worker_paused;
  try {
    applyStatus(await putWorker({ paused }));
    toast(paused ? 'Worker paused' : 'Worker resumed', 'ok');
  } catch (e) {
    toast(e.message, 'err');
  }
}

async function boot() {
  app.config = await native.configGet();
  try {
    app.token = (await native.tokenGet()) || '';
  } catch (e) {
    toast(e.message, 'err');
  }
  buildRail();
  for (const v of VIEWS) v.mod.init(document.getElementById(`v-${v.id}`));
  initNotify();
  initTray();
  initCompute();

  on('conn', updateBanner);
  on('config', () => {
    streamStartedAt = Date.now();
    startStream();
    updateBanner();
  });
  setInterval(updateBanner, 2000);
  document.addEventListener('saved:goto', (e) => showView(e.detail));

  document.addEventListener('keydown', (e) => {
    if (!e.ctrlKey || e.altKey || e.shiftKey || e.metaKey) return;
    const v = VIEWS[Number(e.key) - 1];
    if (v) {
      e.preventDefault();
      showView(v.id);
    }
  });
  native.onTrayAction((a) => {
    if (a === 'toggle-worker') toggleWorker();
    else if (a === 'toggle-compute') toggleCompute();
  });

  const known = VIEWS.some((v) => v.id === app.config.last_view);
  showView(configured() && known ? app.config.last_view : configured() ? 'dashboard' : 'settings');
  startPolling();
  emit('config');
}

boot().catch((e) => {
  document.body.prepend(h('pre', { class: 'log' }, `Failed to start: ${e}`));
});
