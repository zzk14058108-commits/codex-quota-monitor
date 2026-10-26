param([string]$InstallDir=(Join-Path $env:LOCALAPPDATA 'CodexQuotaMonitor'),[string]$PythonExe='',[string]$CodexExe='',[string]$CodexHome='',[switch]$CheckOnly,[switch]$NoStart)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
if (-not $CodexExe -and $env:CODEX_EXE) {$CodexExe=$env:CODEX_EXE}
$python=Resolve-MonitorPython $PythonExe
$target=[IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
if ($target -eq [IO.Path]::GetPathRoot($target).TrimEnd('\') -or $target -eq [IO.Path]::GetFullPath($env:USERPROFILE) -or $target -eq [IO.Path]::GetFullPath($PSScriptRoot)) {throw 'Choose a dedicated installation folder, not a drive root, profile or extracted package folder'}
$doctorArgs=@('-B',(Join-Path $PSScriptRoot 'doctor.py'),'--local-only')
if ($CodexExe) {$doctorArgs+=@('--codex-exe',$CodexExe)}
if ($CodexHome) {$doctorArgs+=@('--codex-home',$CodexHome)}
$json=& $python.python @doctorArgs
$doctorExit=$LASTEXITCODE
if ($doctorExit -ne 0) {$json | Write-Output;throw 'Compatibility preflight failed; nothing installed'}
$doctor=$json | ConvertFrom-Json
$existing=@(Get-ScheduledTask -TaskName 'CodexQuotaMonitor','CodexQuotaMonitorPortable' -ErrorAction SilentlyContinue)
if ($CheckOnly) {
  [ordered]@{passed=$true;check_only=$true;python=$python.python;codex_exe=$doctor.details.codex_exe;existing_monitor_tasks=@($existing | Select-Object TaskName,State);would_refuse_duplicate=($existing.Count -gt 0);files_written=0;tasks_registered=0;model_turns_started=0;APP_closed=$false} | ConvertTo-Json -Depth 5
  exit 0
}
if ($existing.Count) {throw 'An existing quota monitor task is present. Nothing changed. Uninstall that monitor first; duplicate installation is refused.'}
if (Test-Path -LiteralPath $target) {
  if (@(Get-ChildItem -LiteralPath $target -Force).Count) {throw 'Target folder is not empty. No existing data will be overwritten.'}
}
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'MANIFEST.json') -Raw -Encoding utf8 | ConvertFrom-Json
foreach ($prop in $manifest.sha256.PSObject.Properties) {
  $path=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot $prop.Name))
  if (-not $path.StartsWith(([IO.Path]::GetFullPath($PSScriptRoot)+'\'),[StringComparison]::OrdinalIgnoreCase)) {throw 'Invalid package path'}
  if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $prop.Value) {throw ('Package checksum failed: '+$prop.Name)}
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
$copied=@('launcher.py','runtime_support.py','doctor.py','Common.ps1','Manage.ps1','Doctor.ps1','Uninstall.ps1','README.md')
foreach ($name in $copied) {Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $target $name)}
foreach ($name in @('monitor.py','desktop_tasks.py','desktop_task.ps1','quota_rpc.py','winproc.py')) {Copy-Item -LiteralPath (Join-Path $PSScriptRoot ('core\'+$name)) -Destination (Join-Path $target $name)}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'config.default.json') -Destination (Join-Path $target 'config.json')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'MANIFEST.json') -Destination (Join-Path $target 'package-manifest.json')
$runtime=[ordered]@{codex_exe=$CodexExe;codex_home=$doctor.details.codex_home;python_exe=$python.python}
# An empty codex_exe preserves automatic executable discovery across normal app updates.
[IO.File]::WriteAllText((Join-Path $target 'runtime.json'),($runtime | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
$arguments='-B -u "'+(Join-Path $target 'launcher.py')+'" --home "'+$target+'"'
$receipt=[ordered]@{product='CodexQuotaMonitor.Share';version='1.6.1-share.2';install_root=$target;python_exe=$python.python;pythonw_exe=$python.pythonw;task_name='CodexQuotaMonitorPortable';task_arguments=$arguments;installed_at=(Get-Date -Format o)}
[IO.File]::WriteAllText((Join-Path $target 'install.json'),($receipt | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
$action=New-ScheduledTaskAction -Execute $python.pythonw -Argument $arguments -WorkingDirectory $target
$trigger=New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
try {Register-ScheduledTask -TaskName $receipt.task_name -InputObject (New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings) | Out-Null}
catch {throw ('Files prepared but scheduled task registration failed: '+$_.Exception.Message+'. Existing Codex was not modified. Keep the folder for diagnosis, resolve Task Scheduler rights, then retry with a NEW empty installation directory.')}
if (-not $NoStart) {Start-ScheduledTask -TaskName $receipt.task_name}
[ordered]@{installed=$true;install_directory=$target;task_name=$receipt.task_name;started=(-not $NoStart);auto_resume=$true;Codex_APP_opened_or_closed=$false} | ConvertTo-Json
