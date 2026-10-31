# Start the Party Pack if BattleChess is not already up, then open the board.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Url = "http://127.0.0.1:5000/battlechess/"

function Get-BattleChessStatus {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return [int]$resp.StatusCode
    } catch {
        $errResp = $_.Exception.Response
        if ($errResp) {
            return [int]$errResp.StatusCode
        }
        return 0
    }
}

$code = Get-BattleChessStatus
if ($code -eq 200) {
    Write-Host "BattleChess is already running."
    Start-Process $Url
    exit 0
}
if ($code -ne 0) {
    Write-Host "Port 5000 answered /battlechess/ with HTTP $code."
    Write-Host "Something else is using that port. Stop it (often B:\Grok\forge) and run this again."
    exit 1
}

$py = $null
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) { $py = $cmd.Source }
if (-not $py) {
    $fallback = Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe"
    if (Test-Path $fallback) { $py = $fallback }
}
if (-not $py) {
    Write-Host "python was not found on PATH."
    exit 1
}

Write-Host "Starting Party Pack from $Root"
Start-Process -FilePath $py -ArgumentList "forge/app.py" -WorkingDirectory $Root
Write-Host "Waiting for $Url"

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    $code = Get-BattleChessStatus
    if ($code -eq 200) {
        $ready = $true
        break
    }
    if ($code -ne 0 -and $i -gt 4) {
        Write-Host "Port 5000 answered /battlechess/ with HTTP $code."
        Write-Host "Something else is using that port. Stop it (often B:\Grok\forge) and run this again."
        exit 1
    }
}

if (-not $ready) {
    Write-Host "Party Pack did not serve BattleChess. Look at the server window."
    exit 1
}

Start-Process $Url
exit 0
