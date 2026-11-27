# World Voice AI - local (offline) AI services. Optional: the assistant uses
# them only when no cloud model can answer (no internet, quotas used up).
#
#   powershell -ExecutionPolicy Bypass -File "C:\Multimedia Labs\Project\backend\start_local_ai.ps1"
#
# Starts (both listen on 127.0.0.1 only, kept loaded):
#   * llama-server  :8091  Qwen3.5 vision-language model (CPU)
#   * local_sidecar :8092  faster-whisper (speech to text) + Piper (speech)
# Stop: close the two windows (or -Stop).

param([string]$Model = "Qwen3.5-0.8B", [int]$Threads = 4, [switch]$Stop)

$root = "C:\Multimedia Labs\local-ai"
if ($Stop) {
    Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "local_sidecar.py" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "Local AI stopped."; exit 0
}

$gguf = Join-Path $root "models\$Model-Q4_K_M.gguf"
$proj = Join-Path $root "models\mmproj-$Model-F16.gguf"
foreach ($f in $gguf, $proj, "$root\llama.cpp\llama-server.exe", "$root\piper-venv\Scripts\python.exe") {
    if (-not (Test-Path $f)) { Write-Host "Missing: $f" -ForegroundColor Red; exit 1 }
}

Start-Process -FilePath "$root\llama.cpp\llama-server.exe" -WindowStyle Minimized -ArgumentList @(
    "-m", "`"$gguf`"", "--mmproj", "`"$proj`"", "--host", "127.0.0.1", "--port", "8091",
    "-t", "$Threads", "-c", "4096", "-np", "1", "--no-webui", "--reasoning-budget", "0")
Start-Process -FilePath "$root\piper-venv\Scripts\python.exe" -WindowStyle Minimized `
    -ArgumentList "`"$PSScriptRoot\local_sidecar.py`""

Write-Host "Starting local AI ($Model, $Threads threads)..."
foreach ($url in "http://127.0.0.1:8091/health", "http://127.0.0.1:8092/health") {
    for ($i = 0; $i -lt 120; $i++) {
        try { if ((Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { break } } catch {}
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  ready: $url" -ForegroundColor Green
}
