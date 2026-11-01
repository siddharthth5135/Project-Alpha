# Alpha-desk unattended ops (Windows Task Scheduler).
# Modes: preopen | live | eod | paper | train
#
# Jobs keep running even if the developer console is closed.
# Open the console anytime: scripts\ops_open_console.ps1
#
# Angel market-data SoR when ANGELONE_ALLOW_MARKETDATA=true. TrueData OFF.
# Live ORDERS stay disabled (paper-only). Not the same as market-data allow.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("preopen", "live", "eod", "paper", "train", "report")]
    [string]$Mode,

    [double]$Minutes = 180,
    [int]$LiveLimit = 50,
    [string]$ContractId = "NSE:FUT:NIFTY:CONT-desk_rolled",
    [string]$Interval = "15m",
    [string]$TrainSource = "angelone"
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

function Invoke-Journal([string]$Phase, [string]$Level, [string]$Message, [string]$Fix = "none") {
    & $Python (Join-Path $Repo "scripts\ops_journal.py") --phase $Phase --level $Level --message $Message --fix $Fix | Out-Null
}

function Invoke-Notify([string]$Level, [string]$Title, [string]$Body, [string]$Fix = "you") {
    & $Python (Join-Path $Repo "scripts\ops_notify.py") --level $Level --title $Title --body $Body --phase $Mode --fix $Fix | Out-Null
}

function Invoke-Heartbeat([string]$Job, [string]$Status, [int]$Code = 0, [string]$Detail = "") {
    & $Python (Join-Path $Repo "scripts\ops_heartbeat.py") --job $Job --status $Status --exit-code $Code --detail $Detail
}

function Invoke-Py([string[]]$ArgsList) {
    Write-Log ("RUN: {0} {1}" -f $Python, ($ArgsList -join " "))
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Python @ArgsList 2>&1
        $code = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    }
    finally {
        $ErrorActionPreference = $prev
    }
    if ($null -ne $output) {
        foreach ($line in @($output)) {
            Add-Content -Path $LogFile -Value ([string]$line)
            $s = [string]$line
            if ($s -match "(?i)error|traceback|failed|refusing") {
                Write-Host $s -ForegroundColor Red
            }
            elseif ($s -match "(?i)warn") {
                Write-Host $s -ForegroundColor Yellow
            }
            else {
                Write-Host $s
            }
        }
    }
    Write-Host ("python exit={0}" -f $code)
    return $code
}

Write-Log "mode=$Mode repo=$Repo"

# NSE holiday / weekend gate (calendar in repo). Morning notice on preopen only.
$dayCheck = & $Python (Join-Path $Repo "scripts\ops_is_trading_day.py") --json 2>$null
$isOpen = $true
try {
    $dayObj = $dayCheck | ConvertFrom-Json
    $isOpen = [bool]$dayObj.is_trading_day
} catch {
    $isOpen = $true
}
if (-not $isOpen) {
    $msg = "NSE holiday/weekend ($($dayObj.date) $($dayObj.weekday)) - no capture/paper/train today."
    Write-Log "HOLIDAY: $msg"
    if ($Mode -eq "preopen") {
        Invoke-Journal -Phase "holiday" -Level "info" -Message $msg -Fix "none"
        Invoke-Notify -Level "info" -Title "AlphaDesk HOLIDAY" -Body $msg -Fix "none"
    }
    elseif ($Mode -eq "report") {
        Invoke-Journal -Phase "holiday" -Level "info" -Message "Holiday report skip (already notified at preopen if weekday holiday)" -Fix "none"
    }
    else {
        Invoke-Journal -Phase "holiday" -Level "info" -Message ("Skipped {0}: {1}" -f $Mode, $msg) -Fix "none"
    }
    Invoke-Heartbeat -Job $Mode -Status "ok" -Code 0 -Detail "holiday_skip"
    exit 0
}

# Hard gate for market-data modes
$envFile = Join-Path $Repo ".env"
$allowMd = $false
if (Test-Path $envFile) {
    $flag = Get-Content $envFile | Where-Object { $_ -match '^\s*ANGELONE_ALLOW_MARKETDATA\s*=' } | Select-Object -First 1
    if ($flag -match '=\s*(.+)$') {
        $val = $Matches[1].Trim().Trim('"').ToLowerInvariant()
        $allowMd = @("1", "true", "yes", "on") -contains $val
    }
}
$needsMd = @("preopen", "live", "eod") -contains $Mode
if ($needsMd -and -not $allowMd) {
    Write-Log "SKIP: ANGELONE_ALLOW_MARKETDATA is not true - no SmartAPI call made"
    Invoke-Journal -Phase $Mode -Level "warn" -Message "Skipped: Angel market-data still OFF. Set ANGELONE_ALLOW_MARKETDATA=true when you approve." -Fix "you"
    Invoke-Heartbeat -Job $Mode -Status "ok" -Code 0 -Detail "skipped_marketdata_disabled"
    exit 0
}

Invoke-Heartbeat -Job $Mode -Status "start" -Detail "begin"
Invoke-Journal -Phase $Mode -Level "info" -Message ("EXE started: {0}" -f $Mode) -Fix "none"

$exit = 0
try {
    switch ($Mode) {
        "preopen" {
            Invoke-Journal -Phase "preopen" -Level "info" -Message "Pre-market analysis / smoke starting" -Fix "none"
            $exit = Invoke-Py @(
                (Join-Path $Repo "scripts\ao_smoke.py"),
                "--i-allow-network",
                "--skip-live"
            )
            if ($exit -eq 0) {
                # Soft until Angel store has fresh bars (bootstrap / weekend gap).
                $exit = Invoke-Py @((Join-Path $Repo "scripts\ops_integrity.py"), "--max-age-hours", "72", "--soft")
            }
            if ($exit -eq 0) {
                Invoke-Journal -Phase "preopen" -Level "ok" -Message "Pre-market checks PASSED" -Fix "none"
            }
            else {
                Invoke-Journal -Phase "preopen" -Level "error" -Message ("Pre-market FAILED exit={0}" -f $exit) -Fix "you"
                Invoke-Notify -Level "error" -Title "AlphaDesk preopen FAIL" -Body ("exit={0} log={1}" -f $exit, $LogFile) -Fix "you"
            }
        }
        "live" {
            Invoke-Journal -Phase "live" -Level "info" -Message ("Live market data capturing STARTED ({0} min)" -f $Minutes) -Fix "none"
            $pyOut = Join-Path $LogRoot ("live_py_{0}.log" -f $Stamp)
            $pyErr = Join-Path $LogRoot ("live_py_{0}.err" -f $Stamp)
            $liveArgs = @(
                "-u",
                (Join-Path $Repo "scripts\ao_record_live.py"),
                "--i-allow-network",
                "--minutes", "$Minutes",
                "--limit", "$LiveLimit",
                "--underlyings", "NIFTY,BANKNIFTY",
                "--equities", "RELIANCE,HDFCBANK,TCS"
            )
            Write-Log ("RUN: {0} {1}" -f $Python, ($liveArgs -join " "))
            Write-Log ("python_stdout=$pyOut")
            $env:PYTHONUNBUFFERED = "1"
            $env:PYTHONPATH = Join-Path $Repo "src"
            $proc = Start-Process -FilePath $Python -ArgumentList $liveArgs `
                -WorkingDirectory $Repo -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $pyOut -RedirectStandardError $pyErr
            Wait-Process -Id $proc.Id
            $proc.Refresh()
            if ($null -eq $proc.ExitCode) {
                Write-Log "WARN: process ExitCode null after wait; treating as fail"
                $exit = 1
            }
            else {
                $exit = [int]$proc.ExitCode
            }
            Write-Log ("python exit={0}" -f $exit)
            if ($exit -eq 0) {
                Invoke-Journal -Phase "live" -Level "ok" -Message "Live capturing DONE for this window" -Fix "none"
            }
            else {
                Invoke-Journal -Phase "live" -Level "error" -Message ("Live capturing FAILED exit={0}" -f $exit) -Fix "you"
                Invoke-Notify -Level "error" -Title "AlphaDesk LIVE FAIL" -Body ("exit={0} see {1}" -f $exit, $pyErr) -Fix "you"
            }
        }
        "eod" {
            Invoke-Journal -Phase "eod" -Level "info" -Message "Post-market activities STARTED (backfill/gapfill/integrity)" -Fix "none"
            $histDays = "30"
            if (Test-Path $envFile) {
                $line = Get-Content $envFile | Where-Object { $_ -match '^\s*ANGELONE_HISTORY_DAYS_M15\s*=' } | Select-Object -First 1
                if ($line -match '=\s*(.+)$') { $histDays = $Matches[1].Trim() }
            }
            $exit = Invoke-Py @(
                (Join-Path $Repo "scripts\ao_backfill.py"),
                "--i-allow-network",
                "--scope", "futures",
                "--interval", "15m",
                "--days", "$histDays"
            )
            if ($exit -ne 0 -and $exit -ne 1) { throw "backfill hard-failed exit=$exit" }
            $eq = Invoke-Py @(
                (Join-Path $Repo "scripts\ao_backfill.py"),
                "--i-allow-network",
                "--scope", "equities",
                "--interval", "15m",
                "--days", "$histDays"
            )
            if ($eq -ne 0 -and $eq -ne 1) {
                Write-Log ("equity backfill soft-fail exit={0}" -f $eq)
                Invoke-Journal -Phase "eod" -Level "warn" -Message ("Equity store soft-fail exit={0} (AUTO will retry next EOD)" -f $eq) -Fix "auto"
            }
            $gap = Invoke-Py @(
                (Join-Path $Repo "scripts\ao_gapfill.py"),
                "--i-allow-network",
                "--scope", "futures",
                "--interval", "1m",
                "--days", "2"
            )
            if ($gap -ne 0 -and $gap -ne 1) {
                Write-Log ("gapfill soft-fail exit={0}" -f $gap)
                Invoke-Journal -Phase "eod" -Level "warn" -Message ("Gapfill soft-fail exit={0} (AUTO retry next EOD)" -f $gap) -Fix "auto"
            }
            else {
                Invoke-Journal -Phase "eod" -Level "ok" -Message "Gapfill ran (AUTO fills same-session holes)" -Fix "auto"
            }
            $exit = Invoke-Py @((Join-Path $Repo "scripts\ops_integrity.py"), "--max-age-hours", "24")
            if ($exit -eq 0) {
                Invoke-Journal -Phase "eod" -Level "ok" -Message "Post-market DONE" -Fix "none"
            }
            else {
                Invoke-Journal -Phase "eod" -Level "error" -Message ("Post-market integrity FAILED exit={0}" -f $exit) -Fix "you"
                Invoke-Notify -Level "error" -Title "AlphaDesk EOD FAIL" -Body ("exit={0} log={1}" -f $exit, $LogFile) -Fix "you"
            }
        }
        "paper" {
            Invoke-Journal -Phase "paper" -Level "info" -Message "Paper trading / score check STARTED (warehouse; no live orders)" -Fix "none"
            # Prefer TrueData store mirrored into warehouse; falls back gracefully.
            & $Python (Join-Path $Repo "scripts\sync_store_to_warehouse.py") `
                "--source" $TrainSource `
                "--contract-id" $ContractId `
                "--symbol" "NIFTY" `
                "--interval" $Interval | Out-Null
            $exit = Invoke-Py @(
                (Join-Path $Repo "scripts\ao_paper_day.py"),
                "--source", $TrainSource,
                "--symbol", "NIFTY"
            )
            if ($exit -ne 0) {
                Invoke-Notify -Level "error" -Title "AlphaDesk PAPER FAIL" -Body ("exit={0} - often missing bars until capture runs" -f $exit) -Fix "you"
            }
        }
        "train" {
            Invoke-Journal -Phase "train" -Level "info" -Message "Model training STARTED (paper/research only; no live orders)" -Fix "none"
            $exit = Invoke-Py @(
                (Join-Path $Repo "scripts\train_from_store.py"),
                "--source", $TrainSource,
                "--contract-id", $ContractId,
                "--interval", $Interval,
                "--then-train"
            )
            if ($exit -eq 0) {
                Invoke-Journal -Phase "train" -Level "ok" -Message "Model training DONE" -Fix "none"
            }
            else {
                Invoke-Journal -Phase "train" -Level "error" -Message ("Training FAILED exit={0} (need enough stored bars)" -f $exit) -Fix "you"
                Invoke-Notify -Level "error" -Title "AlphaDesk TRAIN FAIL" -Body ("exit={0} log={1}" -f $exit, $LogFile) -Fix "you"
            }
        }
        "report" {
            Invoke-Journal -Phase "report" -Level "info" -Message "Building end-of-day report (+ Telegram if configured)" -Fix "none"
            $exit = Invoke-Py @((Join-Path $Repo "scripts\ops_daily_report.py"), "--send")
            if ($exit -eq 0) {
                Invoke-Journal -Phase "report" -Level "ok" -Message "Daily report DONE (file + Telegram if creds set)" -Fix "none"
            }
            else {
                Invoke-Notify -Level "warn" -Title "AlphaDesk REPORT WARN" -Body ("exit={0}" -f $exit) -Fix "you"
            }
        }
    }
}
catch {
    Write-Log ("ERROR: {0}" -f $_)
    Invoke-Journal -Phase $Mode -Level "error" -Message ("Unhandled ERROR: {0}" -f $_) -Fix "you"
    Invoke-Notify -Level "error" -Title "AlphaDesk CRASH" -Body ("{0}" -f $_) -Fix "you"
    $exit = 1
}

if ($exit -eq 0) {
    Invoke-Heartbeat -Job $Mode -Status "ok" -Code 0 -Detail "log=$LogFile"
    Invoke-Journal -Phase $Mode -Level "ok" -Message ("EXE finished OK: {0}" -f $Mode) -Fix "none"
    Write-Log "DONE ok"
    exit 0
}

Invoke-Heartbeat -Job $Mode -Status "fail" -Code $exit -Detail "log=$LogFile"
Invoke-Journal -Phase $Mode -Level "error" -Message ("EXE finished FAIL: {0} exit={1}" -f $Mode, $exit) -Fix "you"
Write-Log ("DONE fail exit={0}" -f $exit)
exit $exit
