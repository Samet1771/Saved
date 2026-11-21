import { app, emit, pushHist, configured, EVENT_CAP } from './state.js';

let source = null;
let probeAt = 0;

export function applyStatus(status) {
  app.status = status;
  app.statusAt = Date.now();
  app.authError = false;
  if (status.system?.cpu_percent != null) pushHist(app.cpuHist, status.system.cpu_percent);
  setOnline(true);
  emit('status', status);
}

function setOnline(online) {
  if (app.online === online) return;
  app.online = online;
  if (online) app.everConnected = true;
  emit('conn');
}

function addEvent(ev) {
  if (ev.id <= app.lastEventId) return;
  app.lastEventId = ev.id;
  app.events.unshift(ev);
  if (app.events.length > EVENT_CAP) app.events.length = EVENT_CAP;
  emit('event', ev);
}

async function probe() {
  if (Date.now() - probeAt < 8000) return;
  probeAt = Date.now();
  try {
    const res = await fetch(`${app.config.server_url}/api/status`, {
      headers: { Authorization: `Bearer ${app.token}` },
      signal: AbortSignal.timeout(6000),
    });
    if (res.status === 401) {
      app.authError = true;
      emit('conn');
    } else if (res.ok) {
      applyStatus(await res.json());
    }
  } catch { /* still unreachable */ }
}

export function stopStream() {
  source?.close();
  source = null;
}

export function startStream() {
  stopStream();
  app.events = [];
  app.lastEventId = 0;
  app.status = null;
  app.authError = false;
  setOnline(false);
  emit('events-reset');
  emit('status', null);
  if (!configured()) return;
  const url = `${app.config.server_url}/api/events/stream?t=${encodeURIComponent(app.token)}`;
  source = new EventSource(url);
  source.onmessage = (m) => {
    try { addEvent(JSON.parse(m.data)); } catch { /* ignore malformed row */ }
  };
  source.addEventListener('status', (m) => {
    try { applyStatus(JSON.parse(m.data)); } catch { /* ignore malformed status */ }
  });
  source.onerror = () => { probe(); };
}

setInterval(() => {
  if (app.online && Date.now() - app.statusAt > 15000) setOnline(false);
}, 2000);
