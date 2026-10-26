param([string]$InstallDir='')
. (Join-Path $PSScriptRoot 'Common.ps1')
if (-not $InstallDir) {$InstallDir=if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'install.json')) {$PSScriptRoot} else {Join-Path $env:LOCALAPPDATA 'CodexQuotaMonitor'}}
$receipt=Read-MonitorReceipt $InstallDir
$task=Get-ScheduledTask -TaskName $receipt.task_name -ErrorAction SilentlyContinue
if ($task) {
  Assert-OwnedTask $task $receipt
  Disable-ScheduledTask -InputObject $task | Out-Null
  Wait-MonitorStopped $InstallDir $receipt.task_name
  Unregister-ScheduledTask -TaskName $receipt.task_name -Confirm:$false
}
Write-Output 'Monitor uninstalled from Task Scheduler. Codex APP was not closed. Files and private logs are retained in the installation folder for manual removal.'
