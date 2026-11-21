const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

export const native = {
  configGet: () => invoke('config_get'),
  configSet: (config) => invoke('config_set', { config }),
  tokenGet: () => invoke('token_get'),
  tokenSet: (token) => invoke('token_set', { token }),
  gpuStats: () => invoke('gpu_stats'),
  llmStatus: () => invoke('llm_status'),
  llmStart: () => invoke('llm_start'),
  llmStop: () => invoke('llm_stop'),
  llmLogTail: (lines) => invoke('llm_log_tail', { lines }),
  trayUpdate: (tooltip, gpuRunning, workerPaused) => invoke('tray_update', { tooltip, gpuRunning, workerPaused }),
  notify: (title, body) => invoke('notify', { title, body }),
  openExternal: (url) => invoke('open_external', { url }),
  onTrayAction: (fn) => listen('tray-action', (e) => fn(e.payload)),
};
