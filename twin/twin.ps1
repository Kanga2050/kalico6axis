# Printer twin launcher for Windows (Docker Desktop).
#   .\twin.ps1                      pick a config folder
#   .\twin.ps1 C:\path\to\config    use that folder
#   .\twin.ps1 -Stop                stop the twin
param([string]$Config, [switch]$Stop, [int]$Port = 8080)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$work = Join-Path $env:LOCALAPPDATA 'PrinterTwin'
$name = 'printer-twin'

if ($Stop) { docker rm -f $name | Out-Null; Write-Host 'Twin stopped.'; return }

if (-not $Config) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = 'Choose the printer config folder (the one with printer.cfg)'
    if ($dlg.ShowDialog() -ne 'OK') { return }
    $Config = $dlg.SelectedPath
}
if (-not (Test-Path (Join-Path $Config 'printer.cfg'))) { throw "No printer.cfg in $Config" }

docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running. Start it and try again.' }

# work on a copy so the original config is never changed
$cfgCopy = Join-Path $work 'config'
if (Test-Path $cfgCopy) { Remove-Item -Recurse -Force $cfgCopy }
New-Item -ItemType Directory -Force $cfgCopy | Out-Null
Copy-Item -Recurse -Force (Join-Path $Config '*') $cfgCopy

Write-Host 'Building the twin image (cached after the first time)...'
docker build -f (Join-Path $repo 'twin\Dockerfile') -t $name $repo
if ($LASTEXITCODE -ne 0) { throw 'docker build failed' }

docker rm -f $name *> $null
docker run -d --name $name -p "${Port}:80" -p 7125:7125 `
    -v "${cfgCopy}:/data/printer_data/config" `
    -v printer-twin-gcodes:/data/printer_data/gcodes `
    -v printer-twin-cache:/data/cache `
    $name | Out-Null

Write-Host 'Starting (the first run builds the firmware dictionaries, about a minute per MCU)...'
for ($i = 0; $i -lt 600; $i++) {
    Start-Sleep 2
    try {
        $r = Invoke-RestMethod "http://localhost:$Port/server/info" -TimeoutSec 2
        if ($r.result.klippy_state -eq 'ready') { break }
    } catch { }
}
Start-Process "http://localhost:$Port"
Write-Host "Twin running at http://localhost:$Port  (logs: docker logs -f $name)"
