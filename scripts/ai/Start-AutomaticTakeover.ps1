param(
    [string]$TaskFile,
    [switch]$VerifyTakeover
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $repoRoot
try {
    $runnerArgs = @('scripts/ai/automatic_takeover.py')
    if ($VerifyTakeover) { $runnerArgs += '--verify-takeover' }
    elseif ($TaskFile) { $runnerArgs += @('--task-file', $TaskFile) }
    else { throw 'Supply -TaskFile or -VerifyTakeover.' }
    & python @runnerArgs
    exit $LASTEXITCODE
}
finally { Pop-Location }
