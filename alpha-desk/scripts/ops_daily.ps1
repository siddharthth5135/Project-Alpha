# Alpha-desk unattended ops entrypoint (Windows Task Scheduler).
# Modes: preopen | live | eod | train
#
# Example:
#   powershell -File scripts\ops_daily.ps1 -Mode live -Minutes 180

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("preopen", "live", "eod", "train")]
    [string]$Mode,

    [double]$Minutes = 180,

    [int]$LiveLimit = 50,

    [string]$ContractId = "NSE:FUT:NIFTY:2026-09-29",

    [string]$Interval = "1m"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$LogRoot = Join-Path $Repo "logs\ops"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

if (-not (Test-Path $Python)) {
    throw "venv python not found: $Python"
}

$env:PYTHONPATH = Join-Path $Repo "src"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogRoot ("ops_{0}_{1}.log" -f $Mode, $Stamp)

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "o"), $Message
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

function Invoke-Heartbeat([string]$Job, [string]$Status, [int]$Code = 0, [string]$Detail = "") {
    & $Python (Join-Path $Repo "scripts\ops_heartbeat.py") --job $Job --status $Status --exit-code $Code --detail $Detail
}

function Invoke-Py([string[]]$ArgsList) {
    Write-Log ("RUN: {0} {1}" -f $Python, ($ArgsList -join " "))
    & $Python @ArgsList *>> $LogFile
    Write-Host ("python exit={0}" -f $LASTEXITCODE)
    return [int]$LASTEXITCODE
}

Write-Log "mode=$Mode repo=$Repo"
Invoke-Heartbeat -Job $Mode -Status "start" -Detail "begin"

$exit = 0
try {
    switch ($Mode) {
        "preopen" {
            $exit = Invoke-Py @((Join-Path $Repo "scripts\td_smoke.py"))
            if ($exit -eq 0) {
                $exit = Invoke-Py @((Join-Path $Repo "scripts\ops_integrity.py"), "--max-age-hours", "72")
            }
        }
        "live" {
            $exit = Invoke-Py @(
                (Join-Path $Repo "scripts\td_record_live.py"),
                "--minutes", "$Minutes",
                "--limit", "$LiveLimit",
                "--capture-raw",
                "--strikes", "3"
            )
        }
        "eod" {
            $exit = Invoke-Py @(
                (Join-Path $Repo "scripts\td_backfill.py"),
                "--scope", "futures",
                "--interval", "15m",
                "--days", "15"
            )
            if ($exit -ne 0 -and $exit -ne 1) { throw "backfill hard-failed exit=$exit" }
            # exit 1 from backfill often means partial quarantine — continue integrity
            $exit = Invoke-Py @((Join-Path $Repo "scripts\ops_integrity.py"), "--max-age-hours", "24")
            if ($exit -eq 0) {
                $report = Invoke-Py @((Join-Path $Repo "scripts\td_trial_report.py"))
                if ($report -ne 0) { $exit = $report }
            }
        }
        "train" {
            $exit = Invoke-Py @(
                (Join-Path $Repo "scripts\train_from_store.py"),
                "--contract-id", $ContractId,
                "--interval", $Interval,
                "--then-train"
            )
        }
    }
}
catch {
    Write-Log ("ERROR: {0}" -f $_)
    $exit = 1
}

if ($exit -eq 0) {
    Invoke-Heartbeat -Job $Mode -Status "ok" -Code 0 -Detail "log=$LogFile"
    Write-Log "DONE ok"
    exit 0
}

Invoke-Heartbeat -Job $Mode -Status "fail" -Code $exit -Detail "log=$LogFile"
Write-Log ("DONE fail exit={0}" -f $exit)
exit $exit
