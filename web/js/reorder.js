/**
 * Drag-to-reorder for the card grid with pointer events.
 * Mouse: press and move. Touch/pen: long-press (350 ms) to pick up, then drag.
 * onMove(id, afterId|null, toIndex) is called after a drop that changed the order.
 */
export function initReorder(grid, { onMove }) {
  const ctl = { enabled: false, dragging: false };
  let press = null;     // { id, card, x, y, pointerId, type, timer }
  let drag = null;      // { card, ghost, dx, dy, startIndex, lastX, lastY }
  let suppressClick = false;
  let raf = 0;

  const cards = () => [...grid.querySelectorAll('.card')];

  function cancelPress() {
    if (press?.timer) clearTimeout(press.timer);
    press?.card.classList.remove('pressing');
    press = null;
  }

  function start() {
    const { card, x, y } = press;
    const r = card.getBoundingClientRect();
    const ghost = card.cloneNode(true);
    ghost.classList.add('drag-ghost');
    ghost.classList.remove('pressing');
    Object.assign(ghost.style, { width: `${r.width}px`, height: `${r.height}px`, left: `${r.left}px`, top: `${r.top}px` });
    ghost.setAttribute('aria-hidden', 'true');
    document.body.append(ghost);
    card.classList.remove('pressing');
    card.classList.add('drag-src');
    drag = { card, ghost, dx: x - r.left, dy: y - r.top, startIndex: cards().indexOf(card), lastX: x, lastY: y };
    ctl.dragging = true;
    grid.classList.add('reordering');
    try { grid.setPointerCapture(press.pointerId); } catch { /* pointer gone */ }
    navigator.vibrate?.(12);
    raf = requestAnimationFrame(autoScroll);
  }

  function place(x, y) {
    drag.lastX = x; drag.lastY = y;
    drag.ghost.style.left = `${x - drag.dx}px`;
    drag.ghost.style.top = `${y - drag.dy}px`;
    // Map the pointer to a grid cell; cells stay put while cards move, so this does not jitter.
    const list = cards();
    if (!list.length) return;
    const cs = getComputedStyle(grid);
    const cols = cs.gridTemplateColumns.split(' ').length;
    const cg = parseFloat(cs.columnGap) || 0;
    const rg = parseFloat(cs.rowGap) || 0;
    const f = list[0].getBoundingClientRect();
    const col = Math.max(0, Math.min(cols - 1, Math.floor((x - f.left + cg / 2) / (f.width + cg))));
    const row = Math.max(0, Math.floor((y - f.top + rg / 2) / (f.height + rg)));
    const idx = Math.min(list.length - 1, row * cols + col);
    const cur = list.indexOf(drag.card);
    if (idx === cur) return;
    if (idx < cur) list[idx].before(drag.card); else list[idx].after(drag.card);
  }

  function autoScroll() {
    if (!drag) return;
    const edge = 80;
    let v = 0;
    if (drag.lastY < edge) v = -Math.ceil((edge - drag.lastY) / 6);
    else if (drag.lastY > innerHeight - edge) v = Math.ceil((drag.lastY - (innerHeight - edge)) / 6);
    if (v) { scrollBy(0, v); place(drag.lastX, drag.lastY); }
    raf = requestAnimationFrame(autoScroll);
  }

  function finish(commit) {
    cancelAnimationFrame(raf);
    const { card, ghost, startIndex } = drag;
    ghost.remove();
    card.classList.remove('drag-src');
    grid.classList.remove('reordering');
    const list = cards();
    const to = list.indexOf(card);
    drag = null;
    ctl.dragging = false;
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 50);
    if (!commit) {
      const ref = list.filter((c) => c !== card)[startIndex];
      if (ref) ref.before(card); else grid.append(card);
      return;
    }
    if (to !== startIndex) {
      const prev = list[to - 1];
      onMove(+card.dataset.id, prev ? +prev.dataset.id : null, to);
    }
  }

  grid.addEventListener('pointerdown', (e) => {
    if (!ctl.enabled || drag || e.button > 0) return;
    const card = e.target.closest('.card');
    if (!card) return;
    press = { card, x: e.clientX, y: e.clientY, pointerId: e.pointerId, type: e.pointerType, timer: null };
    if (e.pointerType !== 'mouse') {
      card.classList.add('pressing');
      press.timer = setTimeout(() => { if (press) start(); }, 350);
    }
  });

  grid.addEventListener('pointermove', (e) => {
    if (drag) { e.preventDefault(); place(e.clientX, e.clientY); return; }
    if (!press || e.pointerId !== press.pointerId) return;
    const moved = Math.hypot(e.clientX - press.x, e.clientY - press.y);
    if (press.type === 'mouse') { if (moved > 6) start(); }
    else if (moved > 10) cancelPress(); // it's a scroll
  });

  grid.addEventListener('pointerup', (e) => {
    if (drag) { finish(true); press = null; return; }
    if (press && e.pointerId === press.pointerId) cancelPress();
  });
  grid.addEventListener('pointercancel', () => {
    if (drag) finish(false);
    cancelPress();
  });
  // Stop the page from scrolling while a touch drag is active.
  grid.addEventListener('touchmove', (e) => { if (drag) e.preventDefault(); }, { passive: false });
  grid.addEventListener('contextmenu', (e) => { if (ctl.enabled && (press || drag)) e.preventDefault(); });
  grid.addEventListener('click', (e) => { if (suppressClick) { e.preventDefault(); e.stopPropagation(); } }, true);
  grid.addEventListener('dragstart', (e) => { if (ctl.enabled) e.preventDefault(); });
  addEventListener('keydown', (e) => { if (drag && e.key === 'Escape') finish(false); });

  return ctl;
}
