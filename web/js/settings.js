import * as API from './api.js';
import { libHash } from './router.js';
import { icon, esc, fmtBytes, fmtAgo, statusPill, STATUSES, RETRYABLE, toast, toastErr, copyText, confirmDialog } from './util.js';
import { activityMarkup, initActivity, renderActivity, startActivity, stopActivity, updateBusy } from './activity.js';

let el, timer = null, cats = null, models = null, dlTimer = null;

const BIG_MODEL = 3.2e9;
const gb = (n) => `${((n || 0) / 1e9).toFixed(2)} GB`;

export function initSettings(root) {
  el = root;
  el.innerHTML = `<div class="page">
    <div class="page-head"><h1>Status &amp; settings</h1></div>
    <div class="cards two">
      ${activityMarkup()}
      <section class="panel wide" id="s-status" aria-labelledby="h-status"><h2 id="h-status">Pipeline<button type="button" class="btn sm ghost" data-act="refresh">${icon('retry', 'sm')}Refresh</button></h2><div class="body"><div class="list-foot"><div class="spinner"></div></div></div></section>
      <section class="panel wide" id="s-errors" aria-labelledby="h-errors"><h2 id="h-errors">Recent problems</h2><div class="body"></div></section>
      <section class="panel" aria-labelledby="h-add"><h2 id="h-add">Add a link</h2>
        <p>Paste an Instagram post or reel link. It is queued and processed right away.</p>
        <form class="row" data-form="ingest"><label class="sr-only" for="ingest-url">Instagram URL</label>
          <input id="ingest-url" class="input" type="url" required placeholder="https://www.instagram.com/reel/…" autocomplete="off">
          <button class="btn primary">${icon('plus', 'sm')}Add</button></form>
      </section>
      <section class="panel" aria-labelledby="h-import"><h2 id="h-import">Import export</h2>
        <p>Instagram data export: the <code>.zip</code>, or <code>saved_posts.json</code> / <code>saved_collections.json</code>. Importing again is safe.</p>
        <form class="file-drop" data-form="import"><label class="sr-only" for="imp-file">Export file</label>
          <input id="imp-file" type="file" accept=".zip,.json,application/zip,application/json" required>
          <button class="btn primary">${icon('upload', 'sm')}Import</button></form>
        <div class="result" aria-live="polite"></div>
      </section>
      <section class="panel wide cat-edit" id="s-cats" aria-labelledby="h-cats"><h2 id="h-cats">Categories</h2>
        <p>The fixed list the tagger chooses from. Suggestions are names the model proposed that are not in the list yet.</p>
        <div class="body"></div>
      </section>
      <section class="panel wide" id="s-models" aria-labelledby="h-models"><h2 id="h-models">Tagging model</h2>
        <div class="remote" aria-live="polite"></div>
        <p>Items are tagged on the PC GPU whenever it is reachable. The model picked below is only the server's <b>CPU fallback</b>, used for Siri links while the PC is off (about 3.5 min per item); exported items wait for the PC instead. Pick a Q4_K_M GGUF around 4B parameters. Larger models may not fit in the server's RAM.</p>
        <div class="body"></div>
        <h3 class="label sub-h">Download a model</h3>
        <form class="dl-form" data-form="download">
          <label class="field"><span>Hugging Face repo</span><input id="dl-repo" class="input" required placeholder="unsloth/Qwen3.5-4B-GGUF" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
          <label class="field"><span>File</span><input id="dl-file" class="input" required placeholder="Qwen3.5-4B-Q4_K_M.gguf" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
          <button class="btn primary">${icon('down', 'sm')}Download</button></form>
        <div class="dl" aria-live="polite"></div>
        <h3 class="label sub-h">Re-tag items</h3>
        <div class="retag" aria-live="polite"></div>
      </section>
      <section class="panel wide" aria-labelledby="h-siri"><h2 id="h-siri">Siri Shortcut</h2>
        <p>Add posts straight from Instagram's share sheet.</p>
        <ol class="steps">
          <li>In Shortcuts, create a shortcut and turn on <b>Show in Share Sheet</b> (accepts URLs).</li>
          <li>Add <b>Get Contents of URL</b> with this URL, method <b>POST</b>:
            <div class="row"><pre class="code" id="siri-url"></pre><button type="button" class="btn sm" data-copy="siri-url">${icon('copy', 'sm')}Copy</button></div></li>
          <li>Add a header <b>Authorization</b> with a Shortcut token. It is shown once and stays valid until you change the password:
            <div class="row"><pre class="code" id="siri-auth">Bearer …</pre><button type="button" class="btn sm" data-act="mktoken">${icon('lock', 'sm')}Create token</button><button type="button" class="btn sm" data-copy="siri-auth" hidden>${icon('copy', 'sm')}Copy</button></div></li>
          <li>Request body <b>JSON</b> with one text field <code>url</code> set to <b>Shortcut Input</b>:
            <div class="row"><pre class="code">{"url": "&lt;Shortcut Input&gt;"}</pre></div></li>
          <li>Optional: add <b>Show Notification</b> so you see “queued”. The phone must reach the server (home Wi-Fi or Tailscale).</li>
        </ol>
      </section>
      <section class="panel wide" aria-labelledby="h-account"><h2 id="h-account">Account</h2>
        <p>Signed in as <b class="acct-user">…</b>. Changing the password signs out every other device and Shortcut token.</p>
        <form class="row" data-form="password">
          <label class="sr-only" for="pw-cur">Current password</label>
          <input id="pw-cur" class="input" type="password" required placeholder="Current password" autocomplete="current-password">
          <label class="sr-only" for="pw-new">New password</label>
          <input id="pw-new" class="input" type="password" required minlength="8" placeholder="New password" autocomplete="new-password">
          <button class="btn">Change password</button>
          <button type="button" class="btn ghost danger" data-act="signout">${icon('logout', 'sm')}Sign out</button></form>
      </section>
    </div></div>`;

  initActivity(el);
  el.addEventListener('click', onClick);
  el.addEventListener('submit', onSubmit);
  el.addEventListener('keydown', (e) => {
    if (e.target.id === 'cat-new' && e.key === 'Enter') { e.preventDefault(); addCat(e.target.value); }
  });
}

function renderSiri() {
  el.querySelector('#siri-url').textContent = `${location.origin}/api/ingest`;
  API.get('/account').then((a) => { el.querySelector('.acct-user').textContent = a.username || '(no account)'; }).catch(() => {});
}

async function loadStatus() {
  let s;
  try { s = await API.get('/status'); } catch (e) {
    el.querySelector('#s-status .body').innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  renderActivity(s);
  updateBusy(s);
  el.querySelector('#s-models .remote').innerHTML = remoteHTML(s.worker || {});
  const c = s.counts || {};
  const d = s.disk || {};
  const w = s.worker || {};
  const used = Math.max(0, (d.total_bytes || 0) - (d.free_bytes || 0));
  const pct = (n) => (d.total_bytes ? Math.min(100, (n / d.total_bytes) * 100) : 0);
  el.querySelector('#s-status .body').innerHTML = `
    <div class="stats">${STATUSES.map((k) => `<a class="stat" href="${libHash({ status: k })}"><b>${c[k] ?? 0}</b>${statusPill(k, k.replace('_', ' '))}</a>`).join('')}</div>
    <h2 style="margin-top:18px">Disk</h2>
    <div class="meter" role="img" aria-label="${fmtBytes(d.free_bytes)} free of ${fmtBytes(d.total_bytes)}">
      <i class="m-media" style="width:${pct(d.media_bytes || 0)}%"></i><i class="m-used" style="width:${pct(Math.max(0, used - (d.media_bytes || 0)))}%"></i></div>
    <div class="legend"><span style="--c:var(--accent)">Media ${fmtBytes(d.media_bytes)}</span><span style="--c:var(--text-3)">Other ${fmtBytes(Math.max(0, used - (d.media_bytes || 0)))}</span><span style="--c:var(--surface-3)">Free ${fmtBytes(d.free_bytes)} of ${fmtBytes(d.total_bytes)}</span></div>
    ${d.paused ? '<div class="alert warn"><b>Downloads paused:</b> free space is below the safety limit. New links still queue; fetching resumes when space is freed.</div>' : ''}
    <h2 style="margin-top:18px">Worker</h2>
    <div class="worker">${w.stage && w.stage !== 'idle' ? '<span class="status-pill st-processing">running</span>' : '<span class="status-pill st-private">idle</span>'}
      <span>Stage: <span class="wstage">${esc(w.stage || 'idle')}</span></span>
      ${w.item_id ? `<span>Item <a href="#/item/${+w.item_id}">#${+w.item_id}</a></span>` : ''}
      ${w.since ? `<span>since ${esc(fmtAgo(w.since))}</span>` : ''}
      <span>${w.in_batch_window ? 'In the nightly batch window' : 'Outside the batch window (only new links are processed)'}</span></div>`;
  const errs = s.last_errors || [];
  el.querySelector('#s-errors .body').innerHTML = errs.length
    ? `<ul class="errlist">${errs.map((x) => `<li>${statusPill(x.status)}<div class="e"><code>${esc(x.shortcode)}</code><div>${esc(x.error || '')}</div></div>
        <a class="btn sm ghost" href="#/item/${+x.id}">Open</a>
        ${RETRYABLE.has(x.status) ? `<button type="button" class="btn sm" data-retry="${+x.id}">${icon('retry', 'sm')}Retry</button>` : ''}</li>`).join('')}</ul>`
    : '<p>No failed, private, login-only or deleted items.</p>';
}

function remoteHTML(w) {
  const r = w.llm_remote || {};
  if (!r.url) return '<div class="alert warn"><b>No PC GPU configured</b> (<code>LLM_REMOTE_URL</code> is empty). Exported items are not tagged until it is set.</div>';
  const local = w.llm_backend === 'local' ? ' The CPU fallback is running for a Siri link.' : '';
  return r.healthy
    ? `<p><span class="status-pill st-indexed">PC GPU online</span> <code>${esc(r.url)}</code>${r.model ? ` · ${esc(r.model)}` : ''}${r.checked_at ? ` · checked ${esc(fmtAgo(r.checked_at))}` : ''}${esc(local)}</p>`
    : `<p><span class="status-pill st-failed">PC GPU unreachable</span> <code>${esc(r.url)}</code>${r.checked_at ? ` · checked ${esc(fmtAgo(r.checked_at))}` : ''}. Exported items wait until it is back.${esc(local)}</p>`;
}

function renderCats() {
  const body = el.querySelector('#s-cats .body');
  if (!cats) { body.innerHTML = '<div class="list-foot"><div class="spinner"></div></div>'; return; }
  body.innerHTML = `
    <div class="chips">${cats.categories.map((c) => `<span class="chip">${esc(c)}<button type="button" data-rmcat="${esc(c)}" aria-label="Remove ${esc(c)}">${icon('x', 'xs')}</button></span>`).join('') || '<span class="muted small">No categories.</span>'}</div>
    <div class="row"><label class="sr-only" for="cat-new">New category</label><input id="cat-new" class="input" placeholder="Add a category" autocomplete="off" enterkeyhint="done">
      <button type="button" class="btn" data-act="addcat">${icon('plus', 'sm')}Add</button></div>
    ${cats.proposed?.length ? `<div class="proposed"><span class="label" style="align-self:center">Suggested</span>${cats.proposed.map((p) => `<button type="button" class="btn sm" data-addcat="${esc(p.name)}">${icon('plus', 'sm')}${esc(p.name)} <span class="muted">${p.count}</span></button>`).join('')}</div>` : ''}`;
}

function renderModels() {
  const body = el.querySelector('#s-models .body');
  if (!models) { body.innerHTML = '<div class="list-foot"><div class="spinner"></div></div>'; return; }
  const m = models;
  const missing = !m.models.some((x) => x.file === m.active);
  body.innerHTML = `${m.models.length ? `<ul class="models">${m.models.map((x) => {
    const on = x.file === m.active;
    return `<li class="${on ? 'on' : ''}"><div class="m"><code>${esc(x.file)}</code>
      <span class="muted small">${gb(x.size_bytes)}${x.file === m.default ? ' · default' : ''}</span>
      ${x.size_bytes > BIG_MODEL ? `<span class="warn-badge" title="May not fit in the server's RAM">${icon('alert', 'xs')}Large</span>` : ''}</div>
      ${on ? '<span class="status-pill st-indexed">Active</span>' : `<button type="button" class="btn sm" data-use="${esc(x.file)}">Use</button>`}
      <button type="button" class="btn sm icon ghost danger" data-rmmodel="${esc(x.file)}" ${on ? 'disabled title="The active model can’t be deleted"' : ''} aria-label="Delete ${esc(x.file)}">${icon('trash', 'sm')}</button></li>`;
  }).join('')}</ul>` : '<p>No models downloaded yet.</p>'}
    ${missing ? `<div class="alert warn"><b>${esc(m.active)}</b> is the active model but its file is not in the models folder. Tagging fails until it is downloaded or another model is picked.</div>` : ''}`;

  const d = m.download || {};
  const dl = el.querySelector('#s-models .dl');
  if (d.state === 'downloading') {
    const pct = d.total ? Math.min(100, (d.bytes / d.total) * 100) : 0;
    dl.innerHTML = `<div class="small">Downloading <code>${esc(d.file)}</code></div>
      <div class="meter" role="progressbar" aria-label="Download progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}"><i class="m-media" style="width:${pct}%"></i></div>
      <div class="muted small">${gb(d.bytes)} of ${d.total ? gb(d.total) : 'unknown size'}</div>`;
  } else if (d.state === 'error') {
    dl.innerHTML = `<p class="error">Download of ${esc(d.file || 'the model')} failed: ${esc(d.error || 'unknown error')}</p>`;
  } else if (d.state === 'done') {
    dl.innerHTML = `<p class="result">Downloaded <code>${esc(d.file)}</code>. Press Use to switch to it.</p>`;
  }
  el.querySelector('[data-form="download"] button').disabled = d.state === 'downloading';

  const retag = el.querySelector('#s-models .retag');
  if (!retag.dataset.done) {
    const n = m.other_model_items || 0;
    retag.innerHTML = `<p>${n} item${n === 1 ? '' : 's'} tagged by another model or without vision. Re-tagging runs on the PC GPU whenever it is reachable; manual categories and tags are kept.</p>
      <button type="button" class="btn" data-act="retag" ${n ? '' : 'disabled'}>${icon('retry', 'sm')}Re-tag items</button>`;
  }
}

async function loadModels() {
  clearTimeout(dlTimer);
  dlTimer = null;
  try { models = await API.get('/models'); } catch (e) {
    el.querySelector('#s-models .body').innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  renderModels();
  if (models.download?.state === 'downloading' && timer) dlTimer = setTimeout(loadModels, 2000);
}

async function useModel(file) {
  try {
    models = await API.put('/models/active', { file });
    el.querySelector('#s-models .retag').dataset.done = '';
    renderModels();
    toast(`CPU fallback: ${file}`);
  } catch (e) { toastErr(e); }
}

async function deleteModel(file) {
  if (!await confirmDialog('Delete model?', `${file} is removed from the server's disk.`)) return;
  try {
    await API.del(`/models/${encodeURIComponent(file)}`);
    toast(`Deleted ${file}`);
    loadModels();
  } catch (e) { toastErr(e); }
}

async function retagItems() {
  const n = models?.other_model_items || 0;
  if (!await confirmDialog(`Re-tag ${n} item${n === 1 ? '' : 's'}?`,
    'They are tagged again on the PC GPU whenever it is reachable. Current tags stay visible until then.', { ok: 'Re-tag', danger: false })) return;
  const box = el.querySelector('#s-models .retag');
  try {
    const r = await API.post('/items/reenrich', { only_other_models: true });
    box.dataset.done = '1';
    box.innerHTML = `<p class="result"><b>Queued ${+r.queued} item${r.queued === 1 ? '' : 's'}</b> for re-tagging on the PC GPU.</p>`;
    loadStatus();
  } catch (e) { toastErr(e); }
}

async function putCats(list, msg) {
  try {
    cats = await API.put('/categories', { categories: list });
    API.setCategoriesCache(cats);
    API.invalidateFacets();
    renderCats();
    toast(msg);
  } catch (e) { toastErr(e); }
}

function addCat(v) {
  const name = String(v || '').trim().toLowerCase();
  if (!name) return;
  if (cats.categories.includes(name)) return toast('Already in the list');
  putCats([...cats.categories, name], `Added ${name}`).then(() => el.querySelector('#cat-new')?.focus());
}

async function onClick(e) {
  const t = e.target.closest('button, a');
  if (!t) return;
  if (t.dataset.copy) {
    return copyText(el.querySelector(`#${t.dataset.copy}`).textContent);
  }
  if (t.dataset.retry) {
    try { await API.post(`/items/${t.dataset.retry}/retry`); toast('Queued for retry'); loadStatus(); } catch (err) { toastErr(err); }
    return;
  }
  if (t.dataset.rmcat) return putCats(cats.categories.filter((c) => c !== t.dataset.rmcat), `Removed ${t.dataset.rmcat}`);
  if (t.dataset.addcat) return addCat(t.dataset.addcat);
  if (t.dataset.use) return useModel(t.dataset.use);
  if (t.dataset.rmmodel) return deleteModel(t.dataset.rmmodel);
  switch (t.dataset.act) {
    case 'refresh': loadStatus(); break;
    case 'addcat': addCat(el.querySelector('#cat-new').value); break;
    case 'retag': retagItems(); break;
    case 'mktoken':
      try {
        const r = await API.post('/tokens', { label: 'Siri Shortcut' });
        el.querySelector('#siri-auth').textContent = `Bearer ${r.token}`;
        el.querySelector('[data-copy="siri-auth"]').hidden = false;
        toast('Token created. Copy it now; it is not shown again.');
      } catch (err) { toastErr(err); }
      break;
    case 'signout':
      if (await confirmDialog('Sign out?', 'This browser is signed out.', { ok: 'Sign out' })) {
        await API.post('/logout').catch(() => {});
        API.setToken(null);
        window.dispatchEvent(new CustomEvent('saved:auth'));
      }
      break;
    default:
  }
}

async function onSubmit(e) {
  e.preventDefault();
  const f = e.target;
  const btn = f.querySelector('button:not([type="button"])');
  btn.disabled = true;
  try {
    if (f.dataset.form === 'ingest') {
      const inp = f.querySelector('input');
      const r = await API.post('/ingest', { url: inp.value.trim() });
      toast(r.created ? `Queued as #${r.id}` : `Already saved (#${r.id}, ${r.status})`);
      inp.value = '';
      loadStatus();
    } else if (f.dataset.form === 'import') {
      const file = f.querySelector('input[type=file]').files[0];
      if (!file) return;
      const out = el.querySelector('.result');
      out.textContent = `Uploading ${file.name}…`;
      const fd = new FormData();
      fd.append('file', file);
      const r = await API.post('/import', fd);
      out.innerHTML = `<b>Done.</b> ${+r.added} new, ${+r.existing} already saved, ${+r.collections} collections.`;
      API.invalidateFacets();
      API.invalidateCollections();
      f.reset();
      loadStatus();
    } else if (f.dataset.form === 'password') {
      await API.put('/account/password', { current_password: f.querySelector('#pw-cur').value, new_password: f.querySelector('#pw-new').value });
      f.reset();
      toast('Password changed. Other devices were signed out.');
    } else if (f.dataset.form === 'download') {
      el.querySelector('#s-models .dl').innerHTML = '';
      models = await API.post('/models/download', { repo: f.querySelector('#dl-repo').value.trim(), file: f.querySelector('#dl-file').value.trim() });
      f.reset();
      loadModels();
      return;
    }
  } catch (err) {
    if (f.dataset.form === 'download') {
      el.querySelector('#s-models .dl').innerHTML = `<p class="error">${esc(err.message)}</p>`;
      return;
    }
    if (f.dataset.form === 'import') el.querySelector('.result').textContent = `Import failed: ${err.message}`;
    toastErr(err);
  } finally { btn.disabled = false; }
}

export function showSettings() {
  renderSiri();
  startActivity();
  loadStatus();
  API.categories(true).then((c) => { cats = c; renderCats(); }).catch(toastErr);
  renderCats();
  clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden) loadStatus(); }, 10000);
  el.querySelector('#s-models .retag').dataset.done = '';
  loadModels();
}

export function leaveSettings() { stopActivity(); clearInterval(timer); timer = null; clearTimeout(dlTimer); dlTimer = null; }
