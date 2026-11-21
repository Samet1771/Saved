import { app, emit, on } from '../state.js';
import { native } from '../native.js';
import { login, logout, testConnection } from '../api.js';
import { resetComputeToken } from '../compute.js';
import { h, toast, guarded, confirmDialog } from '../util.js';

export function init(root) {
  const url = h('input', { class: 'input', type: 'url', placeholder: 'http://192.168.1.10:8080', spellcheck: false, autocomplete: 'off', 'aria-label': 'Server URL' });
  const user = h('input', { class: 'input', spellcheck: false, autocomplete: 'username', 'aria-label': 'Username' });
  const pass = h('input', { class: 'input', type: 'password', autocomplete: 'current-password', 'aria-label': 'Password' });
  const signedIn = h('span', { class: 'hint' });
  const signIn = h('button', { class: 'btn primary' }, 'Sign in');
  const testBtn = h('button', { class: 'btn' }, 'Test connection');
  const result = h('span', { class: 'hint', role: 'status' });
  const dir = h('input', { class: 'input mono', placeholder: 'C:\\path\\to\\saved\\tools\\pc-llm', spellcheck: false, autocomplete: 'off', 'aria-label': 'Path to tools/pc-llm' });
  const dirHint = h('span', { class: 'hint' });
  const port = h('input', { class: 'input', type: 'number', min: 1, max: 65535, 'aria-label': 'LLM port' });
  const autostart = h('input', { type: 'checkbox' });
  const autostartCompute = h('input', { type: 'checkbox' });
  const autostartComputeRow = h('label', { class: 'check', hidden: true }, autostartCompute, 'Start PC compute when the app starts');
  const resetToken = h('button', { class: 'btn', hidden: true }, 'Reset agent token');
  const tray = h('input', { type: 'checkbox' });
  const save = h('button', { class: 'btn primary' }, 'Save');
  const clear = h('button', { class: 'btn ghost danger' }, 'Sign out');

  root.append(h('div', { class: 'page-head' }, h('h1', null, 'Settings')),
    h('div', { class: 'form' },
      h('div', { class: 'field' }, h('span', null, 'Server URL'), url,
        h('span', { class: 'hint' }, 'The address of the Saved server on your network. The Library view embeds it.')),
      h('div', { class: 'field' }, h('span', null, 'Username'), user),
      h('div', { class: 'field' }, h('span', null, 'Password'), pass, signedIn,
        h('div', { class: 'inline' }, signIn, testBtn, clear, result)),
      h('hr', { class: 'sep' }),
      h('div', { class: 'field' }, h('span', null, 'Path to tools/pc-llm'), dir, dirHint),
      h('div', { class: 'field' }, h('span', null, 'LLM port'), port),
      h('label', { class: 'check' }, autostart, 'Start the GPU server when the app starts'),
      autostartComputeRow,
      h('div', { class: 'actions' }, resetToken),
      h('label', { class: 'check' }, tray, 'Close to tray (keep running in the background)'),
      h('div', { class: 'actions' }, save)));

  function fill() {
    const c = app.config;
    if (!c) return;
    url.value = c.server_url;
    dir.value = c.pcllm_dir;
    port.value = c.llm_port;
    autostart.checked = c.autostart_llm;
    autostartCompute.checked = c.autostart_compute;
    tray.checked = c.close_to_tray;
    pass.value = '';
    signedIn.textContent = app.token
      ? 'Signed in. The session is kept in Windows Credential Manager; the password is not stored.'
      : 'Not signed in.';
    clear.hidden = !app.token;
    testBtn.hidden = !app.token;
    syncHybrid();
    dirHint.textContent = c.pcllm_dir ? '' : 'Needed to start the GPU server from here. It holds start-llm.ps1.';
  }

  const syncHybrid = () => {
    const hybrid = app.status?.compute?.mode === 'hybrid';
    autostartComputeRow.hidden = !hybrid;
    resetToken.hidden = !hybrid || !app.token;
  };

  resetToken.onclick = async () => {
    if (!(await confirmDialog('Reset the agent token?', 'A new token is created on the server and stored in Windows Credential Manager. A running agent restarts with it.', 'Reset'))) return;
    guarded(resetToken, async () => {
      try {
        await resetComputeToken();
        toast('Agent token reset', 'ok');
      } catch (e) {
        toast(String(e), 'err');
      }
    });
  };

  const showResult = (ok, text) => {
    result.className = 'hint';
    result.style.color = ok ? 'var(--ok)' : 'var(--danger)';
    result.textContent = text;
  };

  signIn.onclick = () => guarded(signIn, async () => {
    if (!url.value.trim()) return toast('Enter the server URL first', 'err');
    if (!user.value.trim() || !pass.value) return toast('Enter your username and password', 'err');
    try {
      const r = await login(url.value, user.value.trim(), pass.value);
      if (app.token) await logout();
      await native.tokenSet(r.token);
      app.token = r.token;
      app.authError = false;
      if (app.config.server_url !== url.value.trim().replace(/\/+$/, '')) {
        app.config = await native.configSet({ ...app.config, server_url: url.value });
      }
      fill();
      emit('config');
      showResult(true, `Signed in as ${r.username}.`);
    } catch (e) {
      pass.select();
      showResult(false, e.message);
    }
  });

  testBtn.onclick = async () => {
    const t = app.token;
    if (!url.value.trim()) return toast('Enter the server URL first', 'err');
    if (!t) return toast('Sign in first', 'err');
    result.className = 'hint';
    result.textContent = 'Testing…';
    await guarded(testBtn, async () => {
      try {
        const s = await testConnection(url.value, t);
        const n = Object.values(s.counts || {}).reduce((a, b) => a + b, 0);
        result.className = 'hint';
        result.style.color = 'var(--ok)';
        result.textContent = `Connected. ${n} items on the server.`;
      } catch (e) {
        result.style.color = 'var(--danger)';
        result.textContent = e.status === 401 ? 'The session is no longer valid. Sign in again.' : e.message;
      }
    });
  };

  save.onclick = () => guarded(save, async () => {
    const port_ = Number(port.value);
    if (!Number.isInteger(port_) || port_ < 1 || port_ > 65535) return toast('LLM port must be between 1 and 65535', 'err');
    try {
      const next = await native.configSet({
        ...app.config,
        server_url: url.value,
        pcllm_dir: dir.value,
        llm_port: port_,
        autostart_llm: autostart.checked,
        autostart_compute: autostartCompute.checked,
        close_to_tray: tray.checked,
      });
      app.config = next;
      fill();
      emit('config');
      toast('Settings saved', 'ok');
    } catch (e) {
      toast(String(e), 'err');
    }
  });

  clear.onclick = () => guarded(clear, async () => {
    try {
      await logout();
      await native.tokenSet('');
      app.token = '';
      fill();
      emit('config');
      toast('Signed out', 'ok');
    } catch (e) {
      toast(String(e), 'err');
    }
  });

  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.target.tagName !== 'INPUT' || e.target.type === 'checkbox') return;
    (e.target === user || e.target === pass ? signIn : save).click();
  });
  on('status', syncHybrid);
  fill();
}
