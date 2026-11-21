export const goto = (view) => document.dispatchEvent(new CustomEvent('saved:goto', { detail: view }));
