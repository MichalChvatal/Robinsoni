param(
    [Parameter(Mandatory)]
    [ValidateSet('start', 'send', 'stop')]
    [string]$Action,
    [string]$Config = 'config/app_config.yaml',
    [switch]$NoWindow
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$project = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $project
$appPython = Join-Path $project '.venv/Scripts/python.exe'
$env:PYTHONPATH = Join-Path $project 'backend'
$setupLock = $null

try {
    $setupDir = Join-Path $project '.tmp'
    [void][System.IO.Directory]::CreateDirectory($setupDir)
    $setupLockPath = Join-Path $setupDir 'windows-launcher.lock'
    $until = [DateTime]::UtcNow.AddMinutes(10)
    while ($null -eq $setupLock) {
        try {
            $setupLock = [System.IO.File]::Open($setupLockPath, 'OpenOrCreate', 'ReadWrite', 'None')
        } catch [System.IO.IOException] {
            if ([DateTime]::UtcNow -ge $until) { throw 'Jine spusteni stale pripravuje aplikaci.' }
            Start-Sleep -Milliseconds 500
        }
    }
    if ($Action -eq 'stop' -and -not (Test-Path -LiteralPath $appPython)) {
        Write-Host 'Aplikace zatim nebyla pripravena.'
        exit 0
    }
    if (-not (Test-Path -LiteralPath $appPython)) {
        Write-Host 'Pripravuji Python prostredi...'
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.12 -m venv .venv
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            & python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'
            if ($LASTEXITCODE -ne 0) { throw 'Je potreba Python 3.12.' }
            & python -m venv .venv
        } else {
            throw 'Je potreba nainstalovat Python 3.12 z python.org.'
        }
        if ($LASTEXITCODE -ne 0) { throw 'Nelze vytvorit prostredi. Overte instalaci Pythonu 3.12.' }
    }
    if ($Action -ne 'stop') {
        $probe = @'
import sys
try:
    import alembic, pydantic, pydicom, pynetdicom, sqlalchemy, yaml
    from pydantic import field_validator
    from PySide6 import QtWidgets
except (ImportError, OSError):
    sys.exit(1)
'@
        & $appPython -c $probe
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'Instaluji zavislosti. Prvni spusteni muze trvat nekolik minut...'
            $installArgs = @('-m', 'pip', 'install', '-r', 'requirements-stage6.lock.txt')
            if (Test-Path -LiteralPath 'wheels' -PathType Container) {
                $installArgs += @('--no-index', '--find-links', 'wheels')
            }
            & $appPython @installArgs
            if ($LASTEXITCODE -ne 0) { throw 'Instalace zavislosti se nezdarila.' }
        }
    }
    $setupLock.Dispose()
    $setupLock = $null
    $launchArgs = @('-X', 'utf8', '-m', 'mr_quality_monitor.launcher', $Action, '--config', $Config, '--protocol', 'config/protocols/prostate_mou.yaml')
    if ($NoWindow) { $launchArgs += '--no-window' }
    if ($Action -ne 'stop') { Write-Host 'Spoustim prijem a analyzu...' }
    & $appPython @launchArgs
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message
    exit 1
} finally {
    if ($null -ne $setupLock) { $setupLock.Dispose() }
}
