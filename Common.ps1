$ErrorActionPreference='Stop'
function Resolve-MonitorPython([string]$Explicit='') {
  $candidates=[Collections.Generic.List[string]]::new()
  if ($Explicit) {$candidates.Add([IO.Path]::GetFullPath($Explicit))}
  else {
    $py=Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
      $found=& $py.Source -3 -c 'import sys;print(sys.executable)' 2>$null
      if ($LASTEXITCODE -eq 0 -and $found) {$candidates.Add([string]$found)}
    }
    $python=Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike '*\Microsoft\WindowsApps\*') {$candidates.Add($python.Source)}
    $root=Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path -LiteralPath $root) {foreach ($dir in Get-ChildItem -LiteralPath $root -Directory | Sort-Object Name -Descending) {$candidates.Add((Join-Path $dir.FullName 'python.exe'))}}
  }
  foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {continue}
    $version=& $candidate -c 'import sys,struct;print(int(sys.version_info>=(3,12) and struct.calcsize(chr(80))==8))'
    if ($LASTEXITCODE -eq 0 -and [string]$version -eq '1') {
      $pythonw=Join-Path (Split-Path -Parent $candidate) 'pythonw.exe'
      if (Test-Path -LiteralPath $pythonw) {return [pscustomobject]@{python=$candidate;pythonw=$pythonw}}
    }
  }
  throw 'Install 64-bit Python 3.12+ (including pythonw.exe), or pass -PythonExe with its full path. No Python was installed automatically.'
}
function Read-MonitorReceipt([string]$Directory) {
  $receipt=Get-Content -LiteralPath (Join-Path $Directory 'install.json') -Raw -Encoding utf8 | ConvertFrom-Json
  if ($receipt.product -ne 'CodexQuotaMonitor.Share' -or [IO.Path]::GetFullPath($receipt.install_root) -ne [IO.Path]::GetFullPath($Directory)) {throw 'Installation identity mismatch'}
  return $receipt
}
function Assert-OwnedTask($Task,$Receipt) {
  if ($Task.Actions.Count -ne 1 -or $Task.Actions[0].Execute -ne $Receipt.pythonw_exe -or $Task.Actions[0].Arguments -ne $Receipt.task_arguments) {throw 'Scheduled task is not owned by this installation; no action taken'}
}
function Wait-MonitorStopped([string]$Directory,[string]$TaskName) {
  [IO.File]::WriteAllText((Join-Path $Directory 'STOP_MONITOR.request'),'stop',[Text.UTF8Encoding]::new($false))
  $deadline=[DateTime]::UtcNow.AddSeconds(20)
  do {$task=Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue;if (-not $task -or $task.State -ne 'Running') {return};Start-Sleep -Milliseconds 250} while ([DateTime]::UtcNow -lt $deadline)
  throw 'Monitor has not stopped yet. No process was force-closed; wait and retry.'
}
