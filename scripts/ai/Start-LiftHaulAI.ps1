param(
    [Parameter(Mandatory=$true)][string]$Model,
    [string]$TaskFile,
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $repoRoot
try {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw 'Install Python 3.11 or newer from python.org, then reopen PowerShell.'
    }
    $runnerArgs = @('scripts/ai/ollama_backup.py', '--model', $Model)
    if ($Check) { $runnerArgs += '--check' }
    elseif ($TaskFile) { $runnerArgs += @('--task-file', $TaskFile) }
    else { throw 'Supply -TaskFile or -Check.' }
    & python @runnerArgs
    exit $LASTEXITCODE
}
finally { Pop-Location }
