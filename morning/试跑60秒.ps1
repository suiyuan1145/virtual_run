param([ValidateRange(1,300)][int]$Seconds = 60)
$ErrorActionPreference = 'Stop'
$configPath = Join-Path $PSScriptRoot '晨跑配置.json'
$runnerPath = Join-Path $PSScriptRoot 'morning_run.py'
$config = Get-Content -LiteralPath $configPath -Encoding UTF8 -Raw | ConvertFrom-Json
if (!(Test-Path -LiteralPath $config.python)) { throw 'Configured Python interpreter was not found.' }
& $config.python '-X' 'utf8' $runnerPath '--config' $configPath '--trial-seconds' $Seconds
exit $LASTEXITCODE
