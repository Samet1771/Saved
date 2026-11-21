import * as API from './api.js';
import { mediaUrl } from './api.js';
import { store, normParams, FILTER_KEYS } from './store.js';
import { navigate, libHash, feedHash } from './router.js';
import { initReorder } from './reorder.js';
import {
  icon, esc, $, $$, debounce, fmtTime, statusPill, STATUSES, statusLabel, toast, toastErr,
  pickDialog, promptDialog, sheetDrag,
} from './util.js';

const SORTS = [
  ['saved_desc', 'Newest saved'],
  ['saved_asc', 'Oldest saved'],
  ['posted_desc', 'Newest posted'],
  ['manual', 'Manual order'],
];
const TYPE_ICON = { reel: 'reel', video: 'video', photo: 'photo', carousel: 'carousel' };
const FILTER_LABEL = { category: 'Category', tag: 'Tag', collection: 'Collection', author: 'Author', type: 'Type', status: 'Status' };

let el, raw = {}, params = {}, grid, foot, sentinel, selecting = false;
const selected = new Set();
let collNames = new Map();
let reorder;

export function cardHTML(item) {
  const t = item.type;
  const m = item.media || [];
  let badge = '';
  if (t === 'carousel') badge = `${icon('carousel', 'xs')}${m.length || ''}`;
  else if (t === 'photo') badge = icon('photo', 'xs');
  else {
    const d = m[0]?.duration;
    badge = `${icon(TYPE_ICON[t] || 'reel', 'xs')}${d ? fmtTime(d) : ''}`;
  }
  const notReady = item.status !== 'indexed';
  const dim = ['failed', 'private', 'gone', 'needs_login'].includes(item.status);
  const label = `${t} by @${item.author || 'unknown'}${item.category ? `, ${item.category}` : ''}${notReady ? `, ${statusLabel(item.status)}` : ''}`;
  return `<article class="card${dim ? ' dim' : ''}${selected.has(item.id) ? ' selected' : ''}" role="listitem" data-id="${item.id}">
    <a href="${feedHash(raw, item.id)}" aria-label="${esc(label)}" draggable="false">
      ${item.thumb_url
        ? `<img class="thumb" src="${esc(mediaUrl(item.thumb_url))}" alt="" loading="lazy" decoding="async" draggable="false">`
        : `<span class="placeholder">${icon(item.status === 'private' || item.status === 'needs_login' ? 'lock' : item.status === 'gone' ? 'ghost' : item.status === 'failed' ? 'alert' : 'clock')}<span>${esc(item.status === 'queued' ? 'Not downloaded yet' : statusLabel(item.status))}</span></span>`}
      <span class="type">${badge}</span>
      ${notReady ? statusPill(item.status, item.status === 'queued' || item.status === 'failed' || item.status === 'private' || item.status === 'gone' || item.status === 'needs_login' ? statusLabel(item.status) : 'processing') : ''}
      <span class="shade"></span>
      <span class="meta">
        ${item.category ? `<span class="cat">${esc(item.category)}</span>` : ''}
        <span class="author">@${esc(item.author || 'unknown')}</span>
      </span>
      <span class="check">${icon('check')}</span>
    </a>
    <a class="info-link" href="#/item/${item.id}" aria-label="Open detail page" tabindex="-1">${icon('page', 'sm')}</a>
  </article>`;
}

function render() {
  el.innerHTML = `
  <div class="lib">
    <aside class="filters" id="filters" aria-label="Filters">
      <button type="button" class="sheet-handle" aria-label="Close filters"></button>
      <div class="filters-head"><h2>Filters</h2><button type="button" class="btn ghost sm" data-act="reset">Reset</button></div>
      <div class="grid2 fields"></div>
      <button type="button" class="btn primary done-btn" data-act="filters-close">Show results</button>
    </aside>
    <div class="scrim filters-scrim" data-act="filters-close"></div>
    <div class="lib-main">
      <div class="toolbar">
        <div class="search" role="search">
          ${icon('search')}
          <label class="sr-only" for="q">Search</label>
          <input id="q" class="input" type="search" placeholder="Search — e.g. “pasta without oven”" autocomplete="off" enterkeyhint="search" spellcheck="false">
          <button type="button" class="btn ghost icon clear" data-act="clear" aria-label="Clear search" hidden>${icon('x', 'sm')}</button>
        </div>
        <button type="button" class="btn filters-open-btn" data-act="filters-open" aria-controls="filters">${icon('filter', 'sm')}Filters<span class="badge-count" hidden></span></button>
        <label class="sr-only" for="sort">Sort</label>
        <select id="sort" class="select sort"></select>
        <button type="button" class="btn" data-act="select" aria-pressed="false" aria-label="Select items">${icon('select', 'sm')}<span>Select</span></button>
      </div>
      <div class="coll-banner" hidden></div>
      <div class="tb-row2"><span class="count" aria-live="polite"></span><div class="chips active"></div><span class="hint reorder-hint" hidden></span></div>
      <div class="grid" role="list" aria-label="Items"></div>
      <div class="list-foot"></div>
      <div class="sentinel"></div>
    </div>
  </div>
  <div class="selbar" hidden role="toolbar" aria-label="Selection">
    <span class="n"></span>
    <button type="button" class="btn sm primary" data-act="sel-add">${icon('folderPlus', 'sm')}Add to collection</button>
    <button type="button" class="btn sm" data-act="sel-remove" hidden>Remove from collection</button>
    <button type="button" class="btn sm ghost" data-act="sel-done">Done</button>
  </div>`;
  grid = $('.grid', el);
  foot = $('.list-foot', el);
  sentinel = $('.sentinel', el);
}

async function renderFilters() {
  const box = $('.fields', el);
  let f;
  try { f = await API.facets(); } catch { f = { categories: [], tags: [], collections: [], authors: [], types: [] }; }
  collNames = new Map((f.collections || []).map((c) => [String(c.id), c.name]));
  const opt = (v, label, cur) => `<option value="${esc(v)}" ${String(cur) === String(v) ? 'selected' : ''}>${esc(label)}</option>`;
  const sel = (key, items, mk) => {
    const cur = raw[key] ?? '';
    const has = items.some((x) => String(mk(x)[0]) === String(cur));
    return `<label class="field"><span>${FILTER_LABEL[key]}</span><select class="select" name="${key}">
      <option value="">All</option>
      ${cur && !has ? opt(cur, key === 'collection' ? (collNames.get(String(cur)) || `#${cur}`) : cur, cur) : ''}
      ${items.map((x) => { const [v, l] = mk(x); return opt(v, l, cur); }).join('')}
    </select></label>`;
  };
  box.innerHTML = [
    sel('category', f.categories || [], (c) => [c.name, `${c.name} (${c.count})`]),
    sel('tag', f.tags || [], (c) => [c.name, `#${c.name} (${c.count})`]),
    sel('collection', f.collections || [], (c) => [c.id, `${c.name} (${c.count})`]),
    sel('author', f.authors || [], (c) => [c.name, `@${c.name} (${c.count})`]),
    sel('type', f.types || [], (c) => [c.name, `${c.name} (${c.count})`]),
    sel('status', STATUSES.map((s) => ({ name: s })), (c) => [c.name, c.name.replace('_', ' ')]),
  ].join('');
  renderChips();
}

function renderChips() {
  const chips = $('.chips.active', el);
  const active = FILTER_KEYS.filter((k) => raw[k] && !(k === 'collection'));
  chips.innerHTML = active.map((k) => `<span class="chip"><span class="k">${FILTER_LABEL[k]}:</span>&nbsp;${esc(raw[k])}<button type="button" data-rm="${k}" aria-label="Remove ${FILTER_LABEL[k]} filter">${icon('x', 'xs')}</button></span>`).join('');
  const n = FILTER_KEYS.filter((k) => raw[k]).length;
  const b = $('.filters-open-btn .badge-count', el);
  b.hidden = !n;
  b.textContent = n;
  const banner = $('.coll-banner', el);
  if (raw.collection) {
    const name = collNames.get(String(raw.collection)) || 'Collection';
    banner.hidden = false;
    banner.innerHTML = `${icon('folder')}<div style="flex:1;min-width:0"><strong>${esc(name)}</strong><div class="muted">${params.sort === 'manual' && !params.q ? 'Manual order of this collection' : 'Collection'}</div></div>
      <button type="button" class="btn sm ghost" data-rm="collection">${icon('x', 'sm')}Close</button>`;
  } else banner.hidden = true;
}

function renderSort() {
  const s = $('#sort', el);
  s.innerHTML = params.q
    ? '<option value="relevance" selected>Relevance</option>'
    : SORTS.map(([v, l]) => `<option value="${v}" ${params.sort === v ? 'selected' : ''}>${l}</option>`).join('');
  s.disabled = !!params.q;
  s.title = params.q ? 'Search results are sorted by relevance' : '';
}

function renderFoot() {
  if (store.error) {
    foot.innerHTML = `<div><span>${esc(store.error.message)}</span> <button type="button" class="btn sm" data-act="more">${icon('retry', 'sm')}Retry</button></div>`;
  } else if (store.loading) {
    foot.innerHTML = '<div class="spinner" aria-label="Loading"></div>';
  } else if (store.total === 0) {
    foot.innerHTML = params.q
      ? `<div class="empty">${icon('search')}<h2>No matches</h2><p>Nothing found for “${esc(params.q)}”. Try other words or clear a filter.</p></div>`
      : `<div class="empty">${icon('folder')}<h2>Nothing here yet</h2><p>${FILTER_KEYS.some((k) => raw[k]) ? 'No items match these filters.' : 'Import your Instagram export on the <a href="#/settings">Status</a> page.'}</p></div>`;
  } else if (store.done && store.items.length > 8) {
    foot.innerHTML = `<span>${store.total} items · end of list</span>`;
  } else foot.innerHTML = '';
  const total = store.total;
  $('.count', el).textContent = total == null ? '' : `${total} item${total === 1 ? '' : 's'}${params.q ? ' found' : ''}`;
}

function appendCards(items) {
  grid.insertAdjacentHTML('beforeend', items.map(cardHTML).join(''));
  for (const img of $$('img.thumb:not(.loaded)', grid)) {
    if (img.complete && img.naturalWidth) img.classList.add('loaded');
    else img.addEventListener('load', () => img.classList.add('loaded'), { once: true });
  }
}

function checkSentinel() {
  if (el.hidden || store.loading || store.done || store.error) return;
  const r = sentinel.getBoundingClientRect();
  if (r.top < innerHeight + 1200) store.more();
}

function updateReorderUI() {
  const can = params.sort === 'manual' && !params.q && !selecting;
  const h = $('.reorder-hint', el);
  h.hidden = !can;
  h.innerHTML = can ? `${icon('grip', 'sm')}${matchMedia('(hover: hover)').matches ? 'Drag to reorder' : 'Long-press, then drag to reorder'}` : '';
  reorder.enabled = can;
}

/* ---------------------------------------------------------------- selection */
function setSelecting(on) {
  selecting = on;
  if (!on) {
    selected.clear();
    $$('.card.selected', grid).forEach((c) => c.classList.remove('selected'));
  }
  el.classList.toggle('selecting', on);
  grid.classList.toggle('selecting', on);
  const b = $('[data-act="select"]', el);
  b.setAttribute('aria-pressed', String(on));
  b.classList.toggle('on', on);
  b.querySelector('span').textContent = on ? 'Cancel' : 'Select';
  b.setAttribute('aria-label', on ? 'Cancel selection' : 'Select items');
  updateSelbar();
  updateReorderUI();
}

function updateSelbar() {
  const bar = $('.selbar', el);
  bar.hidden = !selecting;
  $('.n', bar).textContent = `${selected.size} selected`;
  $('[data-act="sel-add"]', bar).disabled = !selected.size;
  const rm = $('[data-act="sel-remove"]', bar);
  rm.hidden = !raw.collection;
  rm.disabled = !selected.size;
}

async function addSelectedToCollection() {
  let cols;
  try { cols = await API.collections(true); } catch (e) { return toastErr(e); }
  let cid = await pickDialog('Add to collection', cols.map((c) => ({ value: c.id, label: c.name, hint: c.count, icon: 'folder' })), { extra: 'New collection' });
  if (cid === undefined) return;
  try {
    if (cid === '__extra__') {
      const name = await promptDialog('New collection', { ok: 'Create' });
      if (!name) return;
      cid = (await API.post('/collections', { name })).id;
    }
    const c = await API.post(`/collections/${cid}/items`, { item_ids: [...selected] });
    API.invalidateCollections();
    API.invalidateFacets();
    for (const id of selected) {
      const it = store.items.find((x) => x.id === id);
      if (it && !it.collections?.some((x) => x.id === c.id)) store.update({ id, collections: [...(it.collections || []), { id: c.id, name: c.name }] });
    }
    toast(`Added ${selected.size} to ${c.name}`);
    setSelecting(false);
  } catch (e) { toastErr(e); }
}

async function removeSelectedFromCollection() {
  const cid = raw.collection;
  const ids = [...selected];
  try {
    for (const id of ids) await API.del(`/collections/${cid}/items/${id}`);
    ids.forEach((id) => store.remove(id));
    API.invalidateCollections();
    API.invalidateFacets();
    toast(`Removed ${ids.length}`);
    setSelecting(false);
  } catch (e) { toastErr(e); }
}

/* ---------------------------------------------------------------- filters sheet */
function openFilters() {
  $('#filters', el).classList.add('open');
  $('.filters-scrim', el).classList.add('open');
  $('#filters select', el)?.focus({ preventScroll: true });
}
function closeFilters() {
  $('#filters', el).classList.remove('open');
  $('.filters-scrim', el).classList.remove('open');
}

/* ---------------------------------------------------------------- hover preview */
let hoverTimer = null, hoverCard = null;
function stopPreview() {
  clearTimeout(hoverTimer);
  if (hoverCard) {
    const v = hoverCard.querySelector('video.preview');
    if (v) { v.pause(); v.removeAttribute('src'); v.load(); v.remove(); }
  }
  hoverCard = null;
}

function setParam(k, v) {
  const next = { ...raw };
  if (v) next[k] = v; else delete next[k];
  if (k === 'collection' && v && !raw.q) next.sort = 'manual';
  if (k === 'collection' && !v && next.sort === 'manual' && raw.sort === 'manual') delete next.sort;
  navigate(libHash(next), { replace: true });
}

export function initLibrary(root) {
  el = root;
  render();
  reorder = initReorder(grid, {
    onMove: async (id, afterId, toIndex) => {
      store.move(id, toIndex);
      try {
        await API.post('/order/move', { item_id: id, after_id: afterId, collection_id: raw.collection ? +raw.collection : null });
        toast('Order saved');
      } catch (e) {
        toastErr(e);
        store.setParams(params, true);
      }
    },
  });

  store.addEventListener('reset', () => { grid.innerHTML = ''; renderFoot(); });
  store.addEventListener('loading', renderFoot);
  store.addEventListener('error', renderFoot);
  store.addEventListener('append', (e) => {
    appendCards(e.detail);
    renderFoot();
    requestAnimationFrame(checkSentinel);
  });
  store.addEventListener('update', (e) => {
    const c = grid.querySelector(`.card[data-id="${e.detail.id}"]`);
    if (c) c.outerHTML = cardHTML(e.detail);
    const img = grid.querySelector(`.card[data-id="${e.detail.id}"] img.thumb`);
    if (img) img.complete ? img.classList.add('loaded') : img.addEventListener('load', () => img.classList.add('loaded'), { once: true });
  });
  store.addEventListener('remove', (e) => {
    grid.querySelector(`.card[data-id="${e.detail}"]`)?.remove();
    selected.delete(e.detail);
    renderFoot();
  });

  new IntersectionObserver((en) => { if (en[0].isIntersecting) checkSentinel(); }, { rootMargin: '1200px 0px' }).observe(sentinel);

  const q = $('#q', el);
  const clearBtn = $('[data-act="clear"]', el);
  const pushQ = debounce(() => {
    const v = q.value.trim();
    if ((raw.q || '') !== v) setParam('q', v);
  }, 350);
  q.addEventListener('input', () => { clearBtn.hidden = !q.value; pushQ(); });
  q.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); q.blur(); }
    if (e.key === 'Escape' && q.value) { q.value = ''; clearBtn.hidden = true; pushQ(); }
  });

  el.addEventListener('change', (e) => {
    const t = e.target;
    if (t.id === 'sort') return setParam('sort', t.value === 'saved_desc' ? '' : t.value);
    if (t.closest('#filters') && t.name) setParam(t.name, t.value);
  });

  el.addEventListener('click', (e) => {
    const card = e.target.closest('.card');
    if (card && selecting && !e.target.closest('.info-link')) {
      e.preventDefault();
      const id = +card.dataset.id;
      if (selected.has(id)) selected.delete(id); else selected.add(id);
      card.classList.toggle('selected', selected.has(id));
      card.querySelector('a').setAttribute('aria-pressed', String(selected.has(id)));
      updateSelbar();
      return;
    }
    const t = e.target.closest('[data-act], [data-rm]');
    if (!t) return;
    if (t.dataset.rm) return setParam(t.dataset.rm, '');
    switch (t.dataset.act) {
      case 'clear': q.value = ''; clearBtn.hidden = true; setParam('q', ''); q.focus(); break;
      case 'filters-open': openFilters(); break;
      case 'filters-close': closeFilters(); break;
      case 'reset': {
        const next = { ...raw };
        FILTER_KEYS.forEach((k) => delete next[k]);
        navigate(libHash(next), { replace: true });
        break;
      }
      case 'select': setSelecting(!selecting); break;
      case 'sel-done': setSelecting(false); break;
      case 'sel-add': addSelectedToCollection(); break;
      case 'sel-remove': removeSelectedFromCollection(); break;
      case 'more': store.error = null; store.more(); break;
      default:
    }
  });

  el.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if ($('#filters', el).classList.contains('open')) closeFilters();
      else if (selecting) setSelecting(false);
    }
  });
  sheetDrag($('#filters .sheet-handle', el), $('#filters', el), closeFilters);
  $('#filters .sheet-handle', el).addEventListener('click', closeFilters);

  grid.addEventListener('pointerover', (e) => {
    if (e.pointerType !== 'mouse' || selecting || reorder.dragging) return;
    const card = e.target.closest('.card');
    if (!card || card === hoverCard) return;
    stopPreview();
    hoverCard = card;
    hoverTimer = setTimeout(() => {
      const it = store.items.find((x) => x.id === +card.dataset.id);
      const m = it?.media?.find((x) => x.kind === 'video');
      if (!m || hoverCard !== card) return;
      const v = document.createElement('video');
      v.className = 'preview';
      v.muted = true; v.loop = true; v.playsInline = true; v.preload = 'auto';
      v.setAttribute('muted', ''); v.setAttribute('playsinline', '');
      v.src = mediaUrl(m.url);
      v.addEventListener('playing', () => v.style.opacity = '1');
      card.querySelector('a').prepend(v);
      v.play().catch(() => {});
    }, 450);
  });
  grid.addEventListener('pointerout', (e) => {
    if (!hoverCard) return;
    if (e.relatedTarget && hoverCard.contains(e.relatedTarget)) return;
    stopPreview();
  });

  addEventListener('resize', debounce(checkSentinel, 200));
  addEventListener('scroll', debounce(checkSentinel, 120), { passive: true });
}

/** Apply the URL params to the library (reloads the list only when they changed). */
export function showLibrary(rawParams) {
  raw = rawParams;
  params = normParams(raw);
  const q = $('#q', el);
  if (document.activeElement !== q) q.value = raw.q || '';
  $('[data-act="clear"]', el).hidden = !q.value;
  renderSort();
  renderFilters();
  renderChips();
  updateSelbar();
  // keep card links in sync with the current params
  const p = store.setParams(params);
  for (const a of $$('.card > a:first-child', grid)) a.setAttribute('href', feedHash(raw, +a.parentElement.dataset.id));
  updateReorderUI();
  return p;
}

export function leaveLibrary() {
  stopPreview();
  closeFilters();
}

export function revealItem(id) {
  const c = grid.querySelector(`.card[data-id="${id}"]`);
  if (!c) return;
  const r = c.getBoundingClientRect();
  if (r.top < 70 || r.bottom > innerHeight - 70) c.scrollIntoView({ block: 'center' });
  c.querySelector('a')?.focus({ preventScroll: true });
}
