import { app, on } from '../state.js';
import { native } from '../native.js';
import { startCompute, stopCompute, resetComputeToken } from '../compute.js';
import { h, meter, toast, confirmDialog, guarded, sinceIso } from '../util.js';

const STAGES = ['fetch', 'extract', 'enrich', 'index'];
const KEYS = {
  name: ['name', 'gpu_name'],
  util: ['util', 'util_pct', 'utilization', 'utilization_pct', 'gpu_util', 'gpu_util_pct', 'gpu_util_percent'],
  used: ['vram_used_mb', 'memory_used_mb', 'vram_used'],
  total: ['vram_total_mb', 'memory_total_mb', 'vram_total'],
  queue: ['queue', 'queues', 'queue_len', 'queue_lengths', 'queued'],
  done: ['done', 'done_counts', 'completed'],
  avg: ['avg_s', 'avg_seconds', 'avg_sec', 'avg'],
  llama: ['llm_ok', 'llama_reachable','llama_server_reachable', 'llama_ok', 'llama', 'llm_reachable'],
};
const WARN_AFTER_MS = 60000;

const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function pick(src, keys, used) {
  for (const k of keys) {
    if (src[k] != null) {
      used.add(k);
      return src[k];
    }
  }
  return null;
}

function parse(info) {
  const out = { name: null, util: null, used: null, total: null, stages: [], llama: null, extra: [] };
  if (!isObj(info)) return out;
  const used = new Set();
  let src = info;
  if (isObj(info.gpu)) {
    src = { ...info, ...info.gpu };
    used.add('gpu');
  } else if (typeof info.gpu === 'string') {
    out.name = info.gpu;
    used.add('gpu');
  }
  out.name ??= pick(src, KEYS.name, used);
  out.util = num(pick(src, KEYS.util, used));
  out.used = num(pick(src, KEYS.used, used));
  out.total = num(pick(src, KEYS.total, used));
  const llama = pick(src, KEYS.llama, used);
  out.llama = typeof llama === 'boolean' ? llama : null;
  const maps = Object.fromEntries(['queue', 'done', 'avg'].map((k) => [k, pick(src, KEYS[k], used)]));
  const names = new Set();
  for (const m of Object.values(maps)) if (isObj(m)) Object.keys(m).forEach((n) => names.add(n));
  const order = [...STAGES.filter((s) => names.has(s)), ...[...names].filter((s) => !STAGES.includes(s))];
  out.stages = order.map((s) => ({ stage: s, queue: num(maps.queue?.[s]), done: num(maps.done?.[s]), avg: num(maps.avg?.[s]) }));
  for (const [k, v] of Object.entries(info)) {
    if (used.has(k) || v == null) continue;
    const text = typeof v === 'object' ? JSON.stringify(v) : String(v);
    out.extra.push([k, text.length > 120 ? `${text.slice(0, 117)}…` : text]);
  }
  return out;
}

function seenText(seen) {
  if (typeof seen === 'string') return sinceIso(seen);
  if (num(seen) != null) return sinceIso(new Date(seen > 1e11 ? seen : seen * 1000).toISOString());
  return null;
}

export function mount() {
  const state = h('span', { class: 'chip' }, 'Stopped');
  const val = Object.fromEntries(['process', 'agent', 'seen', 'leased', 'llama'].map((k) => [k, h('span', { class: 'num' }, '—')]));
  const kv = (label, k) => h('div', { class: 'kv' }, h('span', null, label), val[k]);
  const toggleBtn = h('button', { class: 'btn primary' }, 'Start');
  const logBtn = h('button', { class: 'btn ghost' }, 'View log');
  const resetBtn = h('button', { class: 'btn', hidden: true }, 'Reset agent token');
  const hint = h('p', { class: 'hint', hidden: true });
  const name = h('span', { class: 'hint' });
  const util = meter('Utilization');
  const vram = meter('VRAM');
  const stages = h('div', { class: 'rows' });
  const extra = h('div', { class: 'rows' });
  const noInfo = h('p', { class: 'hint' }, 'No stats from the agent yet.');
  const log = h('pre', { class: 'log', hidden: true });
  let pending = false;
  let runningSince = 0;
  let logTimer = null;
  let rejected = false;
  let checkedAt = 0;

  const el = h('section', { class: 'card', 'aria-label': 'PC compute', hidden: true },
    h('div', { class: 'card-head' }, h('h2', null, 'PC compute'), state),
    h('div', { class: 'rows' }, kv('Local process', 'process'), kv('Agent online', 'agent'), kv('Last seen', 'seen'), kv('Leased items', 'leased'), kv('llama-server', 'llama')),
    h('div', { class: 'actions' }, toggleBtn, logBtn, resetBtn),
    hint,
    h('hr', { class: 'sep' }),
    noInfo, name, util.el, vram.el, stages, extra, log);

  const buttons = [toggleBtn, resetBtn];

  toggleBtn.onclick = async () => {
    const running = app.compute?.running;
    if (running && !(await confirmDialog('Stop PC compute?', 'The agent releases its items first. Processing waits until it is started again.', 'Stop'))) return;
    pending = true;
    render();
    guarded(buttons, async () => {
      if (running) {
        await stopCompute();
        toast('PC compute stopped', 'ok');
      } else {
        await startCompute();
        toast('PC compute is starting', 'ok');
      }
    }).catch((e) => toast(String(e), 'err')).finally(() => { pending = false; render(); });
  };

  resetBtn.onclick = async () => {
    if (!(await confirmDialog('Reset the agent token?', 'A new token is created on the server and stored in Windows Credential Manager. A running agent restarts with it.', 'Reset'))) return;
    pending = true;
    render();
    guarded(buttons, async () => {
      await resetComputeToken();
      rejected = false;
      runningSince = Date.now();
      toast('Agent token reset', 'ok');
    }).catch((e) => toast(String(e), 'err')).finally(() => { pending = false; render(); });
  };

  async function refreshLog() {
    try {
      const t = await native.computeLogTail(200);
      log.textContent = t || 'No log yet. The log only exists when this app started PC compute.';
      log.scrollTop = log.scrollHeight;
    } catch (e) {
      log.textContent = String(e);
    }
  }
  logBtn.onclick = () => {
    log.hidden = !log.hidden;
    logBtn.textContent = log.hidden ? 'View log' : 'Hide log';
    clearInterval(logTimer);
    if (!log.hidden) {
      refreshLog();
      logTimer = setInterval(refreshLog, 2000);
    }
  };

  async function checkRejected() {
    checkedAt = Date.now();
    try {
      rejected = /\b401\b|unauthori[sz]ed/i.test(await native.computeLogTail(60));
    } catch { /* log unreadable */ }
    render();
  }

  function render() {
    const c = app.status?.compute;
    el.hidden = c?.mode !== 'hybrid';
    if (el.hidden) return;
    const local = app.compute;
    const running = Boolean(local?.running);
    const agent = c.agent || {};
    const online = Boolean(agent.online);
    if (running && !runningSince) runningSince = Date.now();
    if (!running) {
      runningSince = 0;
      rejected = false;
    }
    const stuck = running && !online && !pending && Date.now() - runningSince > WARN_AFTER_MS;
    if (stuck && Date.now() - checkedAt > 15000) checkRejected();
    state.textContent = pending ? 'Working' : !running ? 'Stopped' : online ? 'Running' : stuck ? 'No agent' : 'Starting';
    state.className = `chip ${pending || (running && !online) ? 'warn' : running ? 'ok' : ''}`.trim();
    val.process.textContent = running ? `running${local.pid ? ` · PID ${local.pid}` : ''}` : 'stopped';
    val.agent.textContent = online ? `yes${agent.name ? ` · ${agent.name}` : ''}` : agent.seen ? 'no' : 'never seen';
    val.seen.textContent = seenText(agent.seen) || '—';
    const leased = Array.isArray(agent.leased) ? agent.leased.length : num(agent.leased);
    val.leased.textContent = leased ?? '—';

    const info = parse(agent.info);
    const g = app.gpu;
    const gname = info.name || g?.name;
    const gutil = info.util ?? g?.utilization_pct ?? null;
    const gused = info.used ?? g?.memory_used_mb ?? null;
    const gtotal = info.total ?? g?.memory_total_mb ?? null;
    const llama = info.llama ?? (local ? local.llm_healthy : null);
    val.llama.textContent = llama == null ? '—' : llama ? 'reachable' : 'not reachable';
    noInfo.hidden = Boolean(isObj(agent.info) && Object.keys(agent.info).length);
    name.textContent = gname || '';
    name.hidden = !gname;
    util.el.hidden = gutil == null;
    if (gutil != null) util.set(`${gutil} %`, gutil);
    vram.el.hidden = gused == null;
    if (gused != null) {
      const p = gtotal ? (gused / gtotal) * 100 : 0;
      vram.set(`${(gused / 1024).toFixed(1)}${gtotal ? ` / ${(gtotal / 1024).toFixed(1)}` : ''} GB`, p, p > 95 ? 'warn' : '');
    }
    stages.replaceChildren(...info.stages.map((s) => h('div', { class: 'kv' }, h('span', null, s.stage),
      h('span', { class: 'num' }, `${s.queue ?? '—'} queued · ${s.done ?? '—'} done · ${s.avg != null ? `${s.avg.toFixed(1)} s` : '—'}`))));
    extra.replaceChildren(...info.extra.map(([k, v]) => h('div', { class: 'kv' }, h('span', null, k), h('span', { class: 'num' }, v))));

    const dir = app.config?.pcllm_dir;
    toggleBtn.textContent = running ? 'Stop' : 'Start';
    toggleBtn.className = running ? 'btn' : 'btn primary';
    toggleBtn.disabled = pending || (!running && !dir);
    resetBtn.hidden = !stuck;
    hint.hidden = !stuck && Boolean(dir);
    hint.className = 'hint warn';
    hint.textContent = !dir
      ? 'Set the path to tools/pc-llm in Settings. PC compute starts from the pc-compute folder next to it.'
      : rejected
        ? 'The server rejected the agent token (401 in the log). Reset the agent token.'
        : 'The process runs but the server does not see the agent. Open the log; if it shows 401, reset the agent token.';
  }

  on('compute', render);
  on('status', render);
  on('config', render);
  on('gpu', render);
  setInterval(render, 5000);
  render();
  return el;
}
