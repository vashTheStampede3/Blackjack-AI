[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
$pythonPath = Join-Path $projectRoot '.venv312\Scripts\python.exe'
$pythonwPath = Join-Path $projectRoot '.venv312\Scripts\pythonw.exe'
$launcherPath = Join-Path $projectRoot 'scripts\launch_blackjack.py'
$entryPath = Join-Path $projectRoot 'main.py'

foreach ($requiredPath in @($pythonPath, $pythonwPath, $launcherPath, $entryPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required startup file is missing: $requiredPath"
    }
}

# This Windows known folder follows OneDrive and other desktop redirections.
$desktopPath = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if ([string]::IsNullOrWhiteSpace($desktopPath) -or
    -not (Test-Path -LiteralPath $desktopPath -PathType Container)) {
    throw 'The effective Windows desktop directory is not accessible.'
}

$shortcutPath = Join-Path $desktopPath 'Blackjack AI.lnk'
$launcherArguments = '-B "' + $launcherPath + '"'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonwPath
$shortcut.Arguments = $launcherArguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Blackjack AI - project virtual environment'
$shortcut.IconLocation = $pythonwPath + ',0'
$shortcut.WindowStyle = 1
$shortcut.Save()

# Read back the persisted link instead of assuming Save() succeeded.
if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) {
    throw "The desktop shortcut was not created: $shortcutPath"
}
$installed = $shell.CreateShortcut($shortcutPath)
if ($installed.TargetPath -ne $pythonwPath -or
    $installed.Arguments -ne $launcherArguments -or
    $installed.WorkingDirectory -ne $projectRoot) {
    throw 'The saved desktop shortcut does not match the project startup paths.'
}
if (-not (Test-Path -LiteralPath $installed.TargetPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installed.WorkingDirectory -PathType Container)) {
    throw 'The saved shortcut points to an unavailable Python or working directory.'
}

[pscustomobject]@{
    ShortcutPath = $shortcutPath
    TargetPath = $installed.TargetPath
    Arguments = $installed.Arguments
    WorkingDirectory = $installed.WorkingDirectory
    EntryPoint = $entryPath
}
