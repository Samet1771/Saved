# Saved desktop

A Windows companion app for the Saved server. Tauri 2 with a Rust backend and a plain HTML/CSS/ES-module frontend (no bundler), styled with the web UI's tokens.

## What it does

- **Dashboard**: server health (CPU with a 10-minute sparkline, RAM, disk, load, uptime, process memory), pipeline progress per stage with counts, ETA and badges, worker controls (pause/resume, fetch pause, batch window, retry failed, re-enrich), the GPU PC (llama-server state, GPU stats, start/stop/restart, log tail) and the latest errors with a Retry button.
- **Library**: the server's own web UI in an iframe. The app hands its session to the frame, so you sign in once.
- **Activity**: the live event stream (newest first, 2,000 rows), with stage, level and text filters.
- **Settings**: server URL, sign in with your username and password, path to `tools/pc-llm`, LLM port, start-with-app and close-to-tray.
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
- GPU server log: `%APPDATA%\io.github.samet1771.saved\llm.log`, truncated each time the app starts the server. Key-like text (`--api-key ...`, `LLM_REMOTE_KEY=...`) is masked when the log is shown.

## Safety behaviour

- **Start** runs `start-llm.ps1` through a hidden PowerShell that is detached from the app (`DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`). Quitting or crashing the app does not stop the GPU server, so tagging keeps going. The script reads `key.txt` itself; the app never reads or passes the key.
- **Stop** only ever kills a process named `llama-server.exe` that is listening on the configured port, plus its parent PowerShell only if that parent's command line runs `start-llm.ps1`. If something else holds the port, Stop refuses. Stop and Restart ask for confirmation.
- The llama-server command line contains `--api-key`, so it is never returned, logged or sent to the frontend. Only the model file name (from `-m`) is extracted.
- The app does not start a second GPU server when one is already running on the port.
- "Start GPU server when the app starts" is off by default.

## Security notes

- **CSP** (static in `tauri.conf.json`): `connect-src` and `frame-src` allow `http:` and `https:`, because Tauri's CSP cannot be changed at runtime and the server URL is user-configured (a LAN address). Everything else is `'self'`: scripts, styles (the frontend only sets styles through the CSSOM), fonts, `object-src 'none'`. The window only runs the bundled frontend, and the remote iframe has no access to Tauri commands.
- **Capabilities**: only `core:event:allow-listen` and `allow-unlisten` for the main window (tray actions). Opening links and notifications are Rust commands; `open_external` refuses any URL outside the configured server URL.
- The app allows a single instance; starting it again shows the existing window.
