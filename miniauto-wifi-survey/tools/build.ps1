#Requires -Version 5.1
<#
.SYNOPSIS
  Compile the MiniPro_AutoSurvey firmware with arduino-cli.

.DESCRIPTION
  Board settings: ESP32 Dev Module, Huge APP, DIO, 80 MHz, 4 MB flash.
  Looks for arduino-cli in this order:
    1. <project>\tools\arduino-cli\arduino-cli.exe
    2. <workspace>\tools\arduino-cli\arduino-cli.exe   (installed by this project's setup)
    3. arduino-cli from PATH

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\build.ps1
#>
param(
    [string]$Sketch = '',
    [string]$BuildPath = '',
    [string]$Fqbn = 'esp32:esp32:esp32:PartitionScheme=huge_app'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Sketch) { $Sketch = Join-Path $root 'firmware\MiniPro_AutoSurvey' }
if (-not $BuildPath) { $BuildPath = Join-Path $root 'firmware\build' }

$cliCandidates = @(
    (Join-Path $root 'tools\arduino-cli\arduino-cli.exe'),
    (Join-Path $root '..\tools\arduino-cli\arduino-cli.exe')
)
$cli = $null
foreach ($c in $cliCandidates) {
    if (Test-Path -LiteralPath $c) { $cli = $c; break }
}
if (-not $cli) {
    $found = Get-Command arduino-cli -ErrorAction SilentlyContinue
    if ($found) { $cli = $found.Source }
}
if (-not $cli) {
    Write-Host 'arduino-cli not found.' -ForegroundColor Yellow
    Write-Host 'Expected at: .\tools\arduino-cli\arduino-cli.exe'
    Write-Host 'Install:    winget install ArduinoSA.CLI'
    Write-Host 'Then:       arduino-cli core install esp32:esp32@2.0.17'
    exit 1
}

$cfgCandidates = @(
    (Join-Path $root 'tools\arduino-cli.yaml'),
    (Join-Path $root '..\tools\arduino-cli.yaml')
)
$cfg = $null
foreach ($c in $cfgCandidates) {
    if (Test-Path -LiteralPath $c) { $cfg = $c; break }
}

Write-Host "arduino-cli : $cli" -ForegroundColor Gray
if ($cfg) { Write-Host "config      : $cfg" -ForegroundColor Gray }
Write-Host "sketch      : $Sketch" -ForegroundColor Cyan
Write-Host "FQBN        : $Fqbn" -ForegroundColor Cyan

if (Test-Path -LiteralPath $BuildPath) {
    Remove-Item -LiteralPath $BuildPath -Recurse -Force
}

$cliArgs = @('compile', '--fqbn', $Fqbn, '--output-dir', $BuildPath)
if ($cfg) { $cliArgs += @('--config-file', $cfg) }
$cliArgs += $Sketch

& $cli @cliArgs
if ($LASTEXITCODE -ne 0) { throw 'Compile failed.' }

Write-Host ''
Write-Host 'Compile OK.' -ForegroundColor Green
Get-ChildItem -LiteralPath $BuildPath -Filter '*.bin' | Select-Object Name, Length | Format-Table -AutoSize

$binSrc = Join-Path $BuildPath 'MiniPro_AutoSurvey.ino.bin'
$binDst = Join-Path $root 'firmware\bin'
if ((Test-Path -LiteralPath $binSrc) -and (Test-Path -LiteralPath $binDst)) {
    Copy-Item -LiteralPath (Join-Path $BuildPath 'MiniPro_AutoSurvey.ino.bin') -Destination $binDst -Force
    Copy-Item -LiteralPath (Join-Path $BuildPath 'MiniPro_AutoSurvey.ino.bootloader.bin') -Destination $binDst -Force
    Copy-Item -LiteralPath (Join-Path $BuildPath 'MiniPro_AutoSurvey.ino.partitions.bin') -Destination $binDst -Force
    Write-Host 'Copied the three images into firmware\bin\.' -ForegroundColor Green
}
