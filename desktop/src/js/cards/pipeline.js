import { app, on } from '../state.js';
import { h, bar, setBar, pct, fmtDuration, sinceIso, clock } from '../util.js';

const STAGES = ['fetch', 'extract', 'enrich', 'index'];
const STATUSES = [
  ['queued', ''], ['fetched', ''], ['extracted', ''], ['enriched', ''], ['indexed', 'ok'],
  ['failed', 'err'], ['private', 'warn'], ['gone', 'warn'], ['needs_login', 'warn'],
];
const ALWAYS = ['failed', 'private', 'gone', 'needs_login'];

export function mount() {
  const rows = Object.fromEntries(STAGES.map((st) => {
    const b = bar(0);
    const meta = h('span', { class: 'stage-meta' }, '—');
    const el = h('div', { class: 'stage-row' }, h('span', { class: 'stage-name' }, st), b, meta);
    return [st, { el, b, meta }];
  }));
  const chips = h('div', { class: 'chips' });
  const badges = h('div', { class: 'chips' });
  const eta = h('span', { class: 'num' }, '—');
  const current = h('span', { class: 'num' }, '—');
  const el = h('section', { class: 'card', 'aria-label': 'Pipeline' },
    h('div', { class: 'card-head' }, h('h2', null, 'Pipeline')),
    h('div', { class: 'rows' }, ...STAGES.map((st) => rows[st].el)),
    chips,
    h('hr', { class: 'sep' }),
    h('div', { class: 'rows' },
      h('div', { class: 'kv' }, h('span', null, 'Overall ETA'), eta),
      h('div', { class: 'kv' }, h('span', null, 'Now'), current)),
    badges);

  const badge = (text, tone) => h('span', { class: `chip ${tone}`.trim() }, text);

  function render() {
    const s = app.status;
    if (!s) {
      chips.replaceChildren();
      badges.replaceChildren();
      eta.textContent = current.textContent = '—';
      return;
    }
    for (const st of STAGES) {
      const p = s.progress?.[st];
      const r = rows[st];
      r.el.classList.toggle('active', s.worker?.stage === st);
      if (!p) {
        r.meta.textContent = '—';
        setBar(r.b, 0);
        continue;
      }
      setBar(r.b, pct(p.done, p.total), p.total && p.done >= p.total ? 'ok' : '');
      r.meta.textContent = `${p.done}/${p.total} · ${p.done_last_hour}/h · ${p.avg_s != null ? `${p.avg_s.toFixed(1)} s` : '—'}`;
    }
    const counts = s.counts || {};
    chips.replaceChildren(...STATUSES
      .filter(([k]) => counts[k] || ALWAYS.includes(k))
      .map(([k, tone]) => badge(`${k.replace('_', ' ')} ${counts[k] || 0}`, counts[k] ? tone : '')));
    eta.textContent = s.eta_seconds != null ? fmtDuration(s.eta_seconds) : '—';
    const w = s.worker || {};
    current.textContent = !w.stage || w.stage === 'idle' ? 'idle' : `${w.stage}${w.shortcode ? ` · ${w.shortcode}` : ''} · ${sinceIso(w.since)}`;

    const list = [w.in_batch_window ? badge('in batch window', 'ok') : badge('outside batch window', '')];
    const until = w.fetch_backoff_until && Date.parse(w.fetch_backoff_until);
    if (until > Date.now()) list.push(badge(`fetch backoff until ${clock(until).slice(0, 5)}`, 'warn'));
    if (s.disk?.paused) list.push(badge('disk low: fetching paused', 'err'));
    if (s.controls?.worker_paused) list.push(badge('worker paused', 'warn'));
    if (s.controls?.fetch_paused) list.push(badge('fetching paused', 'warn'));
    badges.replaceChildren(...list);
  }

  on('status', render);
  setInterval(render, 1000);
  render();
  return el;
}
