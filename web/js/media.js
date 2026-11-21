import { mediaUrl } from './api.js';
import { esc, icon, statusPill, RETRYABLE } from './util.js';

const STATUS_TEXT = {
  queued: 'Waiting to be downloaded.',
  fetched: 'Downloaded, being processed.',
  extracted: 'Transcribed, being tagged.',
  enriched: 'Tagged, being indexed.',
  failed: 'Processing failed.',
  private: 'This post is from a private account and is skipped.',
  gone: 'This post was deleted on Instagram before it was archived.',
  needs_login: 'Instagram only shows this photo post to logged-in users. Add burner cookies on the server and it is retried automatically.',
};

function one(m, item, controls) {
  const url = esc(mediaUrl(m.url));
  const portrait = m.height && m.width ? m.height / m.width > 1.2 : true;
  if (m.kind === 'video') {
    const poster = item.thumb_url && item.media.length === 1 ? ` poster="${esc(mediaUrl(item.thumb_url))}"` : '';
    return `<video class="media${portrait ? ' portrait' : ''}" data-src="${url}" playsinline webkit-playsinline muted loop preload="none"${poster}${controls ? ' controls' : ''} disablepictureinpicture></video>`;
  }
  return `<img class="media${portrait ? ' portrait' : ''}" src="${url}" alt="" decoding="async" draggable="false">`;
}

/** HTML for the media area of an item (single media, carousel, or a status card). */
export function stageHTML(item, { controls = false } = {}) {
  const media = item.media || [];
  const bg = item.thumb_url ? `<img class="bg" src="${esc(mediaUrl(item.thumb_url))}" alt="" aria-hidden="true" draggable="false">` : '';
  if (!media.length) {
    return `<div class="stage">${bg}<div class="status-card">${statusPill(item.status)}
      <p>${esc(STATUS_TEXT[item.status] || 'No media stored for this item.')}</p>
      ${RETRYABLE.has(item.status) ? `<button class="btn sm primary" data-act="retry">${icon('retry', 'sm')}Retry</button>` : ''}
      <a class="btn sm" href="${esc(/^https?:\/\//.test(item.url || '') ? item.url : '#')}" target="_blank" rel="noopener noreferrer">${icon('ext', 'sm')}Open on Instagram</a></div></div>`;
  }
  if (media.length === 1) return `<div class="stage">${bg}${one(media[0], item, controls)}</div>`;
  return `<div class="stage">${bg}
    <div class="carousel" tabindex="-1" aria-label="Carousel, ${media.length} slides">
      ${media.map((m, i) => `<div class="cslide" data-ci="${i}" aria-label="Slide ${i + 1} of ${media.length}">${one(m, item, controls)}</div>`).join('')}
    </div>
    <button class="c-nav prev" data-cnav="-1" aria-label="Previous slide" hidden>${icon('left')}</button>
    <button class="c-nav next" data-cnav="1" aria-label="Next slide">${icon('right')}</button>
    <span class="c-count" aria-hidden="true">1/${media.length}</span>
    <div class="dots" aria-hidden="true">${media.map((_, i) => `<i${i ? '' : ' class="on"'}></i>`).join('')}</div>
  </div>`;
}

/** Tracks the visible carousel slide; calls onChange(index). Returns {destroy, index, go}. */
export function wireCarousel(root, onChange) {
  const sc = root.querySelector('.carousel');
  if (!sc) return null;
  const dots = [...root.querySelectorAll('.dots i')];
  const count = root.querySelector('.c-count');
  const prev = root.querySelector('.c-nav.prev');
  const next = root.querySelector('.c-nav.next');
  const n = sc.children.length;
  const state = { index: 0 };
  const set = (i) => {
    if (i === state.index) return;
    state.index = i;
    dots.forEach((d, k) => d.classList.toggle('on', k === i));
    if (count) count.textContent = `${i + 1}/${n}`;
    if (prev) prev.hidden = i === 0;
    if (next) next.hidden = i === n - 1;
    onChange?.(i);
  };
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting && e.intersectionRatio >= 0.6) set(+e.target.dataset.ci);
  }, { root: sc, threshold: [0.6] });
  [...sc.children].forEach((c) => io.observe(c));
  let t = 0;
  const onScroll = () => {
    clearTimeout(t);
    t = setTimeout(() => {
      const w = sc.clientWidth;
      if (w && Math.abs(sc.scrollLeft / w - Math.round(sc.scrollLeft / w)) < 0.05) set(Math.round(sc.scrollLeft / w));
    }, 140);
  };
  sc.addEventListener('scroll', onScroll, { passive: true });
  const go = (d) => {
    const i = Math.max(0, Math.min(n - 1, state.index + d));
    sc.scrollTo({ left: i * sc.clientWidth, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  };
  const onNav = (e) => {
    const b = e.target.closest('[data-cnav]');
    if (b) { e.stopPropagation(); go(+b.dataset.cnav); }
  };
  root.addEventListener('click', onNav);
  return {
    get index() { return state.index; },
    go,
    destroy() { io.disconnect(); clearTimeout(t); sc.removeEventListener('scroll', onScroll); root.removeEventListener('click', onNav); },
  };
}

/** The video that should play for the current carousel position (or the single video). */
export function activeVideo(root, carousel) {
  if (!root) return null;
  if (carousel) return root.querySelector(`.cslide[data-ci="${carousel.index}"] video`);
  return root.querySelector('.stage > video');
}

export function loadVideo(v, preload) {
  if (!v) return;
  if (v.preload !== preload) v.preload = preload;
  if (!v.getAttribute('src') && v.dataset.src && preload !== 'none') {
    v.src = v.dataset.src;
  }
}

export function unloadVideo(v) {
  if (!v || !v.getAttribute('src')) return;
  v.pause();
  v.removeAttribute('src');
  try { v.load(); } catch { /* ignore */ }
}
