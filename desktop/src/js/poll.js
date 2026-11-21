import { app, emit, pushHist } from './state.js';
import { native } from './native.js';

let llmBusy = false;
let gpuBusy = false;

export async function refreshLlm() {
  if (llmBusy) return;
  llmBusy = true;
  try {
    app.llm = await native.llmStatus();
    emit('llm');
  } catch { /* backend unavailable */ } finally {
    llmBusy = false;
  }
}

async function refreshGpu() {
  if (gpuBusy) return;
  gpuBusy = true;
  try {
    app.gpu = await native.gpuStats();
    if (app.gpu?.utilization_pct != null) pushHist(app.gpuHist, app.gpu.utilization_pct);
    emit('gpu');
  } catch { /* backend unavailable */ } finally {
    gpuBusy = false;
  }
}

export function startPolling() {
  refreshLlm();
  refreshGpu();
  setInterval(refreshLlm, 3000);
  setInterval(refreshGpu, 4000);
}
