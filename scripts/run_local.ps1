# run_local.ps1 -- start AIPadhaiApp dev server on Windows
# Usage: powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 [port]
param([int]$Port = 8000)

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

$PidFile  = ".padhai_server.pid"
$LogFile  = "padhai_server.log"
# Start-Process on Windows can't redirect stdout + stderr to the same file
# -- they must be different paths. We tail the merged-view via Get-Content.
$ErrLogFile = "padhai_server.err.log"

# Stop any previous instance
if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile
    try {
        Stop-Process -Id $OldPid -Force -ErrorAction SilentlyContinue
        Write-Host "[run_local] stopped previous server (PID $OldPid)"
        Start-Sleep -Seconds 1
    } catch {}
    Remove-Item $PidFile -Force
}

# Free the port if occupied
$existing = netstat -ano | Select-String ":$Port\s" | ForEach-Object {
    ($_ -split '\s+')[-1]
} | Select-Object -Unique
$existing | Where-Object { $_ -match '^\d+$' } | ForEach-Object {
    try { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } catch {}
}

# Load .env if present
if (Test-Path ".env") {
    Get-Content ".env" | Where-Object { $_ -match '^[^#].*=.*' } | ForEach-Object {
        $parts = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
    }
}

if (-not $env:PADHAI_JWT_SECRET) {
    Write-Warning "PADHAI_JWT_SECRET not set -- auth will fail. Add it to .env"
}

$gitSha = (git rev-parse --short HEAD 2>$null) -join ''
Write-Host "[run_local] starting on port $Port | git SHA: $gitSha"

$proc = Start-Process -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "padhai.web:app", "--host", "0.0.0.0", "--port", $Port, "--log-level", "info" `
    -RedirectStandardOutput $LogFile -RedirectStandardError $ErrLogFile `
    -NoNewWindow -PassThru
$proc.Id | Set-Content $PidFile

# Wait for server to bind
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$Port/healthz" -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            Write-Host "[run_local] server is up (PID $($proc.Id))"
            $resp.Content | python -m json.tool
            exit 0
        }
    } catch {}
}

Write-Error "[run_local] server did not start -- check $LogFile / $ErrLogFile"
Write-Host "--- stdout ($LogFile) ---"
if (Test-Path $LogFile) { Get-Content $LogFile -Tail 20 }
Write-Host "--- stderr ($ErrLogFile) ---"
if (Test-Path $ErrLogFile) { Get-Content $ErrLogFile -Tail 20 }
exit 1
