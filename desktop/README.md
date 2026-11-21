# Saved desktop

A Windows companion app for the Saved server. Tauri 2 with a Rust backend and a plain HTML/CSS/ES-module frontend (no bundler), styled with the web UI's tokens.

## What it does

- **Dashboard**: server health (CPU with a 10-minute sparkline, RAM, disk, load, uptime, process memory), pipeline progress per stage with counts, ETA and badges, worker controls (pause/resume, fetch pause, batch window, retry failed, re-enrich), the GPU PC (llama-server state, GPU stats, start/stop/restart, log tail) and the latest errors with a Retry button.
- **Library**: the server's own web UI in an iframe. The app hands its session to the frame, so you sign in once.
- **Activity**: the live event stream (newest first, 2,000 rows), with stage, level and text filters.
- **Settings**: server URL, sign in with your username and password, path to `tools/pc-llm`, LLM port, start-with-app, start PC compute with the app (hybrid mode), reset the agent token, and close-to-tray.
- **PC compute** (only when the server runs in `hybrid` compute mode, `GET /api/status` -> `compute.mode`): the GPU PC card becomes a PC compute card. It starts and stops `tools/pc-compute/start-compute.ps1` (the folder next to `tools/pc-llm`), and shows whether the process runs locally, whether the server sees the agent online, leased items, GPU name, VRAM and utilization, per-stage queue/done/average seconds, llama-server reachability, and the log. The agent heartbeat (`compute.agent.info`) is rendered defensively: known keys are laid out, anything else is listed as key/value, and missing keys are fine. In `server` mode the plain GPU server card is unchanged.
- **Tray**: tooltip with the current stage and GPU state; menu with Show, Start/Stop GPU server, Pause/Resume worker and Quit. Notifications only for the server going away and coming back, the GPU server stopping unexpectedly, and the backlog finishing, each rate-limited.

Ctrl+1 to Ctrl+4 switch views. The Library iframe keeps its own keys (`/`, j/k, space, m, i), and Ctrl+1..4 do not reach the app while the frame has focus.

The server is controlled only through its HTTP API. Server calls are made from the frontend (`fetch` plus `EventSource`); Rust only does local-machine work (config, credentials, GPU stats, llama-server, tray, notifications).

## Run and build

Needs Node, cargo and WebView2 (included in Windows 11).

```bash
cd desktop
npm install
npx tauri dev       # run from source
npx tauri build     # NSIS installer and exe
cd src-tauri && cargo test
```

Output: `src-tauri/target/release/saved-desktop.exe` and `src-tauri/target/release/bundle/nsis/Saved_<version>_x64-setup.exe`.

## Where things are stored

- Settings: `%APPDATA%\io.github.samet1771.saved\config.json` (`server_url`, `pcllm_dir`, `llm_port`, `autostart_llm`, `close_to_tray`, `last_view`). On first run `pcllm_dir` defaults to `tools/pc-llm` when the app runs from a checkout.
- Session: signing in exchanges the username and password for a session token, stored in Windows Credential Manager (service `Saved`, user `session`). The password is never stored, and the token is never written to the JSON, logs or console. The frontend keeps it in memory to call the API (header, and `?t=` for the event stream) and to hand it to the Library frame. Sign out revokes it on the server.
- Agent token: on the first PC compute start the app creates a long-lived token with `POST /api/tokens` (label `PC compute`) using your session, and stores it in Credential Manager (service `Saved`, user `compute_token`). Rust reads it from there when it starts the agent and hands it over only as the `SAVED_TOKEN` environment variable of the child process (with `SAVED_URL`); the frontend sees it only once, at creation. "Reset agent token" (Settings, or the PC compute card when the agent never comes online) creates a new one and restarts a running agent.
- PC compute log: `%APPDATA%\io.github.samet1771.saved\compute.log`, truncated on each start. `SAVED_TOKEN=...`, `Bearer ...` and the llama keys are masked when the log is shown.
- GPU server log: `%APPDATA%\io.github.samet1771.saved\llm.log`, truncated each time the app starts the server. Key-like text (`--api-key ...`, `LLM_REMOTE_KEY=...`) is masked when the log is shown.

## Safety behaviour

- **Start** runs `start-llm.ps1` through a hidden PowerShell that is detached from the app (`DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`). Quitting or crashing the app does not stop the GPU server, so tagging keeps going. The script reads `key.txt` itself; the app never reads or passes the key.
- **Stop** only ever kills a process named `llama-server.exe` that is listening on the configured port, plus its parent PowerShell only if that parent's command line runs `start-llm.ps1`. If something else holds the port, Stop refuses. Stop and Restart ask for confirmation.
- The llama-server command line contains `--api-key`, so it is never returned, logged or sent to the frontend. Only the model file name (from `-m`) is extracted.
- The app does not start a second GPU server when one is already running on the port.
- "Start GPU server when the app starts" is off by default.

## PC compute process handling

- Start spawns a hidden `powershell.exe -File start-compute.ps1` in its own process group, with stdout and stderr appended to `compute.log`. Status is found by scanning for a PowerShell process whose command line runs `start-compute.ps1`, so it survives an app restart.
- Stop takes a snapshot of the launcher's process tree (plus a `llama-server.exe` listening on the LLM port), sends Ctrl+Break to the launcher's console group so the agent can release its leases, waits up to 5 s, then kills whatever is left in the snapshot.
- PC compute and the plain GPU server share the LLM port, so only one runs. Starting PC compute stops a plain GPU server first. Starting the plain GPU server (or stopping it from the app) while PC compute runs is refused with a message.
- The app stops PC compute when it exits, but only if this run of the app started it. Close to tray keeps the app, and PC compute, running. Killing or crashing the app leaves it running; Stop in the next session handles it.

## Security notes

- **CSP** (static in `tauri.conf.json`): `connect-src` and `frame-src` allow `http:` and `https:`, because Tauri's CSP cannot be changed at runtime and the server URL is user-configured (a LAN address). Everything else is `'self'`: scripts, styles (the frontend only sets styles through the CSSOM), fonts, `object-src 'none'`. The window only runs the bundled frontend, and the remote iframe has no access to Tauri commands.
- **Capabilities**: only `core:event:allow-listen` and `allow-unlisten` for the main window (tray actions). Opening links and notifications are Rust commands; `open_external` refuses any URL outside the configured server URL.
- The app allows a single instance; starting it again shows the existing window.
