$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".\.venv\Scripts\python.exe") {
    ".\.venv\Scripts\python.exe"
} else {
    "python"
}

& $python -m pip install -U "pyinstaller>=6.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python -m PyInstaller pmdsaver.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Built: dist\pmdsaver\pmdsaver.exe"
Write-Host "Copy the whole dist\pmdsaver folder to the monitoring PC, then run pmdsaver.exe."
Write-Host "Dashboard: http://127.0.0.1:8080"
Write-Host "SQLite:    dist\pmdsaver\data\pmdsaver.db  (created on first run)"
