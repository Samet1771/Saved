const KEY = 'saved.token';
let token = null;
try { token = localStorage.getItem(KEY) || localStorage.getItem('rv.token'); } catch { /* storage blocked */ }

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
  }
}

export const getToken = () => token;

export function setToken(t) {
  token = t || null;
  try {
    if (token) localStorage.setItem(KEY, token);
    else localStorage.removeItem(KEY);
  } catch { /* storage blocked: keep it in memory */ }
}

export function mediaUrl(path) {
  if (!path) return null;
  return `${path}${path.includes('?') ? '&' : '?'}t=${encodeURIComponent(token || '')}`;
}

export async function api(method, path, body, { tokenOverride, signal } = {}) {
  const headers = { Authorization: `Bearer ${tokenOverride ?? token ?? ''}` };
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: payload, signal });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, 'Server unreachable');
  }
  if (res.status === 401 && tokenOverride === undefined) {
    window.dispatchEvent(new CustomEvent('saved:auth'));
  }
  if (res.status === 204) return null;
  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
    throw new ApiError(res.status, typeof detail === 'string' ? detail : `HTTP ${res.status}`);
  }
  return data;
}

export const get = (p, o) => api('GET', p, undefined, o);
export const post = (p, b, o) => api('POST', p, b, o);
export const patch = (p, b) => api('PATCH', p, b);
export const put = (p, b) => api('PUT', p, b);
export const del = (p) => api('DELETE', p);

/* small caches shared by screens */
let collectionsP = null;
export function collections(force = false) {
  if (force || !collectionsP) collectionsP = get('/collections').catch((e) => { collectionsP = null; throw e; });
  return collectionsP;
}
export function invalidateCollections() { collectionsP = null; }

let categoriesP = null;
export function categories(force = false) {
  if (force || !categoriesP) categoriesP = get('/categories').catch((e) => { categoriesP = null; throw e; });
  return categoriesP;
}
export function setCategoriesCache(data) { categoriesP = Promise.resolve(data); }

let facetsP = null;
export function facets(force = false) {
  if (force || !facetsP) facetsP = get('/facets').catch((e) => { facetsP = null; throw e; });
  return facetsP;
}
export function invalidateFacets() { facetsP = null; }
