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
    <div><h1>Saved</h1><p>Enter the API token from the server's <code>API_TOKEN</code> setting.</p></div>
    <label class="field"><span>API token</span>
      <input class="input" id="tok" type="password" required autocomplete="current-password" autocapitalize="off" spellcheck="false"></label>
    <div class="err" role="alert">${esc(msg)}</div>
    <button class="btn primary">${icon('lock', 'sm')}Unlock</button></form>`;
  const form = box.querySelector('form');
  const inp = box.querySelector('#tok');
  inp.focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const tok = inp.value.trim();
    const err = box.querySelector('.err');
    if (!tok) { err.textContent = 'Enter a token.'; return; }
    const btn = form.querySelector('button');
    btn.disabled = true;
    err.textContent = '';
    try {
      await API.get('/status', { tokenOverride: tok });
      API.setToken(tok);
      box.hidden = true;
      box.innerHTML = '';
      document.getElementById('view').removeAttribute('inert');
      document.getElementById('topbar').removeAttribute('inert');
      start();
    } catch (ex) {
      err.textContent = ex.status === 401 ? 'That token was rejected.' : ex.message;
      btn.disabled = false;
      inp.select();
    }
  });
}

window.addEventListener('saved:auth', () => {
  API.setToken(null);
  showLogin('Your token is missing or no longer valid.');
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
