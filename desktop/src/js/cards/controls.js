import { app, on } from '../state.js';
import { putWorker, retryFailed, reenrich } from '../api.js';
import { applyStatus } from '../stream.js';
import { h, toast, confirmDialog, guarded } from '../util.js';

const WINDOW_RE = /^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/;

export function mount() {
  const workerBtn = h('button', { class: 'btn' }, 'Pause worker');
  const fetchBtn = h('button', { class: 'btn' }, 'Pause fetching');
  const mode = h('select', { class: 'select', 'aria-label': 'Batch window mode' },
    h('option', { value: 'always' }, 'Always'), h('option', { value: 'never' }, 'Never'), h('option', { value: 'custom' }, 'Custom'));
  const range = h('input', { class: 'input mono', placeholder: '01:00-07:00', 'aria-label': 'Batch window range', maxLength: 11 });
  const apply = h('button', { class: 'btn' }, 'Apply');
  const reset = h('button', { class: 'btn ghost' }, 'Reset to default');
  const effective = h('span', { class: 'hint' });
  const skipped = h('input', { type: 'checkbox' });
  const retryBtn = h('button', { class: 'btn' }, 'Retry failed');
  const reenrichBtn = h('button', { class: 'btn' }, 'Re-enrich items from other models');
  const off = h('p', { class: 'hint warn', hidden: true }, 'Controls are disabled while the server is unreachable.');
  let dirty = false;

  const all = () => [workerBtn, fetchBtn, mode, range, apply, reset, skipped, retryBtn, reenrichBtn];
  const el = h('section', { class: 'card', 'aria-label': 'Controls' },
    h('div', { class: 'card-head' }, h('h2', null, 'Controls')),
    off,
    h('div', { class: 'actions' }, workerBtn, fetchBtn),
    h('hr', { class: 'sep' }),
    h('div', { class: 'field' }, h('span', null, 'Batch window'),
      h('div', { class: 'inline' }, mode, range, apply, reset), effective),
    h('hr', { class: 'sep' }),
    h('div', { class: 'actions' }, retryBtn,
      h('label', { class: 'check' }, skipped, 'include private / gone / needs login')),
    h('div', { class: 'actions' }, reenrichBtn));

  const run = (buttons, fn, okText) => async () => {
    try {
      await guarded(buttons, async () => {
        const r = await fn();
        toast(okText ? okText(r) : 'Done', 'ok');
      });
    } catch (e) {
      toast(e.message, 'err');
    }
    render();
  };

  const sendWorker = (body, text) => run(all(), async () => {
    applyStatus(await putWorker(body));
    dirty = false;
    return text;
  }, (t) => t);

  workerBtn.onclick = () => sendWorker({ paused: !app.status.controls.worker_paused }, app.status.controls.worker_paused ? 'Worker resumed' : 'Worker paused')();
  fetchBtn.onclick = () => sendWorker({ fetch_paused: !app.status.controls.fetch_paused }, app.status.controls.fetch_paused ? 'Fetching resumed' : 'Fetching paused')();
  reset.onclick = () => sendWorker({ reset_batch_window: true }, 'Batch window reset to default')();
  apply.onclick = () => {
    const value = mode.value === 'custom' ? range.value.trim() : mode.value;
    if (mode.value === 'custom' && !WINDOW_RE.test(value)) return toast('Use HH:MM-HH:MM, for example 01:00-07:00', 'err');
    sendWorker({ batch_window: value }, `Batch window set to ${value}`)();
  };
  mode.onchange = () => { dirty = true; render(); };
  range.oninput = () => { dirty = true; };

  retryBtn.onclick = run(all(), async () => (await retryFailed(skipped.checked)).queued, (n) => (n ? `Queued ${n} item${n === 1 ? '' : 's'} for retry` : 'Nothing to retry'));
  reenrichBtn.onclick = async () => {
    const ok = await confirmDialog('Re-enrich items?', 'Every item tagged by a different model is queued for tagging again. This uses the GPU for a long time on a large library.', 'Re-enrich');
    if (ok) await run(all(), async () => (await reenrich()).queued, (n) => `Queued ${n} item${n === 1 ? '' : 's'} for re-enrich`)();
  };

  function render() {
    const s = app.status;
    const usable = Boolean(s) && app.online;
    off.hidden = usable || !app.config?.server_url;
    for (const c of all()) c.disabled = !usable;
    range.hidden = mode.value !== 'custom';
    if (!s) return;
    const c = s.controls || {};
    workerBtn.textContent = c.worker_paused ? 'Resume worker' : 'Pause worker';
    fetchBtn.textContent = c.fetch_paused ? 'Resume fetching' : 'Pause fetching';
    reset.textContent = `Reset to default (${c.batch_window_default ?? '?'})`;
    reset.disabled = !usable || c.batch_window == null;
    effective.textContent = `Effective now: ${c.batch_window_effective ?? '?'}${c.batch_window == null ? ' (default)' : ' (override)'}`;
    if (!dirty && !el.contains(document.activeElement)) {
      const w = c.batch_window ?? c.batch_window_default ?? 'always';
      mode.value = w === 'always' || w === 'never' ? w : 'custom';
      range.value = mode.value === 'custom' ? w : range.value;
      range.hidden = mode.value !== 'custom';
    }
  }

  on('status', render);
  on('conn', render);
  render();
  return el;
}
