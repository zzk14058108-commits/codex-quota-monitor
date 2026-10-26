param([string]$PythonExe='')
. (Join-Path $PSScriptRoot 'Common.ps1')
$python=Resolve-MonitorPython $PythonExe
& $python.python -B (Join-Path $PSScriptRoot 'run_tests.py')
exit $LASTEXITCODE
