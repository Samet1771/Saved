import { app, on } from '../state.js';
import { native } from '../native.js';
import { goto } from '../goto.js';
import { h, toast } from '../util.js';

let root;
let frame = null;
let loadedUrl = '';

const origin = () => {
  try { return new URL(app.config.server_url).origin; } catch { return null; }
};

function renderEmpty() {
  root.replaceChildren(h('div', { class: 'empty' },
    h('h2', null, 'No server yet'),
    h('p', { class: 'muted' }, 'Set the server URL and sign in from Settings to browse your library here.'),
    h('button', { class: 'btn primary', onclick: () => goto('settings') }, 'Open Settings')));
  frame = null;
  loadedUrl = '';
}

function ensureFrame() {
  const url = app.config?.server_url;
  if (!url) return renderEmpty();
  if (frame && loadedUrl === url) return;
  loadedUrl = url;
  frame = h('iframe', { class: 'frame', title: 'Saved library', src: url, allow: 'fullscreen; autoplay; picture-in-picture', allowFullscreen: true });
  root.replaceChildren(
    h('div', { class: 'libbar' },
      h('button', { class: 'btn sm ghost', onclick: () => history.back() }, 'Back'),
      h('button', { class: 'btn sm ghost', onclick: () => { frame.src = loadedUrl; } }, 'Reload'),
      h('span', { class: 'grow' }, url),
      h('button', { class: 'btn sm', onclick: () => native.openExternal(loadedUrl).catch((e) => toast(String(e), 'err')) }, 'Open in browser')),
    frame);
}

export function init(el) {
  root = el;
  window.addEventListener('message', (e) => {
    if (!frame || e.source !== frame.contentWindow || e.origin !== origin() || e.data?.type !== 'saved:ready' || !app.token) return;
    frame.contentWindow.postMessage({ type: 'saved:token', token: app.token }, e.origin);
  });
  on('config', () => { if (!root.hidden) ensureFrame(); else { frame = null; loadedUrl = ''; root.replaceChildren(); } });
}

export function onShow() {
  ensureFrame();
}
