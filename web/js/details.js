import * as API from './api.js';
import { store } from './store.js';
import { icon, esc, fmtTime, fmtDate, statusPill, RETRYABLE, toast, toastErr, confirmDialog, promptDialog, emit } from './util.js';

const TYPE_LABEL = { reel: 'Reel', video: 'Video', photo: 'Photo', carousel: 'Carousel' };

function taggedBy(by) {
  if (by.startsWith('remote:')) return `${by.slice(7)} on the PC GPU`;
  if (by.startsWith('local:')) return `${by.slice(6)} on the server CPU`;
  return `${by} (text only)`;
}

function summaryOf(d) {
  const { transcript, ocr_text, lang, error, manual, enriched_by, ...rest } = d;
  return { ...rest, caption: (d.caption || '').slice(0, 200) };
}

/**
 * Renders the full detail panel for an item into root.
 * opts.video(): returns the <video> to seek for transcript taps (may change over time).
 * Returns a cleanup function.
 */
export function renderDetails(root, id, opts = {}) {
  let alive = true;
  let item = null;
  let cats = [];
  let cols = [];
  let unbindTime = null;

  root.innerHTML = '<div class="list-foot"><div class="spinner" aria-label="Loading"></div></div>';

  Promise.all([
    API.get(`/items/${id}`),
    API.categories().catch(() => ({ categories: [], proposed: [] })),
    API.collections().catch(() => []),
  ]).then(([d, c, cl]) => {
    if (!alive) return;
    item = d; cats = c.categories || []; cols = cl;
    render();
  }).catch((e) => {
    if (!alive) return;
    root.innerHTML = `<p class="error">${esc(e.message)}</p>`;
  });

  function publish() {
    store.update(summaryOf(item));
    emit('item:update', summaryOf(item));
  }

  function render() {
    const catOptions = [...new Set([...(item.category ? [item.category] : []), ...cats])];
    const memberOf = new Set((item.collections || []).map((c) => c.id));
    root.innerHTML = `
      <div class="details">
        <section>
          <div class="lead">
            <span class="who">@${esc(item.author || 'unknown')}</span>
            ${statusPill(item.status)}
            <span class="muted small">${esc(TYPE_LABEL[item.type] || item.type)}${item.media?.length > 1 ? ` · ${item.media.length} slides` : ''}</span>
          </div>
          ${item.summary ? `<p class="summary">${esc(item.summary)}</p>` : ''}
          ${item.description ? `<p class="description">${esc(item.description)}</p>` : ''}
          ${item.enriched_by ? `<p class="muted small tagged-by">Tagged by ${esc(taggedBy(item.enriched_by))}</p>` : ''}
          ${item.error ? `<p class="error">${esc(item.error)}</p>` : ''}
          <div class="acts">
            <a class="btn sm" href="${esc(/^https?:\/\//.test(item.url || '') ? item.url : '#')}" target="_blank" rel="noopener noreferrer">${icon('ext', 'sm')}Instagram</a>
            ${opts.inFeed ? `<a class="btn sm" href="#/item/${item.id}" data-act="page">${icon('page', 'sm')}Full page</a>` : ''}
            ${RETRYABLE.has(item.status) ? `<button class="btn sm primary" data-act="retry">${icon('retry', 'sm')}Retry</button>` : ''}
            <button class="btn sm ghost danger" data-act="delete">${icon('trash', 'sm')}Delete</button>
          </div>
        </section>

        ${item.transcript?.length ? `<section>
          <span class="label">Transcript</span>
          <div class="transcript">${item.transcript.map((l, i) => `<button type="button" data-seek="${l.start}" data-i="${i}"><time>${fmtTime(l.start)}</time><span>${esc(l.text)}</span></button>`).join('')}</div>
        </section>` : ''}

        ${item.caption ? `<section><span class="label">Caption</span><p class="caption">${esc(item.caption)}</p></section>` : ''}
        ${item.ocr_text ? `<section><span class="label">Text in video</span><pre class="ocr">${esc(item.ocr_text)}</pre></section>` : ''}

        <section>
          <label class="label" for="d-cat-${item.id}">Category${item.manual?.category ? '<span class="manual">edited</span>' : ''}</label>
          <select class="select" id="d-cat-${item.id}" data-act="category">
            <option value="">— none —</option>
            ${catOptions.map((c) => `<option value="${esc(c)}" ${c === item.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}
          </select>
        </section>

        <section>
          <label class="label" for="d-tag-${item.id}">Tags${item.manual?.tags ? '<span class="manual">edited</span>' : ''}</label>
          <div class="tag-editor">
            ${(item.tags || []).map((t) => `<span class="chip">${esc(t)}<button type="button" data-rmtag="${esc(t)}" aria-label="Remove tag ${esc(t)}">${icon('x', 'xs')}</button></span>`).join('')}
            <input id="d-tag-${item.id}" data-act="addtag" placeholder="Add tag…" enterkeyhint="done" autocomplete="off" autocapitalize="off">
          </div>
        </section>

        <section>
          <span class="label">Collections</span>
          <div class="coll-toggles">
            ${cols.map((c) => `<button type="button" class="toggle" data-coll="${c.id}" aria-pressed="${memberOf.has(c.id)}">${icon('check', 'ck')}${icon('plus', 'pl')}${esc(c.name)}</button>`).join('')}
            <button type="button" class="toggle" data-act="newcoll">${icon('folderPlus', 'pl')}New…</button>
          </div>
        </section>

        <section>
          <dl class="kv">
            <dt>Saved</dt><dd>${fmtDate(item.saved_at)}</dd>
            <dt>Posted</dt><dd>${fmtDate(item.posted_at)}</dd>
            ${item.lang ? `<dt>Language</dt><dd>${esc(item.lang)}</dd>` : ''}
            <dt>Shortcode</dt><dd>${esc(item.shortcode)}</dd>
          </dl>
        </section>
      </div>`;
    bindTime();
  }

  function bindTime() {
    unbindTime?.();
    unbindTime = null;
    const v = opts.video?.();
    const lines = root.querySelectorAll('.transcript button');
    if (!v || !lines.length) return;
    let cur = -1;
    const onTime = () => {
      const t = v.currentTime;
      let idx = -1;
      for (let i = 0; i < item.transcript.length; i++) {
        if (t >= item.transcript[i].start) idx = i; else break;
      }
      if (idx === cur) return;
      lines[cur]?.classList.remove('on');
      cur = idx;
      const el = lines[cur];
      if (el) {
        el.classList.add('on');
        const box = el.parentElement;
        if (el.offsetTop < box.scrollTop || el.offsetTop > box.scrollTop + box.clientHeight - el.offsetHeight) {
          box.scrollTo({ top: el.offsetTop - box.clientHeight / 3 });
        }
      }
    };
    v.addEventListener('timeupdate', onTime);
    unbindTime = () => v.removeEventListener('timeupdate', onTime);
    onTime();
  }

  async function savePatch(body, msg) {
    try {
      item = await API.patch(`/items/${item.id}`, body);
      API.invalidateFacets();
      publish();
      render();
      toast(msg);
    } catch (e) { toastErr(e); render(); }
  }

  root.addEventListener('change', onChange);
  root.addEventListener('click', onClick);
  root.addEventListener('keydown', onKey);

  function onChange(e) {
    if (e.target.dataset.act === 'category') savePatch({ category: e.target.value || null }, 'Category saved');
  }

  function onKey(e) {
    const inp = e.target;
    if (inp.dataset.act !== 'addtag') return;
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const vals = inp.value.split(',').map((s) => s.trim().replace(/^#/, '').toLowerCase()).filter(Boolean);
      if (!vals.length) return;
      const tags = [...new Set([...(item.tags || []), ...vals])];
      savePatch({ tags }, 'Tags saved').then(() => root.querySelector('[data-act="addtag"]')?.focus());
    } else if (e.key === 'Backspace' && !inp.value && item.tags?.length) {
      savePatch({ tags: item.tags.slice(0, -1) }, 'Tag removed').then(() => root.querySelector('[data-act="addtag"]')?.focus());
    }
  }

  async function onClick(e) {
    const t = e.target.closest('button, a');
    if (!t || !item) return;
    if (t.dataset.seek != null) {
      const v = opts.video?.();
      if (v) {
        v.currentTime = parseFloat(t.dataset.seek);
        opts.onSeek?.(v);
        bindTime();
      } else toast('No video to seek');
      return;
    }
    if (t.dataset.rmtag != null) {
      return savePatch({ tags: item.tags.filter((x) => x !== t.dataset.rmtag) }, 'Tag removed');
    }
    if (t.dataset.coll) {
      const cid = +t.dataset.coll;
      const on = t.getAttribute('aria-pressed') === 'true';
      t.setAttribute('aria-pressed', String(!on));
      try {
        if (on) await API.del(`/collections/${cid}/items/${item.id}`);
        else await API.post(`/collections/${cid}/items`, { item_ids: [item.id] });
        const c = cols.find((x) => x.id === cid);
        item.collections = on ? item.collections.filter((x) => x.id !== cid) : [...(item.collections || []), { id: cid, name: c.name }];
        API.invalidateCollections();
        API.invalidateFacets();
        publish();
        toast(on ? `Removed from ${c.name}` : `Added to ${c.name}`);
      } catch (err) { t.setAttribute('aria-pressed', String(on)); toastErr(err); }
      return;
    }
    const act = t.dataset.act;
    if (act === 'newcoll') {
      const name = await promptDialog('New collection', { ok: 'Create' });
      if (!name) return;
      try {
        const c = await API.post('/collections', { name });
        await API.post(`/collections/${c.id}/items`, { item_ids: [item.id] });
        cols = await API.collections(true);
        item.collections = [...(item.collections || []), { id: c.id, name: c.name }];
        API.invalidateFacets();
        publish();
        render();
        toast(`Added to ${c.name}`);
      } catch (err) { toastErr(err); }
    } else if (act === 'retry') {
      try {
        item = await API.post(`/items/${item.id}/retry`);
        publish();
        render();
        toast('Queued for retry');
      } catch (err) { toastErr(err); }
    } else if (act === 'delete') {
      if (!(await confirmDialog('Delete this item?', 'It is removed from Saved and its media files are deleted. Your Instagram save is not touched.'))) return;
      try {
        await API.del(`/items/${item.id}`);
        store.remove(item.id);
        emit('item:delete', item.id);
        API.invalidateFacets();
        API.invalidateCollections();
        toast('Deleted');
        opts.onDeleted?.();
      } catch (err) { toastErr(err); }
    } else if (act === 'page') {
      opts.onNavigate?.();
    }
  }

  return {
    rebindVideo: bindTime,
    destroy() {
      alive = false;
      unbindTime?.();
      root.removeEventListener('change', onChange);
      root.removeEventListener('click', onClick);
      root.removeEventListener('keydown', onKey);
    },
  };
}
