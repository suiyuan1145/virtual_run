param([switch]$Check)
$ErrorActionPreference = 'Stop'
$runnerPath = Join-Path $PSScriptRoot 'morning_run.py'
$configPath = Join-Path $PSScriptRoot '晨跑配置.json'
$config = Get-Content -LiteralPath $configPath -Encoding UTF8 -Raw | ConvertFrom-Json
$pythonPath = $config.python
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Configured Python interpreter was not found.' }
$runnerArgs = @('-X', 'utf8', $runnerPath, '--config', $configPath)
if ($Check) { $runnerArgs += '--check' } else { $runnerArgs += '--scheduled' }
& $pythonPath @runnerArgs
exit $LASTEXITCODE
