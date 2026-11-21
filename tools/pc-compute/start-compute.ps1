param(
    [string]$ServerUrl = $env:SAVED_URL,
    [int]$LlmPort = 8099,
    [int]$Parallel = 2,
    [switch]$NoLlm,
    [Parameter(ValueFromRemainingArguments)][string[]]$AgentArgs = @()
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$LlmDir = Join-Path $PSScriptRoot '..\pc-llm'
$ComputeVenv = $ComputeCache = $null
$Overrides = Join-Path $LlmDir 'paths.local.ps1'
if (Test-Path $Overrides) { . $Overrides }
if (-not $ComputeVenv) { $ComputeVenv = Join-Path $PSScriptRoot '.venv' }
if (-not $ComputeCache) { $ComputeCache = Join-Path $PSScriptRoot 'cache' }
$Python = Join-Path $ComputeVenv 'Scripts\python.exe'
$Requirements = Join-Path $PSScriptRoot 'requirements-pc.txt'
New-Item -ItemType Directory -Force (Join-Path $ComputeCache 'logs') | Out-Null

function Invoke-Pip {
    & $Python -m pip install --disable-pip-version-check -q @args
    if ($LASTEXITCODE -ne 0) { throw "pip install failed ($args)" }
}

if (-not (Test-Path $Python)) {
    Write-Host "Creating venv in $ComputeVenv"
    py -3.12 -m venv $ComputeVenv
    if ($LASTEXITCODE -ne 0) { throw 'could not create the venv (needs Python 3.12: py -3.12)' }
}
$Stamp = Join-Path $ComputeVenv '.requirements-hash'
$Want = (Get-FileHash $Requirements).Hash
if (-not (Test-Path $Stamp) -or (Get-Content $Stamp -Raw).Trim() -ne $Want) {
    Write-Host 'Installing packages (first run downloads a few GB)'
    Invoke-Pip --upgrade pip
    Invoke-Pip torch torchvision --index-url https://download.pytorch.org/whl/cu128
    Invoke-Pip -r $Requirements
    # faster-whisper depends on the CPU onnxruntime, which shadows onnxruntime-gpu
    & $Python -m pip show onnxruntime 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & $Python -m pip uninstall -y onnxruntime | Out-Null
        Invoke-Pip --force-reinstall --no-deps onnxruntime-gpu
    }
    Set-Content -Path $Stamp -Value $Want -NoNewline
}

if ($ServerUrl) { $env:SAVED_URL = $ServerUrl }
$env:COMPUTE_CACHE = $ComputeCache
$env:LLM_PORT = $LlmPort

function Test-Llm {
    try { (Invoke-RestMethod "http://127.0.0.1:$LlmPort/health" -TimeoutSec 3).status -eq 'ok' } catch { $false }
}

$Llm = $null
if ($NoLlm) {
    Write-Host 'Not starting llama-server (-NoLlm)'
} elseif (Test-Llm) {
    Write-Host "llama-server already answers on port $LlmPort; reusing it (it is left running when the agent stops)"
} else {
    $LogDir = Join-Path $ComputeCache 'logs'
    $Llm = Start-Process powershell -WindowStyle Hidden -PassThru `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$LlmDir\start-llm.ps1`"", '-Port', $LlmPort, '-Parallel', $Parallel `
        -RedirectStandardOutput (Join-Path $LogDir 'llama-out.log') -RedirectStandardError (Join-Path $LogDir 'llama-err.log')
    Write-Host "Started llama-server (pid $($Llm.Id), $Parallel slots), waiting for it to load"
    $deadline = (Get-Date).AddMinutes(45)
    while (-not (Test-Llm)) {
        if ($Llm.HasExited) { throw "llama-server launcher exited; see $LogDir\llama-out.log" }
        if ((Get-Date) -gt $deadline) { throw 'llama-server did not become healthy in 45 minutes' }
        Start-Sleep 3
    }
}

try {
    & $Python -u (Join-Path $PSScriptRoot 'agent.py') @AgentArgs
    $code = $LASTEXITCODE
} finally {
    if ($Llm -and -not $Llm.HasExited) {
        Write-Host 'Stopping llama-server'
        & taskkill.exe /PID $Llm.Id /T /F | Out-Null
    }
}
exit $code
