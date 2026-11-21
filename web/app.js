import * as API from './js/api.js';
import { store } from './js/store.js';
import { setRouteHandler, parseHash, rawParams, libHash, feedHash, silentReplace } from './js/router.js';
import { initLibrary, showLibrary, leaveLibrary, revealItem } from './js/library.js';
import { openFeed, closeFeed, feedOpen } from './js/feed.js';
import { initCollections, showCollections } from './js/collections.js';
import { initSettings, showSettings, leaveSettings } from './js/settings.js';
import { initItemPage, showItem, leaveItem } from './js/itempage.js';
import { initBusy } from './js/activity.js';
import { icon, esc } from './js/util.js';

const views = {
  library: document.getElementById('v-library'),
  collections: document.getElementById('v-collections'),
  settings: document.getElementById('v-settings'),
  item: document.getElementById('v-item'),
};
const scrollPos = {};
let currentView = null;
let feedFromGrid = false;   // the feed was opened by a card tap (so Back returns to the grid entry)
let started = false;

function showView(name) {
  if (currentView === name) return;
  if (currentView) {
    scrollPos[currentView] = scrollY;
    if (currentView === 'library') leaveLibrary();
    if (currentView === 'settings') leaveSettings();
    if (currentView === 'item') leaveItem();
  }
  for (const [k, v] of Object.entries(views)) v.hidden = k !== name;
  const prev = currentView;
  currentView = name;
  document.querySelectorAll('[data-nav]').forEach((a) => {
    const on = a.dataset.nav === (name === 'item' ? '' : name);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  requestAnimationFrame(() => scrollTo(0, name === 'item' ? 0 : scrollPos[name] || 0));
  if (prev) document.getElementById('view').focus({ preventScroll: true });
}

async function route() {
  if (!API.getToken()) return showLogin();
  const { path, query } = parseHash();

  if (path === '/feed') {
    const raw = rawParams(query);
    const start = +query.get('start');
    showView('library');
    await showLibrary(raw);
    if (feedOpen()) return;
    openFeed(start, {
      onIndex: (id) => silentReplace(feedHash(raw, id)),
      onClose: (id, { silent }) => {
        requestAnimationFrame(() => revealItem(id));
        if (silent) return;
        const target = libHash(raw);
        if (feedFromGrid && history.state?.rvFeed) history.back();
        else { history.replaceState(null, '', target); route(); }
        feedFromGrid = false;
      },
    });
    return;
  }
  if (feedOpen()) closeFeed({ silent: true });

  if (path === '/' || path === '') {
    showView('library');
    showLibrary(rawParams(query));
  } else if (path === '/collections') {
    showView('collections');
    showCollections();
  } else if (path === '/settings') {
    showView('settings');
    showSettings();
  } else if (path.startsWith('/item/')) {
    showView('item');
    showItem(+path.split('/')[2]);
  } else {
    history.replaceState(null, '', '#/');
    route();
  }
}

/* Card taps push a feed entry; mark it so closing can use history.back() and keep the grid entry. */
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href^="#/feed"]');
  if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
  e.preventDefault();
  feedFromGrid = true;
  history.pushState({ rvFeed: true }, '', a.getAttribute('href'));
  route();
});

/* ---------------------------------------------------------------- login */
function showLogin(msg = '') {
  closeFeed({ silent: true });
  const box = document.getElementById('login');
  box.hidden = false;
  document.getElementById('view').setAttribute('inert', '');
  document.getElementById('topbar').setAttribute('inert', '');
  box.innerHTML = `<form class="login" novalidate>
    <span class="brand-mark" aria-hidden="true"></span>
    <div><h1>Saved</h1><p>Sign in to your archive.</p></div>
    <label class="field"><span>Username</span>
      <input class="input" id="user" required autocomplete="username" autocapitalize="off" spellcheck="false"></label>
    <label class="field"><span>Password</span>
      <input class="input" id="pass" type="password" required autocomplete="current-password"></label>
    <div class="err" role="alert">${esc(msg)}</div>
    <button class="btn primary">${icon('lock', 'sm')}Sign in</button></form>`;
  const form = box.querySelector('form');
  const user = box.querySelector('#user');
  const inp = box.querySelector('#pass');
  user.focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = box.querySelector('.err');
    if (!user.value.trim() || !inp.value) { err.textContent = 'Enter your username and password.'; return; }
    const btn = form.querySelector('button');
    btn.disabled = true;
    err.textContent = '';
    try {
      const r = await API.post('/login', { username: user.value.trim(), password: inp.value }, { tokenOverride: '' });
      API.setToken(r.token);
      box.hidden = true;
      box.innerHTML = '';
      document.getElementById('view').removeAttribute('inert');
      document.getElementById('topbar').removeAttribute('inert');
      start();
    } catch (ex) {
      err.textContent = ex.message;
      btn.disabled = false;
      inp.select();
    }
  });
}

// The desktop app embeds this UI in a frame and hands over the session token it already has.
const DESKTOP_ORIGINS = ['http://tauri.localhost', 'https://tauri.localhost', 'tauri://localhost'];
window.addEventListener('message', (e) => {
  if (!DESKTOP_ORIGINS.includes(e.origin) || e.data?.type !== 'saved:token' || !e.data.token) return;
  if (e.data.token === API.getToken()) return;
  API.setToken(e.data.token);
  location.reload();
});
if (window.parent !== window) window.parent.postMessage({ type: 'saved:ready' }, '*');

document.addEventListener('keydown', (e) => {
  if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest?.('input, textarea, select, [contenteditable]') || document.getElementById('dlg')?.open) return;
  const q = document.getElementById('q');
  if (q && q.offsetParent !== null) { e.preventDefault(); q.focus(); q.select(); }
});

window.addEventListener('saved:auth', () => {
  API.setToken(null);
  showLogin('Your session ended. Sign in again.');
});

function start() {
  if (!started) {
    started = true;
    initLibrary(views.library);
    initCollections(views.collections);
    initSettings(views.settings);
    initItemPage(views.item);
    setRouteHandler(route);
    addEventListener('hashchange', route);
    initBusy();
  } else {
    store.setParams(store.params || {}, true);
  }
  route();
}

if (API.getToken()) start();
else showLogin();

if ('serviceWorker' in navigator && location.protocol !== 'file:') {
  addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}
