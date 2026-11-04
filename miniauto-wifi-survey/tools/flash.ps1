#Requires -Version 5.1
<#
.SYNOPSIS
  Flash the MiniPro AutoSurvey firmware to the miniAuto car.

.DESCRIPTION
  Writes bootloader / partition table / boot_app0 / application to an ESP32.
  Optionally backs up the current 4 MB flash first (recommended).

  If -Port is omitted the script scans for serial ports and picks the one
  that looks like a USB-UART bridge (CH340 / CH341 / CP210x / FTDI).
  Before writing anything it reads the real chip type from the board and
  refuses to continue when it does not match -Chip.

.PARAMETER Identify
  Only read chip type, flash size and MAC address. Writes nothing.
  Use this first to confirm the board type without touching the firmware.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\flash.ps1 -Identify

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\flash.ps1 -Backup -Erase

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\flash.ps1 -Port COM8 -Backup
#>
param(
    [string]$Port,

    [ValidateSet('esp32', 'esp32s3', 'esp32c3')]
    [string]$Chip = 'esp32',

    [int]$Baud = 460800,

    [string]$Python = '',

    [switch]$Identify,

    [switch]$Erase,

    [switch]$Backup,

    [string]$BackupPath = '',

    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $root 'firmware\bin'

$files = @(
    @{ Offset = '0x1000';  Name = 'MiniPro_AutoSurvey.ino.bootloader.bin' },
    @{ Offset = '0x8000';  Name = 'MiniPro_AutoSurvey.ino.partitions.bin' },
    @{ Offset = '0xe000';  Name = 'boot_app0.bin' },
    @{ Offset = '0x10000'; Name = 'MiniPro_AutoSurvey.ino.bin' }
)

Write-Host '=== MiniPro AutoSurvey firmware flasher ===' -ForegroundColor Cyan

if (-not $Identify) {
    foreach ($f in $files) {
        $path = Join-Path $binDir $f.Name
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Missing firmware file: $path"
        }
    }
}

# ---------------------------------------------------------------- Python
$candidates = @()
if ($Python) { $candidates += $Python }
$candidates += (Join-Path $root '..\wifi-rss-toolkit\.venv\Scripts\python.exe')
$candidates += (Join-Path $root '.venv\Scripts\python.exe')
$candidates += 'python'

$py = $null
foreach ($c in $candidates) {
    if (-not $c) { continue }
    try {
        & $c -c "import esptool" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
    } catch { }
}
if (-not $py) {
    throw 'No Python with esptool found. Install it with: pip install esptool'
}
Write-Host "Python : $py"

# ---------------------------------------------------------------- Port
$portList = @()
try {
    $probe = @'
import serial.tools.list_ports as p
for x in p.comports():
    print(x.device + "|" + (x.description or "") + "|" + str(x.vid) + ":" + str(x.pid))
'@
    $raw = & $py -c $probe 2>$null
    foreach ($line in @($raw)) {
        if ($line -and $line -match '^(COM\d+)\|([^|]*)\|(.*)$') {
            $portList += [pscustomobject]@{ Port = $Matches[1]; Desc = $Matches[2]; Id = $Matches[3] }
        }
    }
} catch { }

if (-not $Port) {
    if ($portList.Count -eq 0) {
        Write-Host ''
        throw 'No serial port found. Plug the car into USB and check that the driver is installed (CH340 or CP210x).'
    }
    $bridges = @($portList | Where-Object { $_.Desc -match 'CH34|CP21|FTDI|USB-SERIAL|Silicon|UART' })
    if ($bridges.Count -eq 1) {
        $Port = $bridges[0].Port
        Write-Host "Auto-detected port: $Port ($($bridges[0].Desc))"
    }
    elseif ($portList.Count -eq 1) {
        $Port = $portList[0].Port
        Write-Host "Auto-detected port: $Port ($($portList[0].Desc))"
    }
    else {
        Write-Host ''
        Write-Host 'Several serial ports found:' -ForegroundColor Yellow
        for ($i = 0; $i -lt $portList.Count; $i++) {
            Write-Host ("  [{0}] {1}  {2}" -f ($i + 1), $portList[$i].Port, $portList[$i].Desc)
        }
        $sel = Read-Host "Select port number (1-$($portList.Count))"
        $idx = 0
        if (-not [int]::TryParse($sel, [ref]$idx) -or $idx -lt 1 -or $idx -gt $portList.Count) {
            throw 'Invalid selection.'
        }
        $Port = $portList[$idx - 1].Port
    }
}

if ($Port -notmatch '^COM\d+$') {
    throw "Invalid port name: $Port (expected COM3, COM8, ...)"
}

# ---------------------------------------------------------------- Identify
Write-Host ''
Write-Host "Reading chip information on $Port ..." -ForegroundColor Cyan
$idText = (& $py -m esptool --port $Port flash-id 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    Write-Host $idText
    throw @'
Could not talk to the board.
  - Is the car powered on?
  - Is the USB cable a data cable (not charge-only)?
  - Close any serial monitor that is holding the port.
'@
}

$realChip = ''
if ($idText -match 'Chip type:\s*([^\r\n]+)') { $realChip = $Matches[1].Trim() }
$realFlash = ''
if ($idText -match 'Detected flash size:\s*([^\r\n]+)') { $realFlash = $Matches[1].Trim() }
$realMac = ''
if ($idText -match 'MAC:\s*([0-9a-fA-F:]{17})') { $realMac = $Matches[1] }

Write-Host ''
Write-Host ("Chip  : {0}" -f $(if ($realChip) { $realChip } else { 'unknown' }))
Write-Host ("Flash : {0}" -f $(if ($realFlash) { $realFlash } else { 'unknown' }))
if ($realMac) { Write-Host ("MAC   : {0}" -f $realMac) }

function Test-ChipMatch {
    param([string]$Requested, [string]$Actual)
    if (-not $Actual) { return $true }   # cannot tell, let esptool decide
    $a = $Actual.ToUpperInvariant()
    switch ($Requested) {
        'esp32' {
            if ($a -match 'ESP32-S2|ESP32-S3|ESP32-C2|ESP32-C3|ESP32-C5|ESP32-C6|ESP32-H2|ESP32-P4') { return $false }
            return ($a -match '^ESP32')
        }
        'esp32s3' { return ($a -match '^ESP32-S3') }
        'esp32c3' { return ($a -match '^ESP32-C3') }
        default   { return $true }
    }
}

if ($Identify) {
    Write-Host ''
    if (Test-ChipMatch -Requested $Chip -Actual $realChip) {
        Write-Host "Board matches the prebuilt '-Chip $Chip' firmware." -ForegroundColor Green
    }
    else {
        Write-Host "Mismatch: firmware was built for '$Chip', board is '$realChip'." -ForegroundColor Red
        Write-Host 'Recompile with tools\build.ps1 using the matching board before flashing.' -ForegroundColor Yellow
    }
    Write-Host 'Nothing was written to the board.' -ForegroundColor Green
    exit 0
}

if (-not (Test-ChipMatch -Requested $Chip -Actual $realChip)) {
    Write-Host ''
    Write-Host "The board reports '$realChip' but the bundled firmware was built for '$Chip'." -ForegroundColor Red
    Write-Host 'Refusing to flash a mismatched binary.' -ForegroundColor Red
    Write-Host 'Run tools\build.ps1 with the correct board, or ask for a rebuild.' -ForegroundColor Yellow
    exit 2
}

# ---------------------------------------------------------------- Confirm
if (-not $Yes) {
    Write-Host ''
    Write-Host "About to flash chip '$Chip' on $Port." -ForegroundColor Yellow
    Write-Host 'This OVERWRITES the firmware currently on the car.'
    if (-not $Backup) { Write-Host 'Tip: add -Backup to save the current firmware first.' -ForegroundColor DarkGray }
    $answer = Read-Host 'Type FLASH to continue'
    if ($answer -ne 'FLASH') {
        Write-Host 'Cancelled.'
        exit 1
    }
}

# ---------------------------------------------------------------- Backup
if ($Backup) {
    if (-not $BackupPath) {
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $BackupPath = Join-Path $root "backup\flash_${Chip}_$stamp.bin"
    }
    $backupDir = Split-Path -Parent $BackupPath
    if ($backupDir -and -not (Test-Path -LiteralPath $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    Write-Host "Backing up current flash to $BackupPath ..." -ForegroundColor Cyan
    & $py -m esptool --chip $Chip --port $Port --baud $Baud read-flash 0 0x400000 $BackupPath
    if ($LASTEXITCODE -ne 0) { throw 'Backup failed. Aborting before any write.' }
    Write-Host 'Backup done.' -ForegroundColor Green
}

if ($Erase) {
    Write-Host 'Erasing flash ...' -ForegroundColor Cyan
    & $py -m esptool --chip $Chip --port $Port --baud $Baud erase-flash
    if ($LASTEXITCODE -ne 0) { throw 'Erase failed.' }
}

foreach ($f in $files) {
    Write-Host ("Writing {0} -> {1}" -f $f.Name, $f.Offset) -ForegroundColor Cyan
    & $py -m esptool --chip $Chip --port $Port --baud $Baud write-flash $f.Offset (Join-Path $binDir $f.Name)
    if ($LASTEXITCODE -ne 0) { throw "Flashing $($f.Name) failed." }
}

Write-Host ''
Write-Host 'Flash complete.' -ForegroundColor Green
Write-Host 'Next steps:'
Write-Host '  1. Open the serial monitor at 115200 baud.'
Write-Host '  2. Send: {"cmd":"wifi","ssid":"YOUR_SSID","password":"YOUR_PASSWORD"}'
Write-Host '  3. Wait for association, note the printed IP address.'
Write-Host '  4. Put the car on P11 facing the next column, then run the survey app.'
Write-Host ''
Write-Host 'Or run: python host\provision_wifi.py --port ' + $Port
