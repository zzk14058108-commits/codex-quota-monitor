param([ValidateSet('Status','Start','Stop','Disable','Enable')][string]$Action='Status',[string]$InstallDir='')
. (Join-Path $PSScriptRoot 'Common.ps1')
if (-not $InstallDir) {$InstallDir=if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'install.json')) {$PSScriptRoot} else {Join-Path $env:LOCALAPPDATA 'CodexQuotaMonitor'}}
$receipt=Read-MonitorReceipt $InstallDir
$task=Get-ScheduledTask -TaskName $receipt.task_name
Assert-OwnedTask $task $receipt
switch ($Action) {
  'Status' {if (Test-Path -LiteralPath (Join-Path $InstallDir 'status.json')) {Get-Content -LiteralPath (Join-Path $InstallDir 'status.json') -Raw -Encoding utf8};$task | Select-Object TaskName,State}
  'Stop' {Wait-MonitorStopped $InstallDir $receipt.task_name;Write-Output 'Monitor stopped. Codex APP unchanged. Logon start remains enabled.'}
  'Disable' {Disable-ScheduledTask -InputObject $task | Out-Null;Wait-MonitorStopped $InstallDir $receipt.task_name;Write-Output 'Monitor and logon start disabled. Codex APP unchanged.'}
  {$_ -in 'Start','Enable'} {
    $other=Get-ScheduledTask -TaskName 'CodexQuotaMonitor' -ErrorAction SilentlyContinue
    if ($other -and $other.State -eq 'Running') {throw 'Another quota monitor is running; duplicate start refused'}
    $marker=Join-Path $InstallDir 'STOP_MONITOR.request'
    if (Test-Path -LiteralPath $marker) {Remove-Item -LiteralPath $marker}
    if ($Action -eq 'Enable') {Enable-ScheduledTask -InputObject $task | Out-Null}
    Start-ScheduledTask -TaskName $receipt.task_name;Write-Output 'Monitor start requested; no Codex APP was opened.'
  }
}
