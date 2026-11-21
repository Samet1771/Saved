import { app, on } from './state.js';
import { native } from './native.js';

const COOLDOWN = {
  offline: 10 * 60 * 1000,
  online: 10 * 60 * 1000,
  gpu: 10 * 60 * 1000,
  done: 60 * 60 * 1000,
};
const last = {};

function send(kind, title, body) {
  const now = Date.now();
  if (now - (last[kind] || 0) < COOLDOWN[kind]) return false;
  last[kind] = now;
  native.notify(title, body).catch(() => {});
  return true;
}

const remaining = (s) => Object.values(s?.progress || {}).reduce((n, p) => n + (p.remaining || 0), 0);

let toldOffline = false;
let wasRunning = null;
let lastRemaining = null;

export function initNotify() {
  on('conn', () => {
    if (!app.online && app.everConnected && !app.authError) {
      if (send('offline', 'Saved server unreachable', 'The Saved server stopped responding.')) toldOffline = true;
    } else if (app.online && toldOffline) {
      toldOffline = false;
      send('online', 'Saved server is back', 'The Saved server is reachable again.');
    }
  });

  on('llm', () => {
    const running = Boolean(app.llm?.running);
    if (wasRunning && !running && !app.llm.user_stopped) {
      send('gpu', 'GPU server stopped', 'llama-server stopped unexpectedly. Tagging will wait until it is started again.');
    }
    wasRunning = running;
  });

  on('status', (s) => {
    if (!s) return;
    const now = remaining(s);
    if (lastRemaining > 0 && now === 0) send('done', 'Backlog finished', 'Every item has been processed.');
    lastRemaining = now;
  });
}
