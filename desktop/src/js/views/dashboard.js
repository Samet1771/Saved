import { app, on } from '../state.js';
import { h } from '../util.js';
import * as server from '../cards/server.js';
import * as pipeline from '../cards/pipeline.js';
import * as controls from '../cards/controls.js';
import * as gpu from '../cards/gpu.js';
import * as compute from '../cards/compute.js';
import * as errors from '../cards/errors.js';

export function init(root) {
  const grid = h('div', { class: 'grid' });
  root.append(h('div', { class: 'page-head' }, h('h1', null, 'Dashboard')), grid);
  const gpuCard = gpu.mount();
  const computeCard = compute.mount();
  for (const card of [server.mount(), pipeline.mount(), controls.mount(), gpuCard, computeCard, errors.mount()]) grid.append(card);
  const sync = () => { gpuCard.hidden = app.status?.compute?.mode === 'hybrid'; };
  on('status', sync);
}
