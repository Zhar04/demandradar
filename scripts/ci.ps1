# Локальный CI: линт + тесты. Запуск: .\scripts\ci.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

& $py -m ruff check (Join-Path $root "src") (Join-Path $root "tests")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $py -m pytest
exit $LASTEXITCODE
