#Requires -Version 5.1
<#
.SYNOPSIS
  Create a local .venv and install the host-side dependencies.

.DESCRIPTION
  If this package ships a bundled interpreter under runtime\python, nothing is
  installed and the script just verifies the dependencies.
#>
param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$bundled = Join-Path $root 'runtime\python\python.exe'
if (Test-Path -LiteralPath $bundled) {
    Write-Host 'Bundled Python found, nothing to install.' -ForegroundColor Green
    & $bundled -c "import sys, serial, matplotlib; print('python', sys.version.split()[0]); print('pyserial', serial.__version__); print('matplotlib', matplotlib.__version__)"
    Write-Host ''
    Write-Host 'Ready. Double-click 启动控制台.bat' -ForegroundColor Green
    exit 0
}

$venv = Join-Path $root '.venv'

Write-Host "Using Python: $Python" -ForegroundColor Cyan
& $Python --version
if ($LASTEXITCODE -ne 0) { throw 'Python not found. Install Python 3.10+ first.' }

if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    Write-Host 'Creating .venv ...'
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment.' }
}

$venvPy = Join-Path $venv 'Scripts\python.exe'
Write-Host 'Installing dependencies ...'
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r (Join-Path $root 'requirements.txt')

Write-Host ''
Write-Host 'Environment ready.' -ForegroundColor Green
Write-Host "Run: $venvPy host\survey_gui.py"
