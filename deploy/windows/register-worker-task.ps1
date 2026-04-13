param(
  [string]$PythonExe = "python",
  [string]$ProjectDir = "F:\项目\mailanalysis\mail-analyzer",
  [string]$TaskName = "MailAnalyzerWorker"
)

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m src.services.worker" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Mail Analyzer backend worker"
Write-Host "Scheduled task '$TaskName' registered."
