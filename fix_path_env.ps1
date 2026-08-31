# =====================================================================
# Fix malformed Machine PATH (stray double-quotes around JDK entries)
# - Backs up current Machine/User PATH before any change
# - Removes ONLY double-quote characters; no path entry is deleted
# - Verifies every segment with [IO.Path]::GetPathRoot afterwards
#
# RUN AS ADMINISTRATOR:
#   1) Start menu -> type "PowerShell" -> right-click -> Run as administrator
#   2) powershell -ExecutionPolicy Bypass -File D:\nexus_agent\fix_path_env.ps1
# =====================================================================

$ErrorActionPreference = "Stop"

# --- 0. admin check ---
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ABORT] This script must be run as Administrator." -ForegroundColor Red
    Write-Host "Right-click PowerShell -> 'Run as administrator', then run it again." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# --- 1. backup ---
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$bakDir = "D:\nexus_agent\data"
if (-not (Test-Path $bakDir)) { New-Item -ItemType Directory -Path $bakDir | Out-Null }
$bak = Join-Path $bakDir ("path_backup_{0}.txt" -f $ts)

$machineBefore = [Environment]::GetEnvironmentVariable("Path", "Machine")
$userBefore    = [Environment]::GetEnvironmentVariable("Path", "User")
@(
    "=== MACHINE PATH backup @ $(Get-Date) ===",
    $machineBefore,
    "",
    "=== USER PATH backup @ $(Get-Date) ===",
    $userBefore
) | Out-File -FilePath $bak -Encoding UTF8
Write-Host ("Backup written to: {0}" -f $bak) -ForegroundColor Green

# --- 2. show offending segments ---
$badSegs = ($machineBefore -split ";") | Where-Object { $_ -match '"' }
if (-not $badSegs) {
    Write-Host "No double-quote found in Machine PATH. Nothing to fix. Exiting." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 0
}
Write-Host "`nMalformed segment(s) found:" -ForegroundColor Red
$badSegs | ForEach-Object { Write-Host ("  <{0}>" -f $_) -ForegroundColor Red }

# --- 3. clean: drop quotes, trim segments, drop empties, keep order ---
$clean = (($machineBefore -replace '"', '') -split ';' |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -ne "" }) -join ';'

Write-Host "`n---------- BEFORE ----------" -ForegroundColor Yellow
Write-Host $machineBefore
Write-Host "`n---------- AFTER -----------" -ForegroundColor Yellow
Write-Host $clean
Write-Host "-----------------------------"

$confirm = Read-Host "`nApply this fix to the SYSTEM Path? (y/N)"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "Cancelled by user. No change made. Backup kept at: $bak" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 0
}

# --- 4. write back (also broadcasts WM_SETTINGCHANGE) ---
[Environment]::SetEnvironmentVariable("Path", $clean, "Machine")
Write-Host "System Path updated." -ForegroundColor Green

# --- 5. verify every segment passes GetPathRoot ---
Start-Sleep -Milliseconds 300
$verify = [Environment]::GetEnvironmentVariable("Path", "Machine")
$invalid = [IO.Path]::GetInvalidPathChars()
$fail = 0
foreach ($seg in ($verify -split ';')) {
    if ([string]::IsNullOrWhiteSpace($seg)) { continue }
    try {
        [void][IO.Path]::GetPathRoot($seg)
    } catch {
        $fail++
        Write-Host ("[STILL BAD] <{0}> -> {1}" -f $seg, $_.Exception.Message) -ForegroundColor Red
    }
}
if ($fail -eq 0) {
    Write-Host "[OK] Every Machine PATH segment passes GetPathRoot now." -ForegroundColor Green
} else {
    Write-Host ("[WARN] {0} segment(s) still invalid - send the output to the assistant." -f $fail) -ForegroundColor Yellow
}

Write-Host "`nNEXT STEPS:" -ForegroundColor Cyan
Write-Host "1. Fully QUIT Doubao (tray icon too), then relaunch it."
Write-Host "2. Ask the assistant to run a test command again."
Read-Host "Press Enter to exit"
