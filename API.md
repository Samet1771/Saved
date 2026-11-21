# Saved: code layout and API contract

This is the binding contract between the backend (`app/`) and the web UI (`web/`). Change it here first, then in the code.

## Layout

```
saved/
  DESIGN.md  API.md  README.md
  docker-compose.yml  Dockerfile  .env.example
  requirements.txt  requirements-ml.txt
  app/                 Python 3.12 package
    config.py          env-driven settings (paths, throttle, models, token, batch window)
    db.py              SQLite schema + migrations, sqlite-vec + FTS5
    importer.py        Instagram export parser (zip or json)
    api.py             FastAPI app: /api/*, /media/*, serves web/ at /
    search.py          hybrid search (FTS5 + text vec + visual vec, RRF)
    worker.py          pipeline loop
    stages/fetch.py  stages/extract.py  stages/enrich.py  stages/index.py
    ml/                thin wrappers that load a model, run, and unload (whisper, ocr, llm, text_embed, visual_embed)
  web/                 static, no build step: index.html, app.js (ES modules), styles.css, manifest.webmanifest, sw.js, icons/
  tests/               pytest; ML wrappers are mocked
```

Runtime data lives under `DATA_DIR` (default `/data`):
- `vault.db`
- `media/<shortcode>/...`
- `models/`
- `secrets/burner_cookies.txt` (optional)

## Item model

`type`: `reel` | `video` | `photo` | `carousel`

`status`, in pipeline order:
```
queued → fetched → extracted → enriched → indexed
```
Terminal states:
- `failed`: `error` holds the reason; retryable.
- `private`: skipped. Age- or region-restricted posts ("isn't available to everyone") land here with `error` `restricted: needs login`.
- `gone`: deleted on Instagram.
- `needs_login`: a photo post that Instagram only serves to logged-in users (gallery-dl is redirected to the login page) while no `secrets/burner_cookies.txt` exists. When that file appears or changes (the worker checks its mtime every tick), every `needs_login` item is re-queued once. With cookies present, a login redirect is a plain `failed`.

`private`, `gone`, `needs_login` and `failed` are all retryable through `POST /api/items/{id}/retry`.

### Fetch rate limiting

A yt-dlp error containing `rate-limit`, `429`, `Too Many Requests`, `Please wait a few minutes` or `login required` (reels normally work anonymously) counts as rate limiting:
- An `export` item goes back to `queued` (not `failed`) and moves behind the other queued items. After 3 rate limits in a row it becomes `failed`.
- An `ingest` item is not re-queued: it fails normally (retryable).
- All export fetching pauses with exponential backoff: 15 min, 30 min, 60 min, then 2 h for every further hit. A successful fetch (export or ingest) ends the backoff and resets the schedule. The end time is in `/api/status` as `worker.fetch_backoff_until` and survives a worker restart. Ingest items are still fetched during a backoff.

`source`: `export` | `ingest`. `ingest` items get priority and are processed immediately, outside the batch window.

### ItemSummary (list responses)

```json
{
  "id": 12, "shortcode": "C9xYz...", "url": "https://www.instagram.com/reel/C9xYz.../",
  "type": "reel", "status": "indexed", "author": "someuser",
  "caption": "first 200 chars…", "summary": "one line from LLM or null",
  "category": "cooking", "tags": ["pasta", "recipe"],
  "thumb_url": "/media/C9xYz.../thumb.jpg",
  "media": [{"kind": "video", "url": "/media/C9xYz.../0.mp4", "width": 720, "height": 1280, "duration": 31.2}],
  "saved_at": "2025-03-01T12:00:00Z", "posted_at": "2025-02-27T09:00:00Z",
  "collections": [{"id": 3, "name": "Recipes"}],
  "score": 0.031
}
```

Field notes:
- `media` holds one entry per carousel slide, with `kind` `video` or `image`.
- `score` is present only on search results.
- `thumb_url` and `media` are `null` or `[]` until the item is fetched.

### ItemDetail (`GET /api/items/{id}`)

ItemSummary plus:
- `caption`: the full text.
- `transcript`: `[{"start": 0.0, "end": 3.2, "text": "..."}]`
- `ocr_text`, `lang`, `error`, `manual` (`{"category": bool, "tags": bool}`)
- `enriched_by`: the model that last tagged the item, or `null`: `remote:<model name from the GPU server's /v1/models>` or `local:<.gguf file>` (CPU fallback). Values without a prefix come from the old text-only tagger.
- `description`: 2–4 English sentences from the vision tagger (who/where/what happens/the point), or `null`. Detail only, not in ItemSummary. Searched by FTS and part of the text embedding (right after `summary`).

## Endpoints

### Auth

Sign in with `POST /api/login {username, password, label?}`. It returns `{token, username}`; the token is a session that lasts until sign-out or a password change. Every other `/api/*` route needs `Authorization: Bearer <token>`. That covers reads too, because the vault is reachable over the tailnet. `/media/*` and `/api/events/stream` take `?t=<token>` or the header, since `<video src>` and `EventSource` can't send headers. `/` and the static files are public.

- One account, stored as a scrypt hash in the `settings` table. Create or change it with `python -m app.auth set-password`; that also signs out every session.
- `/api/login` answers 409 when no account exists yet, 401 for a wrong username or password, and 429 after 5 failures from one address in 5 minutes.
- Sessions are kept as SHA-256 hashes in the `sessions` table, never in plain text.
- An optional `API_TOKEN` in `.env` is accepted as a fixed bearer token for scripts. `AUTH_DISABLED=1` turns authentication off (development only).

| Method + path | Body | Returns |
|---|---|---|
| `POST /api/login` | `{username, password, label?}` (no auth) | `{token, username}` |
| `POST /api/logout` | | `{ok}`; revokes the token used for the call |
| `GET /api/account` | | `{username}` |
| `PUT /api/account/password` | `{current_password, new_password}` (8+ chars) | `{ok}`; signs out every other session; 403 if the current password is wrong |
| `POST /api/tokens` | `{label?}` | `{token}`: a session for the Siri Shortcut or scripts |

### Items and search

| Method + path | Body / query | Returns |
|---|---|---|
| `GET /api/items` | `q, category, tag, collection (id), author, type, status, sort, offset=0, limit=60` | `{"total": n, "items": [ItemSummary]}` |
| `GET /api/items/{id}` | | ItemDetail |
| `PATCH /api/items/{id}` | `{"category"?: str, "tags"?: [str]}` | ItemDetail. Manual values are flagged in `manual` and never overwritten by the LLM |
| `POST /api/items/{id}/retry` | | ItemDetail (resets to the failed stage) |
| `POST /api/items/reenrich` | `{"only_other_models": bool = true}` | `{"queued": n}`. See Models |
| `DELETE /api/items/{id}` | | `204`. Removes it from the vault and deletes its media |

`sort` values:
- `saved_desc` (default when `q` is empty)
- `saved_asc`
- `posted_desc`
- `manual`: global manual order, or the collection's order when `collection` is given
- `relevance`: the default and the only meaningful value when `q` is set

`q` is natural language and runs hybrid search. The other params filter.

### Facets

`GET /api/facets` returns:
```json
{"categories": [{"name", "count"}], "tags": [{"name", "count"}], "collections": [{"id", "name", "count"}], "authors": [{"name", "count"}], "types": [{"name", "count"}]}
```
- Tags: the top 200.
- Authors: the top 100.

### Categories (the editable fixed list)

- `GET /api/categories` → `{"categories": [str], "proposed": [{"name", "count"}]}`
  - `proposed`: names the LLM suggested that aren't in the list.
- `PUT /api/categories` with `{"categories": [str]}` → same shape.

### Collections

Collections are user-made. Ones imported from the Instagram export become collections too.

| Method + path | Body | Returns |
|---|---|---|
| `GET /api/collections` | | `[{"id", "name", "count", "cover_url"}]` |
| `POST /api/collections` | `{"name"}` | collection |
| `PATCH /api/collections/{id}` | `{"name"}` | collection |
| `DELETE /api/collections/{id}` | | `204` (items stay) |
| `POST /api/collections/{id}/items` | `{"item_ids": [..]}` | collection. Appends; existing ones are ignored |
| `DELETE /api/collections/{id}/items/{item_id}` | | `204` |

### Manual order

`POST /api/order/move` with `{"item_id", "after_id": int|null, "collection_id": int|null}` → `204`.
- `after_id: null` moves the item to the top.
- `collection_id: null` uses the global manual order.

### Ingest and import

**`POST /api/ingest`** with `{"url"}`
- Returns `{"id", "status", "created": bool}` with `201` when new, `200` when the item already exists.
- Accepted URLs: `/p/`, `/reel/`, `/reels/`, `/tv/`. Query strings are stripped, and a shortcode is required.
- This is what the Siri Shortcut calls. The response must be fast (< 300 ms); processing happens in the worker.

**`POST /api/import`** (multipart, field `file`: the export `.zip` or `saved_posts.json` / `saved_collections.json`)
- Returns `{"added", "existing", "collections"}`.
- Idempotent.

### Status

`GET /api/status` returns:
```json
{"counts": {"queued": n, "...": n}, "disk": {"free_bytes", "total_bytes", "media_bytes", "paused": bool},
 "worker": {"stage": "fetch|extract|enrich|index|idle", "item_id": int|null, "shortcode": str|null, "since": iso,
            "heartbeat": iso, "in_batch_window": bool, "llm_model": "<active local model file>",
            "fetch_backoff_until": iso|null, "llm_backend": "remote|local|null",
            "llm_remote": {"url": str|null, "healthy": bool, "checked_at": iso|null, "model": str|null}},
 "progress": {"fetch": {"done": n, "total": n, "remaining": n, "done_last_hour": n, "avg_s": float|null},
              "extract": {...}, "enrich": {...}, "index": {...}},
 "eta_seconds": int|null,
 "last_errors": [{"id", "shortcode", "status", "error"}]}
```
- `paused` is true when free space is below `MIN_FREE_GB`; ingest still queues, but the fetch stage waits.
- `llm_remote`: the PC GPU llama-server (`LLM_REMOTE_URL`, `null` when unset) as last checked by the worker (`GET /health`, 2 s timeout, cached 30 s); `model` is the name it serves.
- `llm_backend`: `local` while the worker's CPU llama-server is running, else `remote` when the GPU server is healthy, else `null`.
- `progress.<stage>`:
  - `total`: items that are not `private`, `gone` or `needs_login`.
  - `done`: items past that stage (a `failed` item counts as past the stages before its `failed_stage`).
  - `remaining`: items still needing that stage (status `queued` up to the stage's input status).
  - `done_last_hour`: successful item events of that stage in the last 60 min.
  - `avg_s`: mean `duration_s` of the last 50 item events of that stage (successes and failures; model loads and batch events excluded). Fetch time excludes the throttle delay.
- `eta_seconds` = `max(processing, fetching)`, rounded:
  - `processing` = Σ over the four stages of `remaining × avg_s` (time the single worker is busy).
  - `fetching` = `progress.fetch.remaining × ((FETCH_DELAY_MIN + FETCH_DELAY_MAX) / 2 + fetch avg_s)`.
  - The throttle delay between fetches overlaps with extract/enrich/index work, so the two totals are not added: whichever is longer bounds the run.
  - `null` while some stage has remaining items but no timed events yet. It ignores the batch window and fetch backoff.

### Activity events

The worker writes one event per finished item-stage (`"extract done: 42.7 s audio, lang en"`, `"enrich done: cooking · pasta, recipe"`), per terminal outcome (`warn`, e.g. `"private: restricted: needs login"`), per failure (`error`, `"failed: <ErrorType>: <first line>"`), per batch start/end (`"batch extract: 812 items"`, only for batches of 2+), per model load (with load time) and unload (including the local CPU llama-server start/stop), when the remote GPU server comes up or becomes unreachable, per fetch backoff start/end, per disk-guard pause/resume and when `needs_login` items are re-queued. Only the newest 2,000 events are kept.

Event:
```json
{"id": 123, "at": "2026-11-19T10:00:00Z", "level": "info|warn|error", "stage": "fetch|extract|enrich|index",
 "item_id": int|null, "shortcode": str|null, "message": "extract done: 42.7 s audio, lang en", "duration_s": float|null}
```
`duration_s` is the time spent on that item-stage (or the model load time, batch run time, or backoff length).

| Method + path | Query | Returns |
|---|---|---|
| `GET /api/events` | `after_id`, `limit=200` (max 1000) | `{"events": [Event], "last_id": n}`, ascending by `id`. Without `after_id`: the newest `limit` events. `last_id` is the last returned id, else `after_id` (or `0`) |
| `GET /api/events/stream` | `t=<token>` (or the header), optional `after_id` | `text/event-stream` |

The stream (Server-Sent Events):
- On connect it sends the newest 50 events, or every event after `Last-Event-ID` (header, sent by `EventSource` on reconnect) or `after_id`.
- Then it polls the DB every second and pushes new events. Each is a default `message` with `id: <event id>` and the Event JSON as `data`.
- Every 5 s (and right after connect) it sends `event: status` with the same payload as `GET /api/status`.
- `EventSource` can't send headers, so the token goes in `?t=` like `/media`.

### Models

Tagging is a vision request (up to 6 keyframes or photos at 448 px + caption, transcript, OCR) to an OpenAI-compatible llama-server:
- **Remote** (`LLM_REMOTE_URL`, the PC GPU) whenever it is healthy.
- If it is not: `ingest` items use a **local** CPU llama-server the worker starts on demand (and stops after them); `export` items stay `extracted` until the remote is back.

The models below are only for the local fallback. The active one is a `.gguf` file in `models/` stored in the DB; if it is unset or its file is gone, the basename of `LLM_MODEL_PATH` is used. The worker checks it before every local item and restarts its llama-server when it changed. The vision projector is `LLM_MMPROJ_PATH`; without it the fallback tags text-only.

Model payload:
```json
{"active": "Qwen3.5-4B-Q4_K_M.gguf", "default": "Qwen3.5-4B-Q4_K_M.gguf",
 "models": [{"file": "Qwen3.5-4B-Q4_K_M.gguf", "size_bytes": 2710000000}],
 "download": {"state": "idle|downloading|done|error", "repo": str|null, "file": str|null, "bytes": n, "total": n|null, "error": str|null},
 "other_model_items": n}
```
- `models`: `*.gguf` files directly in `models/`, without `*.part` files and files whose name contains `mmproj`.
- `other_model_items`: `enriched`/`indexed` items whose `enriched_by` is neither `local:<active>` nor `remote:<model the GPU server last served>` (this includes `null` and the old unprefixed values).

| Method + path | Body | Returns |
|---|---|---|
| `GET /api/models` | | model payload |
| `PUT /api/models/active` | `{"file"}` | model payload. `422` if not a plain `.gguf` file name, `404` if missing |
| `POST /api/models/download` | `{"repo": "owner/name", "file": "X.gguf" or "sub/dir/X.gguf"}` | `202` + model payload. Saved as `models/X.gguf` |
| `DELETE /api/models/{file}` | | `204`. `409` for the active model, `404` if missing |
| `POST /api/items/reenrich` | `{"only_other_models": bool = true}` | `{"queued": n}` |

Download:
- Streams `https://huggingface.co/{repo}/resolve/main/{file}` to `models/X.gguf.part` in the API process, then renames it. Poll `GET /api/models` for progress.
- `422` for a bad repo or file, `409` if the file exists or another download is running, `507` if free disk would drop below `MIN_FREE_GB`, `404`/`502` if Hugging Face refuses or is unreachable.
- On failure `download.state` is `error` with a message, and the `.part` file is removed.

Re-enrich:
- Moves `enriched`/`indexed` items back to `extracted` so enrich runs again on the remote GPU (whenever it is healthy) and index in the next batch window. With `only_other_models` (the default) only the items counted in `other_model_items` are reset.
- Reset items are also moved to `source: export`, so they wait for the remote GPU instead of being tagged one by one on the CPU as ingest items.
- Tags, category and summary stay visible until the item is re-tagged. Manual category and tags are never overwritten.

### Media

`GET /media/{shortcode}/{file}` serves the file with HTTP Range support (needed for seeking and iOS Safari playback).

## Errors

Errors are returned as `{"detail": "message"}` with the proper status code (`401`, `404`, `422`, `409`, `502`, `507`).
