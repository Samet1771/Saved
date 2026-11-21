param(
    [string]$Dir = "$PSScriptRoot",
    [int]$Port = 8099,
    [int]$Parallel = 1,
    [int]$CtxPerSlot = 0,
    [string]$KvType = ''
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# One slot keeps the original 64k f16 cache. With more slots the cache is shared out of the same VRAM budget:
# 36k tokens per slot fit 120 frames (~200 tokens each) plus text, and q8_0 halves the cache.
if ($CtxPerSlot -le 0) { $CtxPerSlot = if ($Parallel -gt 1) { 36864 } else { 65536 } }
if (-not $KvType) { $KvType = if ($Parallel -gt 1) { 'q8_0' } else { 'f16' } }
$KvArgs = if ($KvType -eq 'f16') { @() } else { '--cache-type-k', $KvType, '--cache-type-v', $KvType }
$Build = 'b12558'
$LlamaDir = Join-Path $Dir 'llama-cuda'
$ModelsDir = Join-Path $Dir 'models'
$KeyFile = Join-Path $PSScriptRoot 'key.txt'
# Optional machine-specific overrides (gitignored): set $LocalLlama, $LocalModel and $LocalMmproj
# to use files that already exist elsewhere instead of downloading them.
$LocalLlama = $LocalModel = $LocalMmproj = $null
$Overrides = Join-Path $PSScriptRoot 'paths.local.ps1'
if (Test-Path $Overrides) { . $Overrides }
$Models = @(
    @{ Name = 'Qwen3.5-9B-Q4_K_M.gguf'; Local = $LocalModel; Url = 'https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf' },
    @{ Name = 'qwen35-9b-mmproj-F16.gguf'; Local = $LocalMmproj; Url = 'https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/mmproj-F16.gguf' }
)

function Get-File($Url, $Dest) {
    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile "$Dest.part" -UseBasicParsing
    Move-Item "$Dest.part" $Dest -Force
}

if (-not (Test-Path $KeyFile)) {
    $alphabet = 'abcdefghijkmnpqrstuvwxyz23456789'
    $bytes = New-Object byte[] 32
    (New-Object Security.Cryptography.RNGCryptoServiceProvider).GetBytes($bytes)
    $newKey = -join ($bytes | ForEach-Object { $alphabet[$_ % 32] })
    Set-Content -Path $KeyFile -Value $newKey -NoNewline -Encoding ASCII
    Write-Host "Created $KeyFile. Put this in the server .env as: LLM_REMOTE_KEY=$newKey" -ForegroundColor Yellow
}
$Key = (Get-Content $KeyFile -Raw).Trim()

New-Item -ItemType Directory -Force $LlamaDir, $ModelsDir | Out-Null

if ($LocalLlama -and (Test-Path (Join-Path $LocalLlama 'llama-server.exe'))) {
    $Server = Join-Path $LocalLlama 'llama-server.exe'
} else {
    if (-not (Get-ChildItem $LlamaDir -Recurse -Filter 'llama-server.exe' -ErrorAction SilentlyContinue)) {
        foreach ($name in "llama-$Build-bin-win-cuda-13.5-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip") {
            $zip = Join-Path $Dir $name
            Get-File "https://github.com/ggml-org/llama.cpp/releases/download/$Build/$name" $zip
            Expand-Archive $zip -DestinationPath $LlamaDir -Force
            Remove-Item $zip
        }
    }
    $Server = (Get-ChildItem $LlamaDir -Recurse -Filter 'llama-server.exe' | Select-Object -First 1).FullName
}

$Paths = foreach ($m in $Models) {
    if ($m.Local -and (Test-Path $m.Local)) { $m.Local; continue }
    $dest = Join-Path $ModelsDir $m.Name
    if (-not (Test-Path $dest)) { Get-File $m.Url $dest }
    $dest
}

# llama-server logs to stderr; with 'Stop', Windows PowerShell treats redirected stderr as a fatal error.
$ErrorActionPreference = 'Continue'
$CrashLog = Join-Path $PSScriptRoot 'crashes.log'
$Restarts = @()
while ($true) {
    Write-Host "Serving $($Paths[0]) on port $Port, $Parallel slot(s) of $CtxPerSlot tokens (Ctrl+C to stop)"
    & $Server -m $Paths[0] --mmproj $Paths[1] `
        -ngl 99 -c ($CtxPerSlot * $Parallel) -np $Parallel --host 0.0.0.0 --port $Port --api-key $Key --no-webui --reasoning off @KvArgs
    $code = $LASTEXITCODE
    Add-Content -Path $CrashLog -Value "$(Get-Date -Format s) llama-server exited with code $code"
    # Restart after a crash so tagging keeps going, but give up if it keeps dying.
    $Restarts = @($Restarts | Where-Object { $_ -gt (Get-Date).AddMinutes(-10) }) + (Get-Date)
    if ($Restarts.Count -gt 3) {
        Write-Host "llama-server exited 4 times in 10 minutes; not restarting. See $CrashLog" -ForegroundColor Red
        break
    }
    Write-Host "llama-server exited with code $code; restarting in 5 s" -ForegroundColor Yellow
    Start-Sleep 5
}
