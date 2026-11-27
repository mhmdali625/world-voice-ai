# DEVELOPMENT ONLY - offline natural-voice test (zero TTS quota).
#
# Starts the normal backend on port 8000, but every spoken
# answer streams the saved Fola recording
# (test_audio\fola_sample_ar-LB.wav) through the REAL
# speech-job / single-stream path instead of calling Gemini TTS.
#
# Whisper (Groq) and image analysis (Gemini vision) still run
# normally, so the whole phone flow can be tested.
#
# Usage (from the backend folder):
#   .\dev_offline_tts.ps1              # normal: 1.5x real time
#   .\dev_offline_tts.ps1 -Rate 0.63   # slow: measured free-tier speed
#
# Change speed while it runs (between answers), from another terminal:
#   Invoke-RestMethod -Method Post "http://127.0.0.1:8000/dev/tts-test?rate=0.63"
#   Invoke-RestMethod -Method Post "http://127.0.0.1:8000/dev/tts-test?rate=1.5"
#
# Never use this for the demo. Close the window to stop it;
# a new terminal without these variables runs the real voice.

param(
    [double]$Rate = 1.5,
    [double]$FirstAudio = 1.0,
    [ValidateSet("smooth", "fast")]
    [string]$Policy = "smooth"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$wav = Join-Path $PSScriptRoot "test_audio\fola_sample_ar-LB.wav"
if (-not (Test-Path $wav)) {
    Write-Error "Test audio not found: $wav"
}

$busy = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
if ($busy) {
    Write-Error "Port 8000 is already in use (process $($busy[0].OwningProcess)). Stop the running backend first."
}

$env:WV_TTS_PROVIDER = "file"
$env:WV_TTS_TEST_WAV = $wav
$env:WV_TTS_TEST_RATE = "$Rate"
$env:WV_TTS_TEST_FIRST_AUDIO = "$FirstAudio"
$env:WV_TTS_START = $Policy

Write-Host ""
Write-Host "=== OFFLINE VOICE TEST MODE (no TTS quota) ===" -ForegroundColor Yellow
Write-Host "Audio : $wav"
Write-Host "Speed : x$Rate real time  |  first audio after $FirstAudio s  |  start policy: $Policy"
Write-Host "Every answer plays the SAME saved Fola sentence; that is expected."
Write-Host ""

& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000
