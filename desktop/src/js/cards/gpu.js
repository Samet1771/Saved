import { app, on, HIST_MS } from '../state.js';
import { native } from '../native.js';
import { refreshLlm } from '../poll.js';
import { h, meter, sparkline, toast, confirmDialog, guarded } from '../util.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function mount() {
  const state = h('span', { class: 'chip' }, 'Stopped');
  const val = Object.fromEntries(['healthy', 'seen', 'model', 'port', 'pid'].map((k) => [k, h('span', { class: 'num' }, '—')]));
  const kv = (label, k) => h('div', { class: 'kv' }, h('span', null, label), val[k]);
  const startBtn = h('button', { class: 'btn primary' }, 'Start');
  const stopBtn = h('button', { class: 'btn' }, 'Stop');
  const restartBtn = h('button', { class: 'btn' }, 'Restart');
  const logBtn = h('button', { class: 'btn ghost' }, 'Open log');
  const hint = h('p', { class: 'hint', hidden: true });
  const name = h('span', { class: 'hint' });
  const util = meter('Utilization');
  const vram = meter('VRAM');
  const spark = sparkline('info');
  const temp = h('span', { class: 'num' }, '—');
  const power = h('span', { class: 'num' }, '—');
  const gpuNote = h('p', { class: 'hint warn', hidden: true }, 'nvidia-smi was not found or failed, so GPU stats are unavailable.');
  const gpuBox = h('div', { class: 'rows' }, name, util.el, spark.el, vram.el,
    h('div', { class: 'kv' }, h('span', null, 'Temperature'), temp), h('div', { class: 'kv' }, h('span', null, 'Power draw'), power));
  const log = h('pre', { class: 'log', hidden: true });
  let starting = 0;
  let logTimer = null;

  const el = h('section', { class: 'card', 'aria-label': 'GPU PC' },
    h('div', { class: 'card-head' }, h('h2', null, 'GPU PC'), state),
    h('div', { class: 'rows' }, kv('Healthy', 'healthy'), kv('Server sees it', 'seen'), kv('Model file', 'model'), kv('Port', 'port'), kv('PID', 'pid')),
    h('div', { class: 'actions' }, startBtn, stopBtn, restartBtn, logBtn),
    hint,
    h('hr', { class: 'sep' }),
    gpuNote, gpuBox, log);

  const buttons = [startBtn, stopBtn, restartBtn];

  async function waitFor(pred, ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      await refreshLlm();
      if (pred(app.llm)) return true;
      await sleep(1000);
    }
    return false;
  }

  async function doStart() {
    await native.llmStart();
    starting = Date.now();
    toast('Starting the GPU server. Loading the model takes up to a minute.', 'ok');
    renderLlm();
    const ok = await waitFor((l) => l?.healthy, 150000);
    starting = 0;
    if (ok) toast('GPU server is ready', 'ok');
    else toast('GPU server did not become healthy. Open the log.', 'err');
  }

  async function doStop() {
    await native.llmStop();
    await waitFor((l) => !l?.running, 15000);
  }

  startBtn.onclick = () => guarded(buttons, doStart).catch((e) => toast(String(e), 'err')).finally(renderLlm);
  stopBtn.onclick = async () => {
    if (!(await confirmDialog('Stop the GPU server?', 'Tagging stops until it is started again. Items wait in the extracted state.', 'Stop'))) return;
    guarded(buttons, async () => { await doStop(); toast('GPU server stopped', 'ok'); }).catch((e) => toast(String(e), 'err')).finally(renderLlm);
  };
  restartBtn.onclick = async () => {
    if (!(await confirmDialog('Restart the GPU server?', 'Tagging pauses while the model reloads.', 'Restart'))) return;
    guarded(buttons, async () => { await doStop(); await doStart(); }).catch((e) => toast(String(e), 'err')).finally(renderLlm);
  };

  async function refreshLog() {
    try {
      const t = await native.llmLogTail(200);
      log.textContent = t || 'No log yet. The log only exists when this app started the server.';
      log.scrollTop = log.scrollHeight;
    } catch (e) {
      log.textContent = String(e);
    }
  }
  logBtn.onclick = () => {
    log.hidden = !log.hidden;
    logBtn.textContent = log.hidden ? 'Open log' : 'Close log';
    clearInterval(logTimer);
    if (!log.hidden) {
      refreshLog();
      logTimer = setInterval(refreshLog, 2000);
    }
  };

  function renderLlm() {
    const l = app.llm;
    const dir = app.config?.pcllm_dir;
    const running = Boolean(l?.running);
    const loading = starting && !l?.healthy;
    state.textContent = loading ? 'Starting' : running ? (l.healthy ? 'Running' : 'Loading') : l?.healthy ? 'Responding' : 'Stopped';
    state.className = `chip ${loading || (running && !l.healthy) ? 'warn' : running && l.healthy ? 'ok' : ''}`.trim();
    val.healthy.textContent = l ? (l.healthy ? 'yes' : 'no') : '—';
    const remote = app.status?.worker?.llm_remote;
    val.seen.textContent = !app.status ? '—' : !remote ? 'not configured on server' : remote.healthy ? 'yes' : 'no';
    val.model.textContent = l?.model_file || '—';
    val.port.textContent = app.config?.llm_port ?? '—';
    val.pid.textContent = l?.pid ?? '—';
    startBtn.disabled = !dir || running || Boolean(starting);
    stopBtn.disabled = !running || Boolean(starting);
    restartBtn.disabled = !dir || !running || Boolean(starting);
    hint.hidden = Boolean(dir);
    hint.textContent = 'Set the path to tools/pc-llm in Settings to start the GPU server from here.';
    hint.className = 'hint warn';
  }

  function renderGpu() {
    const g = app.gpu;
    gpuNote.hidden = g !== null;
    gpuBox.hidden = !g;
    if (!g) return;
    name.textContent = g.name;
    const u = g.utilization_pct;
    util.set(u == null ? '—' : `${u} %`, u || 0);
    const pctMem = g.memory_total_mb ? (g.memory_used_mb / g.memory_total_mb) * 100 : 0;
    vram.set(g.memory_used_mb == null ? '—' : `${(g.memory_used_mb / 1024).toFixed(1)} / ${((g.memory_total_mb || 0) / 1024).toFixed(1)} GB`, pctMem, pctMem > 95 ? 'warn' : '');
    temp.textContent = g.temperature_c == null ? '—' : `${g.temperature_c} °C`;
    power.textContent = g.power_w == null ? '—' : `${g.power_w.toFixed(0)} W`;
    spark.set(app.gpuHist, HIST_MS);
  }

  on('llm', renderLlm);
  on('config', renderLlm);
  on('status', renderLlm);
  on('gpu', renderGpu);
  renderLlm();
  gpuNote.hidden = true;
  gpuBox.hidden = true;
  return el;
}
