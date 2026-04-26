[CmdletBinding()]
param(
    [switch]$RecreateVenv = $true,
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$venvDir = Join-Path $backendDir ".venv"

if (-not (Test-Path $backendDir)) {
    throw "Backend-Verzeichnis nicht gefunden: $backendDir"
}

function Resolve-PythonCommand {
    param([string]$PreferredVersion)

    $candidates = @(
        @("py", "-$PreferredVersion"),
        @("py", "-3.11"),
        @("python", "")
    )

    foreach ($candidate in $candidates) {
        $cmd = $candidate[0]
        $arg = $candidate[1]
        try {
            if ($arg) {
                & $cmd $arg --version *> $null
                if ($LASTEXITCODE -eq 0) { return @($cmd, $arg) }
            }
            else {
                & $cmd --version *> $null
                if ($LASTEXITCODE -eq 0) { return @($cmd) }
            }
        }
        catch {
            continue
        }
    }

    throw "Keine Python-Installation gefunden. Installiere bitte Python 3.11 oder 3.12."
}

if ($RecreateVenv -and (Test-Path $venvDir)) {
    Write-Host "[1/7] Entferne bestehende venv: $venvDir"
    Remove-Item -Recurse -Force $venvDir
}

$pythonCmd = Resolve-PythonCommand -PreferredVersion $PythonVersion
Write-Host "[2/7] Verwende Python-Kommando: $($pythonCmd -join ' ')"

Push-Location $backendDir
try {
    Write-Host "[3/7] Erstelle virtuelle Umgebung"
    if ($pythonCmd.Count -eq 2) {
        & $pythonCmd[0] $pythonCmd[1] -m venv .venv
    }
    else {
        & $pythonCmd[0] -m venv .venv
    }

    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "venv-Python nicht gefunden: $venvPython"
    }

    Write-Host "[4/7] Prüfe Python-Version in der venv"
    & $venvPython --version

    Write-Host "[5/7] Upgrade von pip/setuptools/wheel"
    & $venvPython -m pip install --upgrade pip setuptools wheel

    Write-Host "[6/7] Installiere Abhängigkeiten aus requirements.txt"
    & $venvPython -m pip install -r requirements.txt

    Write-Host "[7/7] Smoke-Test wichtiger Imports"
    & $venvPython -c "import fastapi, pydantic, pydantic_core, uvicorn; print('Setup OK')"

    Write-Host "\nFertig. Starte als Nächstes das Dashboard mit:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\\scripts\\start_dashboard_windows.ps1"
}
finally {
    Pop-Location
}
