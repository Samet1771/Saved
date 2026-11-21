import { app, on, EVENT_CAP } from '../state.js';
import { h, clock } from '../util.js';
import { showItem } from '../item.js';

const STAGES = ['fetch', 'extract', 'enrich', 'index'];

export function init(root) {
  const stage = h('select', { class: 'select', 'aria-label': 'Stage' }, h('option', { value: '' }, 'All stages'), ...STAGES.map((s) => h('option', { value: s }, s)));
  const level = h('select', { class: 'select', 'aria-label': 'Level' },
    h('option', { value: '' }, 'All levels'), h('option', { value: 'info' }, 'info'), h('option', { value: 'warn' }, 'warn'), h('option', { value: 'error' }, 'error'));
  const text = h('input', { class: 'input', type: 'search', placeholder: 'Filter text or shortcode', 'aria-label': 'Filter text' });
  const count = h('span', { class: 'hint num' });
  const empty = h('div', { class: 'act-empty' });
  const jump = h('button', { class: 'btn sm primary jump', hidden: true }, 'Jump to latest');
  const list = h('div', { class: 'act-list', role: 'log', 'aria-live': 'off' });
  root.append(h('div', { class: 'act' },
    h('div', { class: 'page-head' }, h('h1', null, 'Activity'), count),
    h('div', { class: 'act-filters' }, stage, level, text),
    list));

  const passes = (e) => {
    if (stage.value && e.stage !== stage.value) return false;
    if (level.value && e.level !== level.value) return false;
    const q = text.value.trim().toLowerCase();
    return !q || `${e.message} ${e.shortcode || ''} ${e.stage || ''}`.toLowerCase().includes(q);
  };

  function row(e) {
    const el = h('div', { class: `ev ${e.level} ${e.item_id ? 'click' : ''}`.trim(), title: e.item_id ? 'Open item details' : '' },
      h('span', { class: 't' }, clock(Date.parse(e.at))),
      h('span', { class: 'lv' }, e.level),
      h('span', { class: 'stg' }, e.stage || ''),
      h('span', { class: 'msg' }, e.shortcode ? h('span', { class: 'sc' }, e.shortcode) : null, e.message),
      h('span', { class: 'd' }, e.duration_s != null ? `${e.duration_s.toFixed(1)} s` : ''));
    if (e.item_id) el.onclick = () => showItem(e.item_id, e.shortcode);
    return el;
  }

  function updateCount(shown) {
    count.textContent = `${shown} of ${app.events.length} events`;
    empty.textContent = app.events.length ? 'No events match the filters.' : app.online ? 'No events yet.' : 'Waiting for the server.';
    if (shown) empty.remove();
    else list.append(empty);
  }

  function renderAll() {
    const rows = app.events.filter(passes).map(row);
    list.replaceChildren(jump, ...rows);
    updateCount(rows.length);
  }

  let pending = [];
  let frameQueued = false;
  function flush() {
    frameQueued = false;
    const batch = pending.filter(passes);
    pending = [];
    if (batch.length) {
      const before = list.scrollHeight;
      for (const e of batch) list.insertBefore(row(e), list.getElementsByClassName('ev')[0] || null);
      if (list.scrollTop > 4) list.scrollTop += list.scrollHeight - before;
      const rows = list.getElementsByClassName('ev');
      while (rows.length > EVENT_CAP) rows[rows.length - 1].remove();
    }
    updateCount(list.getElementsByClassName('ev').length);
  }

  on('event', (e) => {
    pending.push(e);
    if (!frameQueued) {
      frameQueued = true;
      setTimeout(flush, 120);
    }
  });
  on('events-reset', () => { pending = []; renderAll(); });
  for (const c of [stage, level]) c.onchange = renderAll;
  text.oninput = renderAll;
  list.onscroll = () => { jump.hidden = list.scrollTop < 40; };
  jump.onclick = () => list.scrollTo({ top: 0 });
  renderAll();
}
