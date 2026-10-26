param([string]$PythonExe='',[switch]$LocalOnly,[string]$ThreadId='',[string]$OutputFile='',[string]$InstallDir='',[string]$CodexExe='',[string]$CodexHome='')
. (Join-Path $PSScriptRoot 'Common.ps1')
$codeRoot=$PSScriptRoot
if ($InstallDir) {
  [void](Read-MonitorReceipt $InstallDir)
  $codeRoot=[IO.Path]::GetFullPath($InstallDir)
} elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'install.json')) {
  [void](Read-MonitorReceipt $PSScriptRoot)
} else {
  $default=Join-Path $env:LOCALAPPDATA 'CodexQuotaMonitor'
  if (Test-Path -LiteralPath (Join-Path $default 'install.json')) {
    [void](Read-MonitorReceipt $default)
    $codeRoot=$default
  }
}
$runtime=Join-Path $codeRoot 'runtime.json'
if (-not $PythonExe -and (Test-Path -LiteralPath $runtime)) {$PythonExe=(Get-Content -LiteralPath $runtime -Raw -Encoding utf8 | ConvertFrom-Json).python_exe}
$python=Resolve-MonitorPython $PythonExe
$arguments=@('-B',(Join-Path $codeRoot 'doctor.py'))
if ($LocalOnly) {$arguments+='--local-only'}
if ($ThreadId) {$arguments+=@('--thread-id',$ThreadId)}
if ($OutputFile) {$arguments+=@('--output',$OutputFile)}
if ($CodexExe) {$arguments+=@('--codex-exe',$CodexExe)}
if ($CodexHome) {$arguments+=@('--codex-home',$CodexHome)}
& $python.python @arguments
exit $LASTEXITCODE
