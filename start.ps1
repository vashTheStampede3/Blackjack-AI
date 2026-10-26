param(
    [ValidateSet('app','record','eval','review','doctor')][string]$Mode = 'app',
    [string]$Config = 'data/table.json',
    [string]$Session = '',
    [ValidateSet('train','eval')][string]$Role = 'train',
    [int]$Seconds = 1800,
    [string]$Manifest = '',
    [string]$Labels = '',
    [string]$Weights = 'models/player_cards.onnx',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $PSScriptRoot '.venv312'
    # setx changes user environment, not an already-running parent terminal.
    $savedKey = [Environment]::GetEnvironmentVariable('ROBOFLOW_API_KEY','User')
    if ($savedKey) { $env:ROBOFLOW_API_KEY = $savedKey }
    $env:BJ_TABLE_CONFIG = Join-Path $PSScriptRoot $Config
    $python = Join-Path $PSScriptRoot '.venv312\Scripts\python.exe'
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        if (-not $uv) { throw 'Python environment is missing and uv was not found. Install uv, then run start.ps1 again.' }
        & $uv.Source sync --frozen
        if ($LASTEXITCODE -ne 0) { throw 'Python environment could not be created from uv.lock.' }
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'uv sync finished but .venv312 Python is still missing.' }
    $version = & $python -c 'import sys; print(sys.version_info[0], sys.version_info[1])'
    $versionText = if ($null -eq $version) { 'unavailable' } else { $version.Trim() }
    if ($LASTEXITCODE -ne 0 -or $versionText -ne '3 12') {
        throw "The project environment must use Python 3.12; found '$versionText'. Install Python 3.12 and run uv sync --frozen."
    }
    # Only sync when the pinned runtime imports are incomplete. Normal launches
    # avoid a dependency resolution/sync pass; uv.lock remains authoritative.
    & $python -c 'import importlib.util,sys; sys.exit(any(importlib.util.find_spec(n) is None for n in sys.argv[1:]))' cv2 mss numpy onnxruntime PIL psutil requests screeninfo yaml winocr 2>$null
    if ($LASTEXITCODE -ne 0) {
        if (-not $uv) { throw 'One or more locked runtime packages are missing and uv was not found. Install uv, then run start.ps1 again.' }
        & $uv.Source sync --frozen
        if ($LASTEXITCODE -ne 0) { throw 'Locked runtime packages could not be synchronized.' }
    }
    if ($CheckOnly) {
        Write-Output "Environment ready: Python 3.12 at $python"
        return
    }
    switch ($Mode) {
        'app' {
            $settingsFile = Join-Path $PSScriptRoot 'output/settings.json'
            if (Test-Path -LiteralPath $settingsFile) {
                $tableSettings = Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json
                if ($tableSettings.executor.mode -and $tableSettings.executor.mode -ne 'off') {
                    throw 'Read-only brief requires the existing executor setting to be off.'
                }
            }
            & $python -B scripts/launch_blackjack.py
        }
        'record' {
            if (-not $Session) { throw 'Provide -Session with a new session name.' }
            & $python -m tools.record_table --config $Config --session $Session --role $Role --seconds $Seconds
        }
        'eval' {
            if (-not $Manifest) { throw 'Provide -Manifest data/session/frames.jsonl.' }
            $evalArgs = @('-m','tools.eval','--manifest',$Manifest,'--weights',$Weights)
            if ($Labels) { $evalArgs += @('--labels',$Labels) }
            & $python @evalArgs
        }
        'review' {
            if (-not $Manifest) { throw 'Provide -Manifest data/session/frames.jsonl.' }
            & $python -m tools.annotate --manifest $Manifest
        }
        'doctor' { & $python -m tools.phase0 --config $Config; & $python -m tools.check_access }
    }
    if ($LASTEXITCODE -ne 0) { throw 'The requested operation failed; inspect the message above.' }
} finally { Pop-Location }
