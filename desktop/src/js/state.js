export const app = {
  config: null,
  token: '',
  status: null,
  statusAt: 0,
  online: false,
  authError: false,
  everConnected: false,
  llm: null,
  compute: null,
  gpu: undefined,
  events: [],
  lastEventId: 0,
  cpuHist: [],
  gpuHist: [],
};

export const HIST_MS = 10 * 60 * 1000;
export const EVENT_CAP = 2000;

const subs = {};

export function on(topic, fn) {
  (subs[topic] ||= new Set()).add(fn);
  return () => subs[topic].delete(fn);
}

export function emit(topic, payload) {
  for (const fn of subs[topic] || []) fn(payload);
}

export function pushHist(list, value) {
  const now = Date.now();
  list.push({ t: now, v: value });
  while (list.length && now - list[0].t > HIST_MS) list.shift();
}

export const configured = () => Boolean(app.config?.server_url && app.token);
