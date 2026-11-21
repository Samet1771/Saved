import * as API from './api.js';
import { renderDetails } from './details.js';
import { stageHTML, wireCarousel, activeVideo, loadVideo, unloadVideo } from './media.js';
import { icon, esc } from './util.js';

let el, details = null, carousel = null;

export function initItemPage(root) { el = root; }

export async function showItem(id) {
  leaveItem();
  el.innerHTML = '<div class="list-foot"><div class="spinner" aria-label="Loading"></div></div>';
  let item;
  try { item = await API.get(`/items/${id}`); } catch (e) {
    el.innerHTML = `<div class="page"><div class="empty">${icon('ghost')}<h2>Not found</h2><p>${esc(e.message)}</p><a class="btn" href="#/">Back to library</a></div></div>`;
    return;
  }
  el.innerHTML = `
    <div class="page-head" style="max-width:1400px;margin-inline:auto">
      <button type="button" class="btn ghost icon" data-act="back" aria-label="Back">${icon('back')}</button>
      <h1 style="font-size:var(--fs-xl)">@${esc(item.author || 'unknown')}</h1>
    </div>
    <div class="item-page" style="max-width:1400px;margin-inline:auto">
      <div class="item-media">${stageHTML(item, { controls: true })}</div>
      <aside class="item-side"><div class="det"></div></aside>
    </div>`;
  const media = el.querySelector('.item-media');
  const sync = () => {
    const act = activeVideo(media, carousel);
    media.querySelectorAll('video').forEach((v) => {
      if (v === act) loadVideo(v, 'metadata');
      else if (!v.paused) v.pause();
    });
    details?.rebindVideo();
  };
  carousel = wireCarousel(media, sync);
  sync();
  details = renderDetails(el.querySelector('.det'), id, {
    video: () => activeVideo(media, carousel),
    onSeek: (v) => { if (v.paused) v.play().catch(() => {}); },
    onDeleted: () => { location.hash = '#/'; },
  });
  media.addEventListener('click', async (e) => {
    if (!e.target.closest('[data-act="retry"]')) return;
    try { await API.post(`/items/${id}/retry`); showItem(id); } catch (err) { (await import('./util.js')).toastErr(err); }
  });
  el.querySelector('[data-act="back"]').addEventListener('click', () => {
    if (history.length > 1) history.back(); else location.hash = '#/';
  });
}

export function leaveItem() {
  details?.destroy();
  details = null;
  carousel?.destroy();
  carousel = null;
  el?.querySelectorAll('video').forEach(unloadVideo);
}
