# Saved

A self-hosted archive of the Instagram reels and posts you've saved. It downloads them, transcribes the speech, reads the on-screen text, has a local vision model describe what happens in each video, and lets you search everything in plain language from a phone-friendly web app with a Reels-style feed.

Everything runs on your own hardware. No cloud APIs.

Design and the benchmark behind each choice: [DESIGN.md](DESIGN.md). Code layout and API contract: [API.md](API.md).

## How it works

```
Instagram export / iOS share sheet
        │
        ▼
 fetch    yt-dlp / gallery-dl, anonymous (public posts only)
 extract  Whisper large-v3-turbo (speech) + RapidOCR PP-OCRv5 on 2 fps frames (on-screen text)
 enrich   Qwen3.5-9B vision model on a GPU PC: 2 fps frames + transcript + OCR
          → timeline, visual details, description, category, tags, summary
 index    EmbeddingGemma-2 (text) + SigLIP 2 (frames) in sqlite-vec, FTS5 for keywords
        │
        ▼
 search   BM25 + text vectors + visual vectors, merged by reciprocal rank fusion
```

Two processes from one Docker image:

- **api**: FastAPI on port 8080. It serves `/api/*`, `/media/*` (with HTTP Range) and the web UI at `/`.
- **worker** (`python -m app.worker`): runs the pipeline `queued → fetched → extracted → enriched → indexed`.
  - Items added from the share sheet go through every stage right away.
  - Items from the Instagram export are fetched spread out over the day. Extract and index run in batches inside `BATCH_WINDOW`.
  - Enrich runs on a GPU machine on your LAN (`LLM_REMOTE_URL`, see [tools/pc-llm](tools/pc-llm/README.md)). While it is off, items wait in `extracted`.
  - Only one model family is loaded at a time, and each is unloaded after its batch.

The pipeline was chosen with a benchmark on 20 real reels: 7 speech models, 6 OCR options, 16 vision-model configs and 15 embedders, then every combination of the finalists. The results are in [DESIGN.md](DESIGN.md).

## Hardware it was built for

- **Server:** a 4-core CPU with 16 GB RAM and no usable GPU, running an unprivileged Proxmox LXC (4 GB RAM) with Docker. Fetch, extract, index and search run here.
- **GPU PC:** any machine with ~10 GB of VRAM that runs llama.cpp (tested on an RTX 5070 Ti, about 12 s per reel). It only needs to be on while new items are being tagged.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `API_TOKEN` | empty | Optional fixed bearer token for scripts, accepted next to sign-in sessions |
| `AUTH_DISABLED` | `0` | `1` turns authentication off (local development only) |
| `COMPUTE_MODE` | `server` | `server`: everything on the server. `hybrid`: a GPU PC does extract, enrich and index through `/api/work/*`; the server only fetches. `setup.sh` asks |
| `DATA_DIR` | `/data` | `vault.db`, `models/`, `secrets/` |
| `MEDIA_DIR` | `$DATA_DIR/media` | Downloaded media (`<shortcode>/0.mp4`, `thumb.jpg`, keyframes) |
| `MEDIA_HOST_DIR` | `./data/media` | compose only: host directory mounted at `/media` |
| `TZ` | `Europe/Istanbul` | Time zone of `BATCH_WINDOW` |
| `BATCH_WINDOW` | `01:00-07:00` | `HH:MM-HH:MM` (may wrap midnight), `always` or `never` |
| `FETCH_DELAY_MIN` / `MAX` | `20` / `40` | Random pause after each fetch, per download slot, in seconds |
| `FETCH_SLOTS` | `1` | Downloads running at once. They share one rate-limit back-off: a 429 pauses all of them |
| `MIN_FREE_GB` | `5` | Fetching pauses below this much free space on the media disk |
| `THREADS` | `4` | CPU threads for Whisper, OCR and torch |
| `WHISPER_MODEL` | `deepdml/faster-whisper-large-v3-turbo-ct2` | CTranslate2 large-v3-turbo (int8 at load time) |
| `KEYFRAMES` | `8` | Keyframes kept per video, for visual search and thumbnails |
| `VLM_FPS` / `VLM_FRAME_WIDTH` / `VLM_MAX_FRAMES` | `2` / `336` / `120` | Frames sent to the vision model; longer videos are thinned evenly |
| `LLM_REMOTE_URL` | (empty) | OpenAI-compatible llama-server on the GPU PC, e.g. `http://<gpu-pc-ip>:8099` |
| `LLM_REMOTE_KEY` | (empty) | Sent as `Authorization: Bearer`; the GPU PC's `tools/pc-llm/key.txt` |
| `LLM_LOCAL_FALLBACK` | `0` | `1` = share-sheet items use a slow CPU llama-server on the server while the GPU is off |
| `LLM_MODEL_PATH` / `LLM_MMPROJ_PATH` / `LLM_CTX` | Qwen3.5-4B files / `4096` | Model, vision projector and context of that CPU fallback |
| `TEXT_EMBED_MODEL` | `google/embeddinggemma-2` | Text embeddings. Changing it rebuilds the text index and re-indexes every item |
| `TEXT_EMBED_DIM` | (from the model) | Only needed for models the code doesn't know |
| `VISUAL_EMBED_MODEL` | `google/siglip2-base-patch16-224` | Frame and query embeddings (768-d) |
| `SEARCH_MODELS` | `1` | `0` = keyword-only search in the api: no ML libraries are loaded, which saves about 2 GB of RAM |
| `SYSTEM_MEM_BYTES` | (from the cgroup) | RAM total shown in the status, for LXCs whose memory limit isn't visible from inside |
| `CORS_ORIGINS` | the desktop app's origins | Browser origins allowed to call the API |

Hugging Face models download on first use into `DATA_DIR/models` (`HF_HOME`).

### RAM

- **api**: about 150 MB. With `SEARCH_MODELS=1`, the first search loads EmbeddingGemma-2 and SigLIP 2 (about 1.5 GB); they unload again after `SEARCH_IDLE_UNLOAD` seconds idle (default 900).
- **worker**: peak about 2–3 GB (Whisper turbo int8 about 1.5 GB; the index models about 1.5 GB; never both at once).

## Deploy: Proxmox unprivileged Debian LXC with Docker

1. On the Proxmox host, create the data directories and the container:
   ```bash
   mkdir -p /var/lib/saved/media /var/lib/saved/data
   pct create <ctid> local:vztmpl/debian-13-standard_13.*_amd64.tar.zst --hostname saved \
     --unprivileged 1 --features nesting=1,keyctl=1 --cores 4 --memory 4096 --swap 1024 \
     --rootfs local:16 --net0 name=eth0,bridge=vmbr0,ip=dhcp --onboot 1
   # Unprivileged CTs map uid 0 to 100000 on the host:
   chown -R 100000:100000 /var/lib/saved
   pct set <ctid> -mp0 /var/lib/saved/media,mp=/srv/saved/media,backup=0
   pct set <ctid> -mp1 /var/lib/saved/data,mp=/srv/saved/data,backup=0
   pct start <ctid>
   ```
   - Give the container a fixed IP (a DHCP reservation in your router), so the share-sheet shortcut and the GPU PC's firewall rule can point at it.
   - `backup=0` keeps media out of vzdump; back it up separately if you want to.
2. Inside the container, install Docker:
   ```bash
   apt update && apt install -y ca-certificates curl git
   curl -fsSL https://get.docker.com | sh
   ```
3. Put the project in `/srv/saved` and configure it:
   ```bash
   # /srv/saved already holds the two mount points, so clone elsewhere and copy in:
   git clone https://github.com/Samet1771/Saved.git /tmp/saved && cp -a /tmp/saved/. /srv/saved/
   cd /srv/saved
   cp .env.example .env    # set MEDIA_HOST_DIR=/srv/saved/media
   ```
4. Run the setup. It asks where processing should run, downloads only that mode's models, builds and starts the containers, and creates your sign-in account:
   ```bash
   ./setup.sh
   ```
   - **Server only:** everything runs on the server's CPU. No PC needed, but each reel takes minutes.
   - **Hybrid:** a Windows PC with an NVIDIA GPU does extract, enrich and index ([tools/pc-compute](tools/pc-compute/README.md), or the desktop app's PC compute card). The server only fetches, stores, serves and searches. Items wait while the PC is off.

   Change the mode later with `./setup.sh --reconfigure`. Change the password with `docker compose exec api python -m app.auth set-password`; `python -m app.auth sign-out-all` revokes every session.
5. Hybrid only: start PC compute on the GPU PC (see [tools/pc-compute](tools/pc-compute/README.md)).
6. Import your saves: in Instagram, request **Download your information** in **JSON** format. Then use the import button in the UI, or:
   ```bash
   TOKEN=$(curl -s -H 'Content-Type: application/json' -d '{"username":"<you>","password":"<password>"}' \
     http://<server-ip>:8080/api/login | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
   curl -H "Authorization: Bearer $TOKEN" -F file=@instagram-export.zip http://<server-ip>:8080/api/import
   ```
   The relevant files are `your_instagram_activity/saved/saved_posts.json` and `saved_collections.json`; uploading the whole zip is fine.
7. Optional burner account: if anonymous fetches fail (photo posts usually need a login), put a Netscape-format cookies file from a **burner** account at `data/secrets/burner_cookies.txt`.
   - Only failed items are retried with it.
   - Never use your main account.

Update yt-dlp and gallery-dl regularly, because Instagram changes often: `docker compose build --no-cache && docker compose up -d`.

## iOS share sheet shortcut

1. Shortcuts app → **+** → name it *Saved*.
2. In the shortcut's settings (ⓘ), turn on **Show in Share Sheet** and keep only **URLs** and **Text** as share sheet types.
3. Add **Get URLs from Input** (input: Shortcut Input).
4. Add **Get Contents of URL**:
   - URL: `http://<server-ip>:8080/api/ingest`
   - Method: **POST**
   - Headers: `Authorization` = `Bearer <token>`. Create the token in the web UI under **Settings → Siri Shortcut → Create token**. It stays valid until you change the password.
   - Request Body: **JSON**, with key `url` (Text) set to the *URLs* variable from step 3.
5. Add **Get Dictionary Value** `status` from *Contents of URL*, then **Show Notification**: `Saved: <Dictionary Value>`.
6. In Instagram, tap Share → *Saved*. The response comes back immediately; the worker picks the item up within about a minute.

Away from home, the shortcut works over a VPN that reaches your LAN, for example a Tailscale subnet router.

## Local development

Windows or Linux, no Docker needed:

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt pytest httpx      # Linux: .venv/bin/pip
.venv/Scripts/python -m pytest
```

Run the api with demo data for UI work. This uses keyword-only search, so no ML libraries are needed; ffmpeg must be on PATH.

```bash
DATA_DIR=./data-demo .venv/Scripts/python scripts/seed_demo.py --reset
DATA_DIR=./data-demo .venv/Scripts/python -m app.auth set-password --username dev
DATA_DIR=./data-demo SEARCH_MODELS=0 .venv/Scripts/python -m uvicorn app.api:app --port 8080 --reload
# http://localhost:8080/  sign in as dev
```

`requirements-ml.txt` (torch CPU, torchvision, faster-whisper, sentence-transformers, transformers, rapidocr + onnxruntime, Pillow) is needed only by the worker and by vector search. The `app/ml/*` wrappers import them lazily.

## Desktop app

A Windows app in [desktop/](desktop/README.md) (Tauri 2) that shows the server's health and pipeline progress, controls the worker, starts and stops the GPU PC's llama-server, and embeds the web UI. It talks to the server only through the HTTP API. Build it with `cd desktop && npm install && npx tauri build`.

## A note on use

This is a personal archiving tool. It only fetches posts that are publicly visible, and it is meant for your own saves. Respect Instagram's terms and the rights of the people who made the content; don't republish what it downloads.

## License

[MIT](LICENSE)
