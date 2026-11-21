import { app, on } from './state.js';
import { native } from './native.js';

let timer = null;
let lastTip = '';

function tooltip() {
  const hybrid = app.status?.compute?.mode === 'hybrid';
  const agent = app.status?.compute?.agent;
  const gpu = hybrid
    ? (app.compute?.running ? (agent?.online ? 'PC compute on' : 'PC compute starting') : 'PC compute off')
    : app.llm?.running ? (app.llm.healthy ? 'GPU on' : 'GPU loading') : 'GPU off';
  if (!app.config?.server_url) return `Saved: not configured · ${gpu}`;
  if (app.authError) return `Saved: signed out · ${gpu}`;
  if (!app.online) return `Saved: server unreachable · ${gpu}`;
  const w = app.status?.worker;
  const p = w && app.status.progress?.[w.stage];
  const work = p ? `${w.stage} ${p.done}/${p.total}` : 'idle';
  return `Saved: ${work} · ${gpu}`;
}

function sync() {
  timer = null;
  const tip = tooltip();
  const paused = Boolean(app.status?.controls?.worker_paused);
  const hybrid = app.status?.compute?.mode === 'hybrid';
  const running = Boolean(hybrid ? app.compute?.running : app.llm?.running);
  const key = `${tip}|${paused}|${running}|${hybrid}`;
  if (key === lastTip) return;
  lastTip = key;
  native.trayUpdate(tip, running, paused, hybrid).catch(() => {});
}

export function initTray() {
  const queue = () => { timer ||= setTimeout(sync, 800); };
  for (const t of ['status', 'conn', 'llm', 'compute', 'config']) on(t, queue);
  queue();
}
