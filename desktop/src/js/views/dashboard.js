import { h } from '../util.js';
import * as server from '../cards/server.js';
import * as pipeline from '../cards/pipeline.js';
import * as controls from '../cards/controls.js';
import * as gpu from '../cards/gpu.js';
import * as errors from '../cards/errors.js';

export function init(root) {
  const grid = h('div', { class: 'grid' });
  root.append(h('div', { class: 'page-head' }, h('h1', null, 'Dashboard')), grid);
  for (const card of [server, pipeline, controls, gpu, errors]) grid.append(card.mount());
}
