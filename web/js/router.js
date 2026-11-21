import { PARAM_KEYS, paramsKey } from './store.js';

let handler = () => {};
export function setRouteHandler(fn) { handler = fn; }

export function parseHash(hash = location.hash) {
  const h = hash.replace(/^#/, '') || '/';
  const qi = h.indexOf('?');
  const path = (qi < 0 ? h : h.slice(0, qi)) || '/';
  const query = new URLSearchParams(qi < 0 ? '' : h.slice(qi + 1));
  return { path, query };
}

/** Raw (user-chosen) list params as they appear in the URL, without derived defaults. */
export function rawParams(query) {
  const p = {};
  for (const k of PARAM_KEYS) {
    const v = query.get(k);
    if (v != null && v !== '') p[k] = v;
  }
  return p;
}

export function libHash(raw) {
  const s = paramsKey(raw);
  return `#/${s ? `?${s}` : ''}`;
}

export function feedHash(raw, id) {
  const s = paramsKey(raw);
  return `#/feed?${s ? `${s}&` : ''}start=${id}`;
}

/** Navigate to a hash. replace=true swaps the history entry and routes immediately. */
export function navigate(hash, { replace = false } = {}) {
  if (hash === location.hash) return;
  if (replace) {
    history.replaceState(history.state, '', hash);
    handler();
  } else {
    location.hash = hash;
  }
}

/** Update the URL without routing (e.g. the feed's current item). */
export function silentReplace(hash) {
  history.replaceState(history.state, '', hash);
}
