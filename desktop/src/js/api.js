import { app, emit } from './state.js';

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(base, token, method, path, body) {
  let res;
  try {
    res = await fetch(`${base}/api${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    throw new ApiError(0, 'Server unreachable');
  }
  if (res.status === 401) {
    if (base === app.config?.server_url) {
      app.authError = true;
      emit('conn');
    }
    throw new ApiError(401, 'Signed out. Sign in again in Settings.');
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') detail = j.detail;
      else if (j.detail) detail = j.detail.map?.((d) => d.msg).join('; ') || detail;
    } catch { /* body was not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

const call = (method, path, body) => {
  if (!app.config?.server_url || !app.token) throw new ApiError(0, 'Not signed in');
  return request(app.config.server_url, app.token, method, path, body);
};

const base = (url) => url.trim().replace(/\/+$/, '');

export async function login(url, username, password) {
  let res;
  try {
    res = await fetch(`${base(url)}/api/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, label: 'Saved desktop' }),
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new ApiError(0, 'Server unreachable');
  }
  let j = {};
  try { j = await res.json(); } catch { /* body was not JSON */ }
  if (!res.ok) throw new ApiError(res.status, typeof j.detail === 'string' ? j.detail : `HTTP ${res.status}`);
  return j;
}

export const logout = () => call('POST', '/logout').catch(() => {});
export const testConnection = (url, token) => request(base(url), token.trim(), 'GET', '/status');
export const getItem = (id) => call('GET', `/items/${id}`);
export const putWorker = (body) => call('PUT', '/worker', body);
export const retryFailed = (includeSkipped) => call('POST', '/items/retry-failed', { include_skipped: includeSkipped });
export const retryItem = (id) => call('POST', `/items/${id}/retry`);
export const reenrich = () => call('POST', '/items/reenrich', { only_other_models: true });
