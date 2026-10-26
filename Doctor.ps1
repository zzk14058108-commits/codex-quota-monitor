param([string]$PythonExe='',[switch]$LocalOnly,[string]$ThreadId='',[string]$OutputFile='')
. (Join-Path $PSScriptRoot 'Common.ps1')
$runtime=Join-Path $PSScriptRoot 'runtime.json'
if (-not $PythonExe -and (Test-Path -LiteralPath $runtime)) {$PythonExe=(Get-Content -LiteralPath $runtime -Raw -Encoding utf8 | ConvertFrom-Json).python_exe}
$python=Resolve-MonitorPython $PythonExe
$arguments=@('-B',(Join-Path $PSScriptRoot 'doctor.py'))
if ($LocalOnly) {$arguments+='--local-only'}
if ($ThreadId) {$arguments+=@('--thread-id',$ThreadId)}
if ($OutputFile) {$arguments+=@('--output',$OutputFile)}
& $python.python @arguments
exit $LASTEXITCODE
