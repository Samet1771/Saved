# PC GPU tagger

Runs llama.cpp's `llama-server` (CUDA build `b12558`) with Qwen3.5-9B Q4_K_M and its vision projector on this PC's GPU (64k context, one slot, ~9.5 GB VRAM). The Saved server sends each reel's frames at 2 fps plus transcript and OCR, ~12 s per reel. While this PC is off, items wait.

## Run

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\pc-llm\start-llm.ps1
```

On the first run it:
- creates `key.txt` with a random 32-character key and prints a line to put in the server's `.env` as `LLM_REMOTE_KEY=...`;
- uses llama.cpp and the model from the paths in an optional `paths.local.ps1` next to the script (gitignored; see below) if they exist, otherwise downloads the llama.cpp CUDA build plus cudart and the two model files (~6.6 GB) from GitHub and Hugging Face.

It then serves in the foreground on port 8099 (all interfaces, API key required) until you press Ctrl+C. Options: `-Port 8099`, `-Dir <folder for llama/ and models/>`.

## One-time setup (do this yourself)

1. **Firewall.** Allow the port only from the Saved server. In an **admin** PowerShell:
   ```powershell
   New-NetFirewallRule -DisplayName "Saved LLM" -Direction Inbound -Protocol TCP -LocalPort 8099 -RemoteAddress <server-ip> -Action Allow
   ```
   Windows may also ask about `llama-server.exe` on the first start. Don't press Cancel there: that adds a block rule, and block rules win over the allow rule above.
2. **Fixed IP.** Give this PC a DHCP reservation in the router , because the server's `.env` points at it:
   ```
   LLM_REMOTE_URL=http://<gpu-pc-ip>:8099
   LLM_REMOTE_KEY=<contents of key.txt>
   ```
   Then on the server: `docker compose up -d` (env changes need a container restart).

## Verify from the server

```bash
curl -s http://<gpu-pc-ip>:8099/health                     # {"status":"ok"}
curl -s -H "Authorization: Bearer $LLM_REMOTE_KEY" http://<gpu-pc-ip>:8099/v1/models
```

In the vault UI, Settings → Tagging model shows "PC GPU online" within about 30 s, and the Activity log records `remote GPU up`.

## Using files you already have

To use a llama.cpp build or model files that already exist elsewhere on this PC, create `tools/pc-llm/paths.local.ps1` (gitignored):

```powershell
$LocalLlama = 'D:\llama.cpp'                           # folder with llama-server.exe
$LocalModel = 'D:\models\Qwen3.5-9B-Q4_K_M.gguf'
$LocalMmproj = 'D:\models\mmproj-F16.gguf'
```
