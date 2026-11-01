# End-to-end smoke (SAFE): no Angel SmartAPI calls.
# Exercises scheduler entrypoints, journal, console, report, notify path.
#
#   powershell -ExecutionPolicy Bypass -File scripts\ops_e2e_smoke.ps1

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$env:PYTHONPATH = Join-Path $Repo "src"
Set-Location $Repo

function Step([string]$Name, [scriptblock]$Block) {
    Write-Host ""
    Write-Host "==== $Name ====" -ForegroundColor Cyan
    & $Block
    Write-Host ("---- exit={0} ----" -f $LASTEXITCODE) -ForegroundColor DarkGray
}

Write-Host "AlphaDesk E2E SMOKE (Angel API stays OFF unless you already enabled it)" -ForegroundColor Green
Write-Host "Repo: $Repo"

Step "unit tests (ops + angel adapter)" {
    & $Python -m pytest tests/test_ops_desk_state.py tests/test_angelone_adapter.py -q --tb=line
}

Step "ao_smoke without --i-allow-network (must REFUSE)" {
    & $Python scripts\ao_smoke.py
}

Step "ops preopen (skip if Angel allow=false)" {
    powershell -NoProfile -File scripts\ops_daily.ps1 -Mode preopen
}

Step "ops live short window (skip if Angel allow=false)" {
    powershell -NoProfile -File scripts\ops_daily.ps1 -Mode live -Minutes 0.05 -LiveLimit 5
}

Step "ops eod (skip if Angel allow=false)" {
    powershell -NoProfile -File scripts\ops_daily.ps1 -Mode eod
}

Step "ops paper (may FAIL until bars exist - expected)" {
    powershell -NoProfile -File scripts\ops_daily.ps1 -Mode paper
}

Step "ops train (may FAIL until bars exist - expected)" {
    powershell -NoProfile -File scripts\ops_daily.ps1 -Mode train
}

Step "daily report (local file; Telegram only if creds set)" {
    & $Python scripts\ops_daily_report.py --send
}

Step "console snapshot (this is what you see live)" {
    & $Python scripts\ops_console.py --once
}

Step "scheduled tasks present" {
    Get-ScheduledTask -TaskName "AlphaDesk-*" | Select-Object TaskName, State | Format-Table -AutoSize
}

Write-Host ""
Write-Host "E2E SMOKE COMPLETE" -ForegroundColor Green
Write-Host "Open live terminal anytime:" -ForegroundColor Yellow
Write-Host "  powershell -File scripts\ops_open_console.ps1"
Write-Host "Or desktop shortcut: AlphaDesk OPS Console"
Write-Host "Journal/report: $Repo\logs\ops\"
