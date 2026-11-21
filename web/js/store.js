import { get } from './api.js';

export const PAGE = 30;
export const FILTER_KEYS = ['category', 'tag', 'collection', 'author', 'type', 'status'];
export const PARAM_KEYS = ['q', ...FILTER_KEYS, 'sort'];

/** Canonical list params from a URLSearchParams-ish object. */
export function normParams(src) {
  const p = {};
  for (const k of PARAM_KEYS) {
    const v = (src.get ? src.get(k) : src[k]) ?? '';
    if (String(v).trim() !== '') p[k] = String(v).trim();
  }
  if (p.q) p.sort = 'relevance';
  else if (p.sort === 'relevance' || !p.sort) p.sort = 'saved_desc';
  return p;
}

export function paramsKey(p) {
  return PARAM_KEYS.filter((k) => p[k] != null).map((k) => `${k}=${encodeURIComponent(p[k])}`).join('&');
}

/** Paged list of items for the current query; shared by the grid and the feed. */
class ListStore extends EventTarget {
  constructor() {
    super();
    this.params = null;
    this.key = null;
    this.items = [];
    this.total = null;
    this.loading = false;
    this.error = null;
    this.gen = 0;
  }

  get done() { return this.total != null && this.items.length >= this.total; }

  emit(type, detail) { this.dispatchEvent(new CustomEvent(type, { detail })); }

  /** Switches to new params; no-op when unchanged unless force. Resolves after the first page. */
  setParams(params, force = false) {
    const key = paramsKey(params);
    if (!force && key === this.key && (this.items.length || this.loading || this.total != null)) {
      return this.firstPage || Promise.resolve();
    }
    this.params = params;
    this.key = key;
    this.items = [];
    this.total = null;
    this.error = null;
    this.loading = false;
    this.gen++;
    this.emit('reset');
    this.firstPage = this.more();
    return this.firstPage;
  }

  async more() {
    if (this.loading || this.done || !this.params) return this.pending;
    const gen = this.gen;
    const qs = new URLSearchParams({ ...this.params, offset: String(this.items.length), limit: String(PAGE) });
    this.loading = true;
    this.emit('loading');
    this.pending = (async () => {
      try {
        const res = await get(`/items?${qs}`);
        if (gen !== this.gen) return;
        const seen = new Set(this.items.map((i) => i.id));
        const fresh = res.items.filter((i) => !seen.has(i.id));
        this.items.push(...fresh);
        this.total = res.total;
        if (!res.items.length) this.total = this.items.length;
        this.loading = false;
        this.emit('append', fresh);
      } catch (e) {
        if (gen !== this.gen) return;
        this.loading = false;
        this.error = e;
        this.emit('error', e);
      }
    })();
    return this.pending;
  }

  indexOf(id) { return this.items.findIndex((i) => i.id === id); }

  update(item) {
    const i = this.indexOf(item.id);
    if (i < 0) return;
    const keep = this.items[i];
    for (const k of Object.keys(keep)) if (k in item) keep[k] = item[k];
    this.emit('update', keep);
  }

  remove(id) {
    const i = this.indexOf(id);
    if (i < 0) return;
    this.items.splice(i, 1);
    if (this.total != null) this.total--;
    this.emit('remove', id);
  }

  move(id, toIndex) {
    const i = this.indexOf(id);
    if (i < 0) return;
    const [it] = this.items.splice(i, 1);
    this.items.splice(toIndex, 0, it);
  }
}

export const store = new ListStore();
