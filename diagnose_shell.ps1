# =====================================================================
# Nexus shell-wrapper diagnosis (read-only, no changes to your system)
# Usage (run in YOUR OWN PowerShell window, e.g. PyCharm terminal):
#   powershell -ExecutionPolicy Bypass -File D:\nexus_agent\diagnose_shell.ps1
# Then copy ALL output back to the assistant.
# =====================================================================

$ErrorActionPreference = "Continue"

function Test-PathValue($scope, $name, $raw) {
    if ($null -eq $raw) { return }
    $val = [string]$raw
    $invalid = [IO.Path]::GetInvalidPathChars()
    $badCodes = New-Object System.Collections.Generic.List[int]
    foreach ($ch in $val.ToCharArray()) {
        if ($invalid -contains $ch) { $badCodes.Add([int]$ch) }
        if ([int]$ch -lt 32)          { $badCodes.Add([int]$ch) }
    }
    $segments = if ($val.Contains(";")) { $val.Split(";") } else { @($val) }
    $rtErr = ""
    foreach ($seg in $segments) {
        if ([string]::IsNullOrWhiteSpace($seg)) { continue }
        try { [void][IO.Path]::GetPathRoot($seg) }
        catch { $rtErr = $_.Exception.Message; break }
    }
    if ($badCodes.Count -gt 0 -or $rtErr) {
        $hex = ($badCodes | Select-Object -Unique | ForEach-Object { "0x{0:X2}" -f $_ }) -join ","
        Write-Host ("[BAD][{0}] {1} = <{2}>" -f $scope, $name, $val) -ForegroundColor Red
        Write-Host ("        invalid-char-codes: {0} ; GetPathRoot: {1}" -f $hex, $rtErr) -ForegroundColor Red
    }
}

Write-Host "===== 1. Scan ALL env vars in 3 scopes (Process/User/Machine) =====" -ForegroundColor Cyan
$found = $false
foreach ($scope in "Process", "User", "Machine") {
    try { $vars = [Environment]::GetEnvironmentVariables($scope) }
    catch { Write-Host "cannot read $scope scope: $_"; continue }
    foreach ($name in $vars.Keys) {
        $before = ($found -as [int])
        Test-PathValue $scope $name $vars[$name]
    }
}
Write-Host "(if nothing marked [BAD] above, env vars are clean)" -ForegroundColor DarkGray

Write-Host "`n===== 2. Key path variables (Process scope, inherited by Doubao) =====" -ForegroundColor Cyan
foreach ($n in "USERPROFILE","HOMEDRIVE","HOMEPATH","HOME","TEMP","TMP","TMPDIR",
               "APPDATA","LOCALAPPDATA","CD","PWD","PSModulePath","ComSpec","SystemRoot") {
    $v = [Environment]::GetEnvironmentVariable($n, "Process")
    Write-Host ("{0,-14} = <{1}>" -f $n, $v)
}

Write-Host "`n===== 3. Full-width / quote / trailing-space suspects =====" -ForegroundColor Cyan
foreach ($scope in "Process", "User", "Machine") {
    $vars = [Environment]::GetEnvironmentVariables($scope)
    foreach ($name in $vars.Keys) {
        $val = [string]$vars[$name]
        $flags = New-Object System.Collections.Generic.List[string]
        if ($val -match [char]0xFF1A) { $flags.Add("fullwidth-colon") }
        if ($val -match [char]0xFF3C) { $flags.Add("fullwidth-backslash") }
        if ($val -match '"')          { $flags.Add("double-quote") }
        if ($val -match "^\s|\s$")    { $flags.Add("leading/trailing-space") }
        if ($val -match "`n|`r")      { $flags.Add("newline") }
        if ($flags.Count -gt 0) {
            Write-Host ("[SUSPECT][{0}] {1} = <{2}> -> {3}" -f $scope,$name,$val,($flags -join ",")) -ForegroundColor Yellow
        }
    }
}

Write-Host "`n===== 4. Doubao processes: path / command line =====" -ForegroundColor Cyan
try {
    Get-CimInstance Win32_Process | Where-Object { $_.Name -match "oubao|Doubao" } |
        Select-Object ProcessId, Name, ExecutablePath, CommandLine |
        Format-List
} catch { Write-Host "query process failed: $_" }

Write-Host "===== 5. PowerShell / OS / current location =====" -ForegroundColor Cyan
Write-Host ("PSVersion : {0}" -f $PSVersionTable.PSVersion)
Write-Host ("PWD       : <{0}>" -f (Get-Location).Path)
Write-Host ("OS        : {0}" -f [Environment]::OSVersion.VersionString)
Write-Host ("User      : {0}" -f [Environment]::UserName)
Write-Host "`nDone. Copy everything above back to the assistant." -ForegroundColor Green
