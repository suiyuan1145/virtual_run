$ErrorActionPreference = 'Stop'
$taskName = 'VirtualRun-Morning-0700'
$launcherPath = Join-Path $PSScriptRoot '启动晨跑.ps1'
$runnerPath = Join-Path $PSScriptRoot 'morning_run.py'
if (!(Test-Path -LiteralPath $runnerPath)) { throw 'Missing morning runner script.' }
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot '晨跑配置.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if (!(Test-Path -LiteralPath $config.python)) { throw 'Configured Python interpreter was not found.' }
if ((Get-TimeZone).Id -ne 'China Standard Time') { throw 'Set Windows time zone to China Standard Time first.' }
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    if ($existingTask.Description -notlike 'Prepare emulator at 06:55;*') {
        throw 'A task with this name already exists and is not owned by this application.'
    }
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcherPath + '"') -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '06:55'
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Prepare emulator at 06:55; run Anxiaoxin from 07:00 to 08:00 Beijing time. No late catch-up.'
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object TaskName,NextRunTime,LastTaskResult
