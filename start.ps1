param(
    [ValidateSet('app','record','eval','review','doctor')][string]$Mode = 'app',
    [string]$Config = 'data/table.json',
    [string]$Session = '',
    [ValidateSet('train','eval')][string]$Role = 'train',
    [int]$Seconds = 1800,
    [string]$Manifest = '',
    [string]$Labels = '',
    [string]$Weights = 'models/player_cards.onnx'
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $PSScriptRoot '.venv312'
    # setx changes user environment, not an already-running parent terminal.
    $savedKey = [Environment]::GetEnvironmentVariable('ROBOFLOW_API_KEY','User')
    if ($savedKey) { $env:ROBOFLOW_API_KEY = $savedKey }
    $env:BJ_TABLE_CONFIG = Join-Path $PSScriptRoot $Config
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'Python environment could not be synchronized.' }
    $python = Join-Path $PSScriptRoot '.venv312\Scripts\python.exe'
    switch ($Mode) {
        'app' {
            $settingsFile = Join-Path $PSScriptRoot 'output/settings.json'
            if (Test-Path -LiteralPath $settingsFile) {
                $tableSettings = Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json
                if ($tableSettings.executor.mode -and $tableSettings.executor.mode -ne 'off') {
                    throw 'Read-only brief requires the existing executor setting to be off.'
                }
            }
            & $python main.py
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
