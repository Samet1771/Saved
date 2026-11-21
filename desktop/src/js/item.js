import { getItem } from './api.js';
import { h, infoDialog, toast } from './util.js';

const SKIP = new Set(['error']);

export async function showItem(id, shortcode) {
  try {
    const item = await getItem(id);
    const rows = Object.entries(item)
      .filter(([k, v]) => !SKIP.has(k) && v != null && v !== '' && ['string', 'number', 'boolean'].includes(typeof v))
      .map(([k, v]) => h('div', { class: 'kv' }, h('span', null, k.replace(/_/g, ' ')), h('span', null, String(v).slice(0, 240))));
    infoDialog(item.shortcode || shortcode || `Item ${id}`, h('div', { class: 'rows' }, ...rows, item.error ? h('pre', null, item.error) : null));
  } catch (e) {
    toast(e.message, 'err');
  }
}
