import { app, on } from './state.js';
import { native } from './native.js';
import { createAgentToken } from './api.js';
import { refreshCompute } from './poll.js';
import { toast } from './util.js';

export const isHybrid = () => app.status?.compute?.mode === 'hybrid';

async function newToken() {
  const { token } = await createAgentToken();
  await native.computeTokenSet(token);
}

export async function startCompute() {
  const st = await native.computeStatus();
  if (!st.has_token) await newToken();
  await native.computeStart();
  await refreshCompute();
}

export async function stopCompute() {
  await native.computeStop();
  await refreshCompute();
}

export async function resetComputeToken() {
  const wasRunning = (await native.computeStatus()).running;
  if (wasRunning) await native.computeStop();
  await native.computeTokenClear();
  await newToken();
  if (wasRunning) await native.computeStart();
  await refreshCompute();
}

export async function toggleCompute() {
  try {
    if ((await native.computeStatus()).running) await stopCompute();
    else await startCompute();
  } catch (e) {
    native.notify('Saved: PC compute', String(e)).catch(() => {});
  }
}

export function initCompute() {
  let tried = false;
  on('status', async (s) => {
    if (tried || s?.compute?.mode !== 'hybrid' || !app.config?.autostart_compute || !app.config.pcllm_dir) return;
    tried = true;
    try {
      if (!(await native.computeStatus()).running) await startCompute();
    } catch (e) {
      toast(String(e), 'err');
    }
  });
}
