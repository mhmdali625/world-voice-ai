# World Voice AI - Windows Firewall rules (run ONCE, as Administrator)
#
#   Right-click PowerShell > Run as administrator, then:
#   powershell -ExecutionPolicy Bypass -File "C:\Multimedia Labs\Project\backend\setup_firewall.ps1"
#
# Why: on a phone hotspot or university Wi-Fi, Windows usually classes the
# network as "Public" and blocks connections from the phone to the laptop.
#
# What is allowed (and nothing more):
#   * program: the Python interpreter that runs the backend;
#   * TCP 8000 (API) and UDP (WebRTC video, random ports chosen by aiortc);
#   * ONLY from the local subnet or Tailscale (100.64.0.0/10) - never the internet;
#   * Private and Public profiles (home Wi-Fi, hotspot, university Wi-Fi).
# Optional: -Metro also opens TCP 8081 for Expo development (not needed with the APK).
# Remove:   -Remove deletes these rules.

param([switch]$Metro, [switch]$Remove)

$ErrorActionPreference = "Stop"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Please run this script as Administrator." -ForegroundColor Red
    exit 1
}

$names = "World Voice AI - API (TCP 8000)", "World Voice AI - WebRTC video (UDP)", "World Voice AI - Expo Metro (TCP 8081)"
foreach ($n in $names) { Get-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue | Remove-NetFirewallRule }
if ($Remove) { Write-Host "Rules removed."; exit 0 }

# The venv's python.exe is a launcher; the real interpreter is its base Python.
$venvCfg = Join-Path $PSScriptRoot ".venv\pyvenv.cfg"
$homeLine = (Get-Content $venvCfg | Where-Object { $_ -match "^home\s*=" }) -replace "^home\s*=\s*", ""
$python = Join-Path $homeLine.Trim() "python.exe"
if (-not (Test-Path $python)) { Write-Host "Python not found at $python" -ForegroundColor Red; exit 1 }

$remote = "LocalSubnet", "100.64.0.0/10"
New-NetFirewallRule -DisplayName $names[0] -Direction Inbound -Action Allow -Program $python `
    -Protocol TCP -LocalPort 8000 -RemoteAddress $remote -Profile Private, Public | Out-Null
New-NetFirewallRule -DisplayName $names[1] -Direction Inbound -Action Allow -Program $python `
    -Protocol UDP -RemoteAddress $remote -Profile Private, Public | Out-Null
if ($Metro) {
    New-NetFirewallRule -DisplayName $names[2] -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort 8081 -RemoteAddress $remote -Profile Private, Public | Out-Null
}
Write-Host "Firewall rules created for $python (local subnet and Tailscale only)." -ForegroundColor Green
Get-NetFirewallRule -DisplayName "World Voice AI*" | Format-Table DisplayName, Enabled, Profile
