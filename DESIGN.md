# Saved — design

Self-hosted archive of everything saved on Instagram: download, transcribe, tag, categorize, search in natural language.

Status keys: `locked` (agreed), `proposed` (my recommendation, awaiting your OK), `open`.

## 1. Requirements

| # | Requirement | Status |
|---|---|---|
| R1 | Import all saved items from the Instagram data export (`saved_posts.json`, `saved_collections.json`) | locked |
| R2 | Everything saved from **public** accounts: reels, videos, photo posts, carousels. Private-account posts are out of scope | locked |
| R3 | ~500 items at start, slow growth after | locked |
| R4 | Mostly English content and queries, with some Turkish | locked |
| R10 | Avoid tying downloads to the main Instagram account where possible | locked |
| R11 | Downloaded videos and photos are kept permanently, so the archive survives deletion on Instagram | locked |
| R12 | Web UI works on PC, tablet and phone screens | locked |
| R5 | Fully local, no cloud APIs | locked |
| R6 | New saves arrive by a Siri Shortcut sending the link from the share sheet | locked |
| R7 | Speech to text (Whisper), tagging, auto-categorizing, natural-language search | locked |
| R8 | Manual sorting and searching, which Instagram lacks | locked |
| R9 | Runs as a container on the home server | locked |

## 2. Server constraints (measured 2026-11-18)

| Item | Value | Consequence |
|---|---|---|
| CPU | i5-4590, 4 cores / 4 threads, AVX2 + FMA, no AVX-512 | CPU-only inference; every ML stage is a batch job, not instant |
| GPU | Intel HD 4600 iGPU | No usable compute for Whisper/LLM; ignore |
| RAM | 16 GB total, ~5.2 GB available | Run ML stages **one at a time**; peak budget ≈ 3–3.5 GB |
| `local-lvm` thin pool | 79 % used, **~11.7 GB free** | Can't host the media library |
| `local` (root dir storage) | 29 GB free | Container rootfs + DB fits; media fits short-term only |
| Cloud drive | rclone remote already on the host | Natural home for media backups |

## 3. Pipeline (proposed)

```
export JSON ─┐
Siri link ───┴─► queue ─► fetch ─► extract ─► enrich ─► index ─► web UI / search
```

1. **Queue.** SQLite table of URLs with a per-item state (`queued → fetched → transcribed → tagged → indexed`, or `failed` with the reason). Every stage is resumable and idempotent.
2. **Fetch.** `yt-dlp` for reels and videos, `gallery-dl` for photos and carousels. Login is tiered (see §7): anonymous first, burner-account cookies only for the items that fail, and the main account never. Items from private accounts are marked `private` and skipped. Throttle: one item every ~20–40 s with jitter. At that rate 500 items take ~4 h; once is enough. Save the caption, author, hashtags, post date and thumbnail. Items deleted on Instagram are marked `gone`, and their export entry is kept.
3. **Extract.**
   - Speech: `faster-whisper` **large-v3-turbo int8** with language auto-detect and VAD on → **locked** (2026-11-19). Est. ~1.5 GB RAM. Turbo because `small` is weak on Turkish.
     - **Benchmark.** 11 real reels, compared with Whisper large-v3 and Qwen3-ASR-1.7B; results kept with the benchmark notes.
     - **Qwen3-ASR.** Better at sung lyrics, but garbled Turkish dialogue and proper nouns, and once came out in Chinese. Roughly 2–3× slower on CPU.
     - **large-v3.** No better than turbo overall, slower, and hallucinated lines in a long Turkish clip.
     - **VAD off.** Catches lyrics but hallucinates over music, so VAD stays on.
   - On-screen text: RapidOCR (PP-OCRv5) on frames at 2 fps, skipping near-duplicate frames; each line is kept with the time it first appears.
   - Photos: OCR only.
4. **Enrich.** → **locked** (2026-11-21, chosen by the 20-reel funnel benchmark): Qwen3.5-9B Q4_K_M (thinking off) with its vision projector, on the PC GPU only.
   - **How.** Frames at 2 fps (336 px, timestamp-labelled, thinned evenly above 120), the Whisper transcript with segment times and the OCR lines with first-seen times go in one request. JSON out: `timeline[]` (start, end, event), `visual` (setting, people, objects, on-screen text, format, audio), `description`, `category`, `tags[]`, `summary`. Timeline and visual are stored in `items.details`. The Instagram caption and author are not sent.
   - **Why.** Benchmark scores out of 160 (timeline, visual, meaning, no inventions): Whisper + OCR + Qwen3.5-9B 119, Qwen3-ASR + OCR + Qwen3.5-9B 118, Whisper + OCR + Qwen3-VL-8B 103, old Qwen3.5-4B 96. 12.6 s per reel on the RTX 5070 Ti. Qwen3-ASR tied with Whisper but has no timestamps, so it was dropped.
   - **While the PC is off,** every item waits in `extracted`. The 9B does not fit the server's 4 GB container; `LLM_LOCAL_FALLBACK=1` re-enables the old CPU path for Siri items.
   - Categories come from a fixed list you edit, plus "other". The LLM proposes new ones, and nothing gets created silently.
5. **Index.**
   - Text embeddings: `google/embeddinggemma-2` (768-d, query/document prompts) → **locked** (2026-11-21). Benchmark MRR on 40 queries: 0.948 vs 0.938 for bge-m3; on the server 154 ms per query and 1.2 GB RAM vs 165 ms and 2.0 GB. Needs torchvision, so the image pins torch 2.14. Changing `TEXT_EMBED_MODEL` rebuilds `text_vec` and re-indexes everything.
   - Full text: SQLite FTS5.
   - Storage: `sqlite-vec` in the same SQLite file.
   - Visual (v1) → **locked**:
     - **Why.** Finds visual queries ("a dog on a skateboard") when nothing is said or written.
     - **How.** 8 keyframes per video, plus every photo, embedded with SigLIP 2 base (multilingual text side, ~0.4 B params, ~1 GB RAM). The search text is embedded with the same model and compared against the frames.
     - **Cost estimate.** ~0.3–0.5 s per frame on CPU, so 500 items × 5 frames ≈ 15–20 min.
6. **Search.** Hybrid: three ranked lists (BM25, text-embedding, visual) merged by reciprocal rank fusion, then filters (category, tag, collection, author, type, date).
7. **Web UI** (R12, one responsive app, no separate mobile app). → **proposed**
   - Layout:
     - Phone: 2-column grid.
     - Tablet: 3–4 columns.
     - PC: 5–6 columns plus a side panel for filters and item details.
   - Touch-sized controls. Filters move into a bottom sheet on phones.
   - Installable as a PWA, so it opens full-screen from the phone or tablet home screen like an app.
   - Playback: the stored MP4 files are served directly. Instagram already delivers H.264/AAC, which plays on all three device types, so there's no transcoding. HTTP range requests enable seeking.
   - Item view: video or carousel, caption, transcript (tap a line to seek), tags, category, link back to Instagram.
   - **Swipe feed** → **locked**:
     - **Opening and order.** Tapping a grid item opens a full-screen vertical feed starting at that item. The feed's order is whatever list it was opened from: search results, filter, collection, or manual order.
     - **Gestures.** Swipe up/down for next/previous, carousels swipe sideways, tap to pause.
     - **Playback.** Autoplay muted until the first tap, then with sound. Only the current video plays, and the next one is preloaded.
     - **Details.** Caption, tags and transcript are in a pull-up panel, so the video stays full-screen.
     - **Other screens.** Tablet and PC get the same feed (arrow keys / mouse wheel on PC), plus the detail page as an option.
   - Thumbnail grid with inline playback.
   - Search box, filters, your own collections and drag-to-reorder manual order.
   - Edit tags and category by hand. Manual edits win over the LLM.

## 4. Throughput estimate

These are **estimates**, not measured. They'll be benchmarked on the server before anything is locked.

- Average reel ≈ 30 s of audio. Turbo int8 on 4 Haswell threads is assumed to run ~2–4× faster than real time, so ~8–15 s per reel → **500 reels ≈ 1–2 h**.
- LLM tagging with a 4B model: ~500 input tokens + ~80 output tokens. Assume ~20–40 tok/s prompt processing and ~5–8 tok/s generation → ~25–40 s per item → **500 items ≈ 3.5–5.5 h**.
- Embeddings: negligible (minutes).
- **Total first run: one night.** One new link: ~1 min end to end.

## 5. Siri Shortcut ingest (proposed)

- Share sheet → Shortcut "Saved" → `POST /api/ingest {url}` with a bearer token. It returns "queued" and the item is processed in the background.
- Remote access → **locked**:
  - **Mechanism.** An existing Tailscale subnet router already advertises the home LAN, so the phone reaches the server's LAN IP from anywhere with Tailscale on. Nothing new to install.
  - **Requirement.** The CT needs a fixed IP (static, or a DHCP reservation).
  - **Tradeoff.** Remote access depends on the machine running the subnet router being up.
  - **Not verified.** That the route is approved in the Tailscale admin console.
- It doesn't remove anything from Instagram; your Instagram saves remain unchanged.

## 6. Deployment (proposed)

- New **unprivileged Debian LXC** (`nesting=1`, `keyctl=1`) running Docker Compose, which matches how the rest of the server is built. 4 cores (shared), 4 GB RAM.
- Containers (both from one image):
  - `api` (FastAPI + UI)
  - `worker` (pipeline stages, run one at a time)
- The LLM is not in-process: enrich calls an OpenAI-compatible llama-server. Normally the PC GPU's (vision is ~3.5 min/reel on this CPU, measured); for Siri items while the PC is off, the worker spawns the prebuilt CPU `llama-server` from `/opt/llama` as a subprocess and kills it afterwards. No separate `llm` container, so no container has to start another one.
- Schedule: the batch runs after 01:00 so it avoids the evening backup and normal use. Siri items get processed right away (fetch + Whisper + LLM ≈ 1 min).
- Media → **locked**:
  - **Where.** Stored on the server's own disk in host directories on `local`, bind-mounted into the container with `backup=0`:
    - `/var/lib/saved/media` → `/srv/saved/media`
    - `/var/lib/saved/data` → `/srv/saved/data` (DB and models, ~10 GB)
    - `/var/lib/saved/docker` → `/var/lib/docker` (images, ~2.5 GB)
  - **Why this split.** `local` can't hold container root disks, so only an 8 GB OS rootfs sits on `local-lvm`. This keeps the full thin pool safe and keeps vzdump small.
  - **Format.** Videos downloaded at ≤720p.
  - **Size.** 500 items ≈ 4–8 GB (estimate).
  - **Safeguard.** The UI shows disk usage, and ingest pauses below 5 GB free so the host's root disk can never fill.
- Backup: add the CT to the nightly vzdump **excluding** the media volume. Sync media to the cloud drive with rclone separately.

## 6b. Deployed (2026-11-19)

- **Container.** LXC `saved`, Debian 13, unprivileged, nesting + keyctl, 4 cores, 4 GB RAM + 2 GB swap, DHCP with a router reservation. Docker 26 from Debian apt.
- **Memory limits.** `api` is capped at 3 GB; its bf16 query models use ~2.2 GB while loaded and unload after 15 min idle. `worker` is capped at 3.5 GB.
- **First real item verified.** A public NASA reel went through every stage without a login: fetch 720×1280, Whisper (no speech), OCR, Qwen3.5 tags, both embeddings, search.
- **Measured:** Qwen3.5-4B takes 22–35 s per item; loading the model takes 15 s.

## 7. Risks

- **Instagram account.** Tiered fetch, so the main account is never at risk by default:
  1. **Anonymous:** no cookies. Works only for public posts, and only as far as Instagram currently allows logged-out access, which changes often. Rate limits hit the home IP, not an account. The success rate is **unknown until tested** on a sample from the real export.
  2. **Burner account cookies**, only for public posts that anonymous access fails to fetch. If a checkpoint happens, it hits the burner. The main account is never used (R2 excludes private posts).
- **Disk.** Only ~11.7 GB is free in the thin pool. A second disk would remove the constraint.
- **RAM.** Another always-on service on a host with ~5 GB free. ML models load per stage and unload after.

## 8. Open decisions

1. ~~Media storage~~ → locked: server's `local` disk with a free-space guard (§6).
2. ~~LLM~~ → locked: Qwen3.5-4B Q4_K_M, in-process (§3 step 4). The 20-item benchmark still runs, to measure speed and tag quality, not to choose a model.
3. ~~Remote access~~ → locked: existing Tailscale subnet route (§5).
4. ~~Visual search~~ → locked: v1, SigLIP 2 (§3 step 5).
5. ~~Phone item view~~ → locked: vertical swipe feed (§3 step 7).
