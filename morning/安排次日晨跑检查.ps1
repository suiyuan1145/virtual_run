param(
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA 'VirtualRunDesktop\morning\晨跑配置.json'),
    [string]$Date = '',
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
if ((Get-TimeZone).Id -ne 'China Standard Time') { throw 'Expected China Standard Time.' }
if (!$Date) { $Date = (Get-Date).AddDays(1).ToString('yyyy-MM-dd') }
if (!$OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot ('..\analysis\晨跑检查-' + $Date) }
$checkDay = [datetime]::ParseExact($Date, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
if ($checkDay.Date -le (Get-Date).Date) { throw 'Check date must be in the future.' }
$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$scriptPath = Join-Path $PSScriptRoot 'monitor_next_morning.py'
if (!(Test-Path -LiteralPath $config.python) -or !(Test-Path -LiteralPath $scriptPath)) { throw 'Missing check runtime.' }
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$taskName = 'VirtualRun-ReadOnlyCheck-' + $Date
$description = 'VirtualRun read-only next-morning check; no navigation or GPS writes.'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $description) { throw 'Existing task is not owned by this check.' }
$arguments = '"' + $scriptPath + '" --config "' + $ConfigPath + '" --date ' + $Date + ' --output "' + $outputPath + '"'
$action = New-ScheduledTaskAction -Execute $config.python -Argument $arguments -WorkingDirectory $PSScriptRoot
$times = @('06:56', '07:05', '07:30', '08:05')
$triggers = @($times | ForEach-Object {
    $at = [datetime]::ParseExact(($Date + ' ' + $_), 'yyyy-MM-dd HH:mm', [System.Globalization.CultureInfo]::InvariantCulture)
    New-ScheduledTaskTrigger -Once -At $at
})
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$task = New-ScheduledTask -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description $description
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
$registered = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
$result = [pscustomobject]@{task_name=$taskName;state=[string]$registered.State;date=$Date;times=$times;next_run=$info.NextRunTime.ToString('o');report=(Join-Path $outputPath '晨跑检查报告.md');read_only=$true}
$result | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $outputPath 'registration.json') -Encoding UTF8
$result | ConvertTo-Json -Depth 3
