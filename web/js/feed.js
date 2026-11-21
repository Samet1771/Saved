import * as API from './api.js';
import { store } from './store.js';
import { renderDetails } from './details.js';
import { stageHTML, wireCarousel, activeVideo, loadVideo, unloadVideo } from './media.js';
import { icon, esc, avatarColor, statusPill, reducedMotion, sheetDrag, toast, toastErr } from './util.js';

let soundOn = false; // becomes true after the first tap; kept for the session
let current = null;

export const feedOpen = () => !!current;
export const closeFeed = (opts) => current?.close(opts);

function overlayHTML(item) {
  const caption = (item.caption || item.description || '').replace(/\s+/g, ' ').trim();
  const tags = (item.tags || []).slice(0, 4);
  const url = /^https?:\/\//.test(item.url || '') ? item.url : '#';
  return `
    <div class="overlay">
      <div class="who"><span class="avatar" style="background:${avatarColor(item.author || '?')}" aria-hidden="true">${esc((item.author || '?')[0])}</span>@${esc(item.author || 'unknown')}</div>
      ${(item.category || tags.length || item.status !== 'indexed') ? `<div class="tagrow">
        ${item.status !== 'indexed' ? statusPill(item.status) : ''}
        ${item.category ? `<span class="catc">${esc(item.category)}</span>` : ''}
        ${tags.map((t) => `<span>#${esc(t)}</span>`).join('')}
      </div>` : ''}
      ${caption ? `<button type="button" class="cap" data-act="details">${esc(caption)}</button>` : ''}
    </div>
    <div class="rail">
      <div class="r"><button type="button" class="fbtn" data-act="details" aria-label="Details, transcript and editing">${icon('info')}</button><small aria-hidden="true">Details</small></div>
      <div class="r"><a class="fbtn" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="Open on Instagram">${icon('ext')}</a><small aria-hidden="true">Instagram</small></div>
    </div>`;
}

export async function openFeed(startId, { onClose, onIndex } = {}) {
  if (current) current.close({ silent: true });
  const root = document.getElementById('feed-root');
  root.innerHTML = `
    <div class="feed" role="dialog" aria-modal="true" aria-label="Feed">
      <div class="feed-scroller" tabindex="-1"></div>
      <div class="feed-top">
        <button type="button" class="fbtn" data-act="close" aria-label="Close feed">${icon('back')}</button>
        <span class="counter" aria-live="polite"></span>
        <button type="button" class="sound-hint" data-act="sound" hidden>${icon('mute', 'sm')}Tap for sound</button>
        <button type="button" class="fbtn" data-act="mute"></button>
      </div>
      <div class="sheet-scrim" data-act="sheet-close"></div>
      <div class="dsheet" role="dialog" aria-label="Item details" aria-modal="false">
        <div class="sheet-top">
          <button type="button" class="sheet-handle" aria-label="Close details"></button>
          <h2>Details</h2>
          <button type="button" class="btn ghost icon" data-act="sheet-close" aria-label="Close details">${icon('x')}</button>
        </div>
        <div class="sheet-body"></div>
      </div>
    </div>`;
  const feed = root.firstElementChild;
  const scroller = feed.querySelector('.feed-scroller');
  const counter = feed.querySelector('.counter');
  const muteBtn = feed.querySelector('[data-act="mute"]');
  const hint = feed.querySelector('.sound-hint');
  const sheet = feed.querySelector('.dsheet');
  const sheetBody = feed.querySelector('.sheet-body');
  const scrim = feed.querySelector('.sheet-scrim');

  const slides = [];           // parallel to store.items
  const mounted = new Map();   // slide element -> { carousel }
  let cur = -1;
  let details = null;
  let closed = false;
  const prevFocus = document.activeElement;

  document.documentElement.classList.add('locked');
  for (const id of ['view', 'topbar']) document.getElementById(id)?.setAttribute('inert', '');

  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting && e.intersectionRatio >= 0.6) setActive(slides.indexOf(e.target));
    }
  }, { root: scroller, threshold: [0.6] });

  function addSlides(items) {
    const frag = document.createDocumentFragment();
    for (const it of items) {
      const s = document.createElement('section');
      s.className = 'slide';
      s.dataset.id = it.id;
      s.setAttribute('aria-roledescription', 'slide');
      slides.push(s);
      frag.append(s);
      io.observe(s);
    }
    scroller.append(frag);
    updateCounter();
  }

  function itemOf(slide) { return store.items[slides.indexOf(slide)]; }

  function mount(slide) {
    if (mounted.has(slide)) return;
    const item = itemOf(slide);
    if (!item) return;
    slide.setAttribute('aria-label', `@${item.author || 'unknown'}`);
    slide.innerHTML = `${stageHTML(item)}${overlayHTML(item)}<div class="progress" aria-hidden="true"><i></i></div><div class="flash" aria-hidden="true"></div>`;
    const st = { carousel: null };
    st.carousel = wireCarousel(slide, () => { if (slides[cur] === slide) { applyPreload(); playActive(); } });
    const bar = slide.querySelector('.progress i');
    slide.querySelectorAll('video').forEach((v) => {
      v.addEventListener('timeupdate', () => {
        if (v === activeVideo(slide, st.carousel) && v.duration) bar.style.transform = `scaleX(${v.currentTime / v.duration})`;
      });
      v.addEventListener('error', () => { if (v.getAttribute('src')) slide.classList.add('media-error'); });
    });
    if (!slide.querySelector('video')) slide.querySelector('.progress')?.remove();
    mounted.set(slide, st);
    if (!item.caption && item.description === undefined) {
      item.description = null;
      API.get(`/items/${item.id}`).then((d) => {
        item.description = d.description;
        if (d.description) refreshOverlay(slide);
      }).catch(() => {});
    }
  }

  function unmount(slide) {
    const st = mounted.get(slide);
    if (!st) return;
    slide.querySelectorAll('video').forEach(unloadVideo);
    st.carousel?.destroy();
    slide.innerHTML = '';
    mounted.delete(slide);
  }

  function refreshOverlay(slide) {
    const item = itemOf(slide);
    if (!item || !mounted.has(slide)) return;
    slide.querySelector('.overlay')?.remove();
    slide.querySelector('.rail')?.remove();
    slide.querySelector('.stage').insertAdjacentHTML('afterend', overlayHTML(item));
  }

  function applyPreload() {
    mounted.forEach((st, slide) => {
      const d = slides.indexOf(slide) - cur;
      const active = activeVideo(slide, st.carousel);
      slide.querySelectorAll('video').forEach((v) => {
        let p = 'none';
        if (d === 0) p = v === active ? 'auto' : 'metadata';
        else if (d === 1) p = v === active ? 'auto' : 'none';
        else if (d === -1) p = v === active ? 'metadata' : 'none';
        if (p === 'none') unloadVideo(v);
        loadVideo(v, p);
      });
    });
  }

  function setMuted(v, m) {
    v.muted = m;
    if (m) v.setAttribute('muted', ''); else v.removeAttribute('muted');
  }

  function updateMuteBtn() {
    muteBtn.innerHTML = icon(soundOn ? 'vol' : 'mute');
    muteBtn.setAttribute('aria-label', soundOn ? 'Mute' : 'Unmute');
    muteBtn.setAttribute('aria-pressed', String(!soundOn));
    hint.hidden = soundOn || !activeVideo(slides[cur], mounted.get(slides[cur])?.carousel);
  }

  function playActive() {
    const slide = slides[cur];
    const st = mounted.get(slide);
    const v = st ? activeVideo(slide, st.carousel) : null;
    mounted.forEach((_, s) => s.querySelectorAll('video').forEach((x) => { if (x !== v && !x.paused) x.pause(); }));
    slide?.querySelector('.flash')?.classList.remove('hold', 'show');
    updateMuteBtn();
    if (!v) return;
    loadVideo(v, 'auto');
    setMuted(v, !soundOn);
    v.play().catch((err) => {
      if (err.name === 'NotAllowedError' && !v.muted) {
        // Sound autoplay blocked (e.g. iOS per-element policy): fall back to muted, ask for a tap.
        setMuted(v, true);
        hint.hidden = false;
        v.play().catch(() => {});
      }
    });
    details?.rebindVideo();
  }

  function setActive(i) {
    if (i < 0 || i === cur || closed) return;
    cur = i;
    for (const [slide] of mounted) {
      const d = Math.abs(slides.indexOf(slide) - cur);
      if (d > 2) unmount(slide);
    }
    for (let k = cur - 2; k <= cur + 2; k++) if (slides[k]) mount(slides[k]);
    applyPreload();
    playActive();
    updateCounter();
    const item = store.items[cur];
    if (item) onIndex?.(item.id);
    if (cur >= store.items.length - 4 && !store.done) store.more();
    if (details && item && sheet.classList.contains('open')) openSheet();
  }

  function updateCounter() {
    const total = store.total ?? store.items.length;
    counter.textContent = cur >= 0 ? `${cur + 1} / ${total}` : '';
  }

  function flash(slide, name, hold) {
    const f = slide?.querySelector('.flash');
    if (!f) return;
    f.innerHTML = icon(name, 'fill');
    f.classList.remove('show', 'hold');
    void f.offsetWidth;
    f.classList.add(hold ? 'hold' : 'show');
  }

  function goTo(i) {
    i = Math.max(0, Math.min(slides.length - 1, i));
    scroller.scrollTo({ top: i * scroller.clientHeight, behavior: reducedMotion() ? 'auto' : 'smooth' });
  }

  function togglePause() {
    const slide = slides[cur];
    const v = activeVideo(slide, mounted.get(slide)?.carousel);
    if (!v) return;
    if (!soundOn) {
      soundOn = true;
      setMuted(v, false);
      v.play().catch(() => {});
      updateMuteBtn();
      flash(slide, 'vol');
      return;
    }
    if (v.paused) { v.play().catch(() => {}); flash(slide, 'play'); }
    else { v.pause(); flash(slide, 'pause', true); }
  }

  function toggleMute() {
    soundOn = !soundOn;
    const slide = slides[cur];
    const v = activeVideo(slide, mounted.get(slide)?.carousel);
    if (v) {
      setMuted(v, !soundOn);
      if (v.paused) v.play().catch(() => {});
    }
    updateMuteBtn();
  }

  function openSheet() {
    const item = store.items[cur];
    if (!item) return;
    details?.destroy();
    sheet.classList.add('open');
    scrim.classList.add('open');
    feed.classList.add('sheet-open');
    details = renderDetails(sheetBody, item.id, {
      inFeed: true,
      video: () => activeVideo(slides[cur], mounted.get(slides[cur])?.carousel),
      onSeek: (v) => { if (v.paused) v.play().catch(() => {}); },
      onDeleted: () => closeSheet(),
    });
    sheetBody.scrollTop = 0;
  }

  function closeSheet() {
    sheet.classList.remove('open');
    scrim.classList.remove('open');
    feed.classList.remove('sheet-open');
    details?.destroy();
    details = null;
    scroller.focus({ preventScroll: true });
  }
  sheetDrag(sheet.querySelector('.sheet-handle'), sheet, closeSheet);

  /* --------------------------------------------------------- events */
  feed.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-act]');
    if (t) {
      const act = t.dataset.act;
      if (act === 'close') return close();
      if (act === 'mute') return toggleMute();
      if (act === 'sound') { soundOn = false; return togglePause(); }
      if (act === 'details') return openSheet();
      if (act === 'sheet-close') return closeSheet();
      if (act === 'retry') {
        const item = store.items[cur];
        try {
          const d = await API.post(`/items/${item.id}/retry`);
          store.update({ id: d.id, status: d.status });
          toast('Queued for retry');
        } catch (err) { toastErr(err); }
        return;
      }
      return;
    }
    if (e.target.closest('a, button, .dsheet, .c-nav')) return;
    if (e.target.closest('.slide')) togglePause();
  });

  function onKey(e) {
    if (e.target.closest?.('input, textarea, select, [contenteditable]')) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    if (document.getElementById('dlg').open) return;
    const st = mounted.get(slides[cur]);
    switch (e.key) {
      case 'ArrowDown': case 'PageDown': case 'j':
        e.preventDefault(); goTo(cur + 1); break;
      case 'ArrowUp': case 'PageUp': case 'k':
        e.preventDefault(); goTo(cur - 1); break;
      case 'ArrowRight':
        if (st?.carousel) { e.preventDefault(); st.carousel.go(1); } break;
      case 'ArrowLeft':
        if (st?.carousel) { e.preventDefault(); st.carousel.go(-1); } break;
      case ' ':
        if (e.target.closest?.('button, a')) return;
        e.preventDefault(); togglePause(); break;
      case 'm': toggleMute(); break;
      case 'i': case 'd': sheet.classList.contains('open') ? closeSheet() : openSheet(); break;
      case 'Escape':
        e.preventDefault();
        if (sheet.classList.contains('open')) closeSheet(); else close();
        break;
      default:
    }
  }
  document.addEventListener('keydown', onKey);

  // Fallback to the observer: settle on whichever slide the scroll position snapped to.
  let scrollT = 0;
  scroller.addEventListener('scroll', () => {
    clearTimeout(scrollT);
    scrollT = setTimeout(() => {
      const h = scroller.clientHeight;
      if (h && Math.abs(scroller.scrollTop / h - Math.round(scroller.scrollTop / h)) < 0.05) setActive(Math.round(scroller.scrollTop / h));
    }, 140);
  }, { passive: true });

  let wheelAcc = 0, wheelLock = 0;
  scroller.addEventListener('wheel', (e) => {
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return; // horizontal: let carousels scroll
    e.preventDefault();
    const now = performance.now();
    if (now < wheelLock) return;
    wheelAcc += e.deltaY * (e.deltaMode === 1 ? 30 : 1);
    if (Math.abs(wheelAcc) >= 40) {
      goTo(cur + Math.sign(wheelAcc));
      wheelAcc = 0;
      wheelLock = now + 480;
    }
  }, { passive: false });

  const onVis = () => {
    if (document.hidden) mounted.forEach((_, s) => s.querySelectorAll('video').forEach((v) => v.pause()));
  };
  document.addEventListener('visibilitychange', onVis);

  const offAppend = (e) => addSlides(e.detail);
  const offUpdate = (e) => {
    const i = store.indexOf(e.detail.id);
    if (slides[i]) refreshOverlay(slides[i]);
  };
  const offRemove = (e) => {
    const i = slides.findIndex((s) => +s.dataset.id === e.detail);
    if (i < 0) return;
    const s = slides[i];
    unmount(s);
    io.unobserve(s);
    s.remove();
    slides.splice(i, 1);
    if (!slides.length) return close();
    cur = -1;
    setActive(Math.min(i, slides.length - 1));
  };
  const offReset = () => close({ silent: false });
  store.addEventListener('append', offAppend);
  store.addEventListener('update', offUpdate);
  store.addEventListener('remove', offRemove);
  store.addEventListener('reset', offReset);

  function close({ silent = false } = {}) {
    if (closed) return;
    closed = true;
    clearTimeout(scrollT);
    const id = store.items[cur]?.id;
    details?.destroy();
    io.disconnect();
    [...mounted.keys()].forEach(unmount);
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('visibilitychange', onVis);
    store.removeEventListener('append', offAppend);
    store.removeEventListener('update', offUpdate);
    store.removeEventListener('remove', offRemove);
    store.removeEventListener('reset', offReset);
    root.innerHTML = '';
    document.documentElement.classList.remove('locked');
    for (const el of ['view', 'topbar']) document.getElementById(el)?.removeAttribute('inert');
    current = null;
    onClose?.(id, { silent, prevFocus });
  }

  current = { close, get id() { return store.items[cur]?.id; } };

  /* --------------------------------------------------------- initial position */
  addSlides(store.items);
  let idx = store.indexOf(startId);
  for (let guard = 0; idx < 0 && !store.done && guard < 20 && !closed; guard++) {
    await store.more();
    idx = store.indexOf(startId);
  }
  if (closed) return current;
  if (!slides.length) {
    toast('Nothing to show');
    close();
    return null;
  }
  if (idx < 0) idx = 0;
  scroller.scrollTop = idx * scroller.clientHeight;
  setActive(idx);
  scroller.focus({ preventScroll: true });
  return current;
}

