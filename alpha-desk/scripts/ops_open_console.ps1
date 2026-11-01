# Opens the transparent AlphaDesk console (view-only).
# Safe to close — scheduled jobs keep running. Re-run this to see full history.
#
#   powershell -ExecutionPolicy Bypass -File scripts\ops_open_console.ps1

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "venv missing: $Python"
}

$env:PYTHONPATH = Join-Path $Repo "src"
$host.UI.RawUI.WindowTitle = "AlphaDesk OPS CONSOLE (close = view only; jobs keep running)"
Set-Location $Repo
& $Python (Join-Path $Repo "scripts\ops_console.py") --refresh 5
