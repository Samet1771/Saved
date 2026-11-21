# PC GPU compute agent

In hybrid mode (`COMPUTE_MODE=hybrid` on the server) the home server only downloads, stores, serves and searches. This agent runs on a Windows PC with an NVIDIA GPU and does all the processing, over the [Work API](../../API.md#work-hybrid-mode):

| Stage | What runs on the GPU PC |
|---|---|
| extract | faster-whisper (large-v3-turbo, fp16) speech, RapidOCR on frames, keyframes |
| enrich | Qwen3.5-9B vision model in llama.cpp (`llama-server`, 2 parallel slots) |
| index | EmbeddingGemma-2 text vector, SigLIP2-base image vectors (bf16) |

The agent imports the repo's own `app.stages.*.compute` functions, so results match what the server would produce. It never opens the server database. The three stages run concurrently with every model resident on the GPU; an item's media is downloaded once into a local cache and removed after it is indexed.

## Requirements

- Windows 10/11, NVIDIA GPU with 16 GB (tested on RTX 5070 Ti, Blackwell sm_120), recent driver
- Python 3.12 (`py -3.12`), ffmpeg and ffprobe on `PATH`
- `tools/pc-llm` set up once (see its README): `key.txt` and, if your files live elsewhere, `paths.local.ps1`

## Configure

Set two environment variables, or put the same values in `tools/pc-compute/agent.local.json` (gitignored):

```json
{"url": "http://<server-ip>:8080", "token": "<token from web Settings → Siri Shortcut → Create token>"}
```

Optional JSON keys / env vars: `cache_dir` / `COMPUTE_CACHE`, `llm_port` / `LLM_PORT` (8099), `enrich_threads` / `COMPUTE_ENRICH_THREADS` (2, keep equal to llama-server `-np`), `cache_max_gb` / `COMPUTE_CACHE_MAX_GB` (10), `backlog` / `COMPUTE_BACKLOG` (10 items extracted but not yet indexed), `threads` / `THREADS` (4), `name` / `COMPUTE_AGENT_NAME` (PC name).

Big files can go on another drive: in the gitignored `tools/pc-llm/paths.local.ps1` set `$ComputeVenv` and `$ComputeCache`, for example

```powershell
$ComputeVenv = 'E:\saved-llm\compute-venv'
$ComputeCache = 'E:\saved-llm\compute-cache'
```

Defaults are `tools/pc-compute/.venv` and `tools/pc-compute/cache`. The cache holds Hugging Face weights (`hf/`), the Whisper model, per-item working files (`items/`) and logs (`logs/`).

## Run

```powershell
$env:SAVED_URL = 'http://<server-ip>:8080'
$env:SAVED_TOKEN = '<token>'   # or start it from the desktop app's PC compute card, which handles the token
powershell -ExecutionPolicy Bypass -File .\tools\pc-compute\start-compute.ps1
```

The first run creates the venv, installs the CUDA builds (a few GB) and downloads the model weights; later runs start in about a minute (model loading). The script then:

1. starts `tools/pc-llm/start-llm.ps1 -Parallel 2` hidden (or reuses a llama-server that already answers on the port, and leaves that one running afterwards),
2. waits for it to be healthy,
3. runs `agent.py` in the foreground, and stops the llama-server it started when the agent exits.

Parameters: `-ServerUrl`, `-LlmPort`, `-Parallel`, `-NoLlm`. Anything else is passed to `agent.py` (`--idle-exit <s>` exits after that long without work, `--grace <s>` is how long in-flight items may finish on shutdown, default 60).

## Stop

Ctrl+C, SIGTERM or Ctrl+Break (`CTRL_BREAK_EVENT` for a process started with `CREATE_NEW_PROCESS_GROUP`): the agent lets running items finish, gives every other leased item back with `POST /api/work/release` and exits. A hard kill is also safe: leases expire after 15 minutes and the items are claimed again. If you kill the script hard, also kill its child tree (`taskkill /PID <pid> /T /F`), otherwise the hidden llama-server keeps running.

## Logs

- Agent: stdout and `<cache>/logs/agent.log`, one line per item per stage with its time, for example `extract ABC123  6.1 s  whisper 2.0 s | ocr 3.2 s (30 frames) | 12 lines, 8 segments, 8 keyframes`
- llama-server: `<cache>/logs/llama-out.log` and `llama-err.log`
- The server's `GET /api/status` shows the agent under `compute` (online, GPU, VRAM, queue lengths, per-stage averages)

The API token and the llama key are never printed or logged.

## Behaviour notes

- Failures: an exception posts `/fail` with the error type, message and last frames. Transient problems (llama-server down or restarting, server unreachable, CUDA out of memory) are not reported as failures: the item is released and retried, up to 3 times, and enrich claims pause while llama-server is unreachable.
- A 409 from the server means the item changed (retried or edited); it is dropped.
- The server's pause switch is honoured: while paused the agent claims nothing.
- OCR runs on the CPU by default. RapidOCR on the GPU measured no faster on 336 px frames and costs about 0.8 GB VRAM; set `OCR_GPU=1` to force it.
- VRAM is tight on a 16 GB card shared with the desktop. `start-llm.ps1 -Parallel 2` uses 36k tokens per slot with a q8_0 KV cache for that reason; do not run other CUDA programs while the agent is working, a CUDA out-of-memory in llama-server crashes it (it is restarted automatically).
