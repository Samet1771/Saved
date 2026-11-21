import { app, on } from '../state.js';
import { retryItem } from '../api.js';
import { h, toast, guarded } from '../util.js';
import { showItem } from '../item.js';

export function mount() {
  const list = h('div', { class: 'rows' });
  const count = h('span', { class: 'chip' });
  const el = h('section', { class: 'card wide', 'aria-label': 'Recent errors' },
    h('div', { class: 'card-head' }, h('h2', null, 'Recent errors'), count), list);
  const busy = new Set();

  function render() {
    const errs = app.status?.last_errors || [];
    count.textContent = errs.length;
    count.className = `chip ${errs.length ? 'err' : 'ok'}`;
    if (!errs.length) {
      list.replaceChildren(h('p', { class: 'hint' }, app.status ? 'No recent errors.' : 'Waiting for the server.'));
      return;
    }
    list.replaceChildren(...errs.map((e) => {
      const btn = h('button', { class: 'btn sm', disabled: busy.has(e.id) || !app.online }, 'Retry');
      btn.onclick = async () => {
        busy.add(e.id);
        try {
          await guarded(btn, async () => {
            await retryItem(e.id);
            toast(`Queued ${e.shortcode || e.id} for retry`, 'ok');
          });
        } catch (x) {
          toast(x.message, 'err');
        } finally {
          busy.delete(e.id);
        }
      };
      const first = (e.error || '').split('\n')[0];
      return h('div', { class: 'err-row' },
        h('button', { class: 'link', onclick: () => showItem(e.id, e.shortcode) }, e.shortcode || `#${e.id}`),
        h('span', { class: 'chip err' }, e.status),
        h('span', { class: 'msg', title: first }, first || '—'),
        btn);
    }));
  }

  on('status', render);
  on('conn', render);
  render();
  return el;
}
