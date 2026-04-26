[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 8080,
    [string]$FrontendEntry = "index.html"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Keine virtuelle Umgebung gefunden ($venvPython). Bitte zuerst setup_windows.ps1 ausführen."
}
if (-not (Test-Path $frontendDir)) {
    throw "Frontend-Verzeichnis nicht gefunden: $frontendDir"
}

$backendArgs = @("-m", "uvicorn", "app.main:app", "--reload", "--port", "$BackendPort")
$frontendArgs = @("-m", "http.server", "$FrontendPort")

Write-Host "Starte Backend auf Port $BackendPort ..."
$backendProc = Start-Process -FilePath $venvPython -ArgumentList $backendArgs -WorkingDirectory $backendDir -PassThru

Write-Host "Starte Frontend auf Port $FrontendPort ..."
$frontendProc = Start-Process -FilePath $venvPython -ArgumentList $frontendArgs -WorkingDirectory $frontendDir -PassThru

$healthUrl = "http://127.0.0.1:$BackendPort/health"
$dashboardUrl = "http://127.0.0.1:$FrontendPort/$FrontendEntry"

$maxAttempts = 40
$ready = $false
for ($i = 1; $i -le $maxAttempts; $i++) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $ready) {
    Write-Warning "Backend ist noch nicht gesund erreichbar unter $healthUrl. Dashboard wird trotzdem geöffnet."
}

Start-Process $dashboardUrl

Write-Host "\nDashboard geöffnet: $dashboardUrl"
Write-Host "Backend PID : $($backendProc.Id)"
Write-Host "Frontend PID: $($frontendProc.Id)"
Write-Host "Zum Stoppen in PowerShell:"
Write-Host "  Stop-Process -Id $($backendProc.Id),$($frontendProc.Id)"
