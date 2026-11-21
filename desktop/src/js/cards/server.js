import { app, on, HIST_MS } from '../state.js';
import { h, meter, sparkline, fmtBytes, fmtDuration, ago } from '../util.js';

const tone = (p) => (p > 90 ? 'err' : p > 75 ? 'warn' : '');

export function mount() {
  const state = h('span', { class: 'chip' }, 'Offline');
  const last = h('span', { class: 'hint num' });
  const cpu = meter('CPU');
  const spark = sparkline();
  const ram = meter('RAM');
  const disk = meter('Disk');
  const values = Object.fromEntries(['load', 'uptime', 'media', 'api', 'worker'].map((k) => [k, h('span', { class: 'num' }, '—')]));
  const kv = (label, k) => h('div', { class: 'kv' }, h('span', null, label), values[k]);

  const el = h('section', { class: 'card', 'aria-label': 'Server' },
    h('div', { class: 'card-head' }, h('h2', null, 'Server'), last, state),
    h('div', { class: 'rows' }, cpu.el, spark.el, ram.el, disk.el),
    h('hr', { class: 'sep' }),
    h('div', { class: 'rows' }, kv('Host load (1 / 5 / 15)', 'load'), kv('Host uptime', 'uptime'), kv('Media size', 'media'), kv('API memory', 'api'), kv('Worker memory', 'worker')));

  function render() {
    const s = app.status;
    state.textContent = app.authError ? 'Signed out' : app.online ? 'Online' : 'Offline';
    state.className = `chip ${app.online ? 'ok' : 'err'}`;
    last.textContent = app.statusAt ? `status ${ago(app.statusAt)}` : '';
    spark.set(app.cpuHist, HIST_MS);
    if (!s) return;
    const sys = s.system || {};
    const c = sys.cpu_percent;
    cpu.set(c == null ? '—' : `${c.toFixed(1)} % of ${sys.cpus ?? '?'} cores`, c || 0, tone(c || 0));
    const mp = sys.mem_total_bytes ? (sys.mem_used_bytes / sys.mem_total_bytes) * 100 : 0;
    ram.set(`${fmtBytes(sys.mem_used_bytes)} / ${fmtBytes(sys.mem_total_bytes)}`, mp, tone(mp));
    const d = s.disk || {};
    const usedPct = d.total_bytes ? ((d.total_bytes - d.free_bytes) / d.total_bytes) * 100 : 0;
    disk.set(`${fmtBytes(d.free_bytes)} free of ${fmtBytes(d.total_bytes)}`, usedPct, d.paused ? 'err' : tone(usedPct));
    values.load.textContent = sys.load ? sys.load.map((n) => n.toFixed(2)).join(' / ') : '—';
    values.uptime.textContent = sys.uptime_s != null ? fmtDuration(sys.uptime_s) : '—';
    values.media.textContent = fmtBytes(d.media_bytes);
    values.api.textContent = sys.api_rss_mb != null ? `${sys.api_rss_mb} MB` : '—';
    values.worker.textContent = sys.worker_rss_mb != null ? `${sys.worker_rss_mb} MB` : '—';
  }

  on('status', render);
  on('conn', render);
  setInterval(render, 1000);
  render();
  return el;
}
