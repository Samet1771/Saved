import * as API from './api.js';
import { mediaUrl } from './api.js';
import { libHash } from './router.js';
import { icon, esc, toast, toastErr, promptDialog, confirmDialog } from './util.js';

let el;

export function initCollections(root) {
  el = root;
  el.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const id = t.dataset.id;
    const name = t.dataset.name;
    try {
      if (t.dataset.act === 'new') {
        const n = await promptDialog('New collection', { ok: 'Create', placeholder: 'e.g. Recipes to try' });
        if (!n) return;
        await API.post('/collections', { name: n });
        toast(`Created ${n}`);
      } else if (t.dataset.act === 'rename') {
        const n = await promptDialog('Rename collection', { value: name, ok: 'Rename' });
        if (!n || n === name) return;
        await API.patch(`/collections/${id}`, { name: n });
        toast('Renamed');
      } else if (t.dataset.act === 'delete') {
        if (!(await confirmDialog(`Delete “${name}”?`, 'The collection is removed. Its items stay in your library.'))) return;
        await API.del(`/collections/${id}`);
        toast('Collection deleted');
      } else return;
      API.invalidateCollections();
      API.invalidateFacets();
      showCollections();
    } catch (err) { toastErr(err); }
  });
}

export async function showCollections() {
  if (!el.firstChild) el.innerHTML = '<div class="page"><div class="list-foot"><div class="spinner"></div></div></div>';
  let cols;
  try { cols = await API.collections(true); } catch (e) {
    el.innerHTML = `<div class="page"><p class="error">${esc(e.message)}</p></div>`;
    return;
  }
  el.innerHTML = `<div class="page" style="max-width:1400px">
    <div class="page-head"><h1>Collections</h1>
      <button type="button" class="btn primary" data-act="new">${icon('plus', 'sm')}New collection</button></div>
    <div class="coll-grid" role="list">
      <article class="coll special" role="listitem">
        <div class="cover"><span class="ph">${icon('sort')}</span></div>
        <a class="open" href="${libHash({ sort: 'manual' })}" aria-label="All items in manual order"></a>
        <div class="body"><div class="t"><div class="name">Manual order</div><div class="cnt">All items · drag to sort</div></div></div>
      </article>
      ${cols.map((c) => `<article class="coll" role="listitem">
        <div class="cover">${c.cover_url ? `<img src="${esc(mediaUrl(c.cover_url))}" alt="" loading="lazy">` : `<span class="ph">${icon('folder')}</span>`}</div>
        <a class="open" href="${libHash({ collection: String(c.id), sort: 'manual' })}" aria-label="Open ${esc(c.name)}"></a>
        <div class="body">
          <div class="t"><div class="name">${esc(c.name)}</div><div class="cnt">${c.count} item${c.count === 1 ? '' : 's'}</div></div>
          <button type="button" class="btn ghost icon sm" data-act="rename" data-id="${c.id}" data-name="${esc(c.name)}" aria-label="Rename ${esc(c.name)}">${icon('edit', 'sm')}</button>
          <button type="button" class="btn ghost icon sm" data-act="delete" data-id="${c.id}" data-name="${esc(c.name)}" aria-label="Delete ${esc(c.name)}">${icon('trash', 'sm')}</button>
        </div>
      </article>`).join('')}
    </div>
    ${cols.length ? '' : '<p class="muted" style="margin-top:16px">No collections yet. Create one, or select items in the library and add them.</p>'}
  </div>`;
}
