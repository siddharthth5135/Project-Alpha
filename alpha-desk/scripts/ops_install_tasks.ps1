# Install / refresh Alpha-desk Windows Scheduled Tasks (IST).
# Work runs with PC ON even if Cursor/console is closed.
#
#   powershell -ExecutionPolicy Bypass -File scripts\ops_install_tasks.ps1
#
# Also creates a desktop shortcut to the transparent console.

param(
    [switch]$RemoveOnly
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Ps = Join-Path $Repo "scripts\ops_daily.ps1"
$ConsolePs = Join-Path $Repo "scripts\ops_open_console.ps1"
$TaskPrefix = "AlphaDesk"

if (-not (Test-Path $Ps)) {
    throw "missing $Ps"
}

$existing = Get-ScheduledTask -TaskName "$TaskPrefix-*" -ErrorAction SilentlyContinue
if ($existing) {
    foreach ($t in $existing) {
        Unregister-ScheduledTask -TaskName $t.TaskName -Confirm:$false
        Write-Host "removed $($t.TaskName)"
    }
}

if ($RemoveOnly) {
    Write-Host "removed only; done"
    exit 0
}

function Register-AlphaTask {
    param(
        [string]$Name,
        [string]$Mode,
        [string]$Time,
        [string]$ExtraArgs = "",
        [string[]]$Days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
    )

    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$Ps`" -Mode $Mode $ExtraArgs"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $Repo
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At $Time
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
        -MultipleInstances IgnoreNew `
        -WakeToRun
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Host "registered $Name at $Time ($Mode)"
}

# Daily market pipeline (IST wall clock — set Windows timezone to India Standard Time)
Register-AlphaTask -Name "$TaskPrefix-PreOpen" -Mode "preopen" -Time "09:00"
Register-AlphaTask -Name "$TaskPrefix-LiveAM" -Mode "live" -Time "09:14" -ExtraArgs "-Minutes 180"
Register-AlphaTask -Name "$TaskPrefix-LivePM" -Mode "live" -Time "12:20" -ExtraArgs "-Minutes 200"
Register-AlphaTask -Name "$TaskPrefix-EOD" -Mode "eod" -Time "15:45"
Register-AlphaTask -Name "$TaskPrefix-Paper" -Mode "paper" -Time "16:05"
Register-AlphaTask -Name "$TaskPrefix-Train" -Mode "train" -Time "16:35" -ExtraArgs "-Interval 15m -TrainSource angelone -ContractId NSE:FUT:NIFTY:CONT-desk_rolled"
Register-AlphaTask -Name "$TaskPrefix-Report" -Mode "report" -Time "16:50"
Register-AlphaTask -Name "$TaskPrefix-WeekendTrain" -Mode "train" -Time "10:00" -Days @("Saturday") -ExtraArgs "-Interval 15m -TrainSource angelone -ContractId NSE:FUT:NIFTY:CONT-desk_rolled"

# Desktop shortcut to console (history on reopen)
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "AlphaDesk OPS Console.lnk"
$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($lnkPath)
$lnk.TargetPath = "powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ConsolePs`""
$lnk.WorkingDirectory = $Repo
$lnk.WindowStyle = 1
$lnk.Description = "AlphaDesk transparent ops console (view-only; jobs keep running)"
$lnk.Save()
Write-Host "desktop shortcut: $lnkPath"

Write-Host ""
Write-Host "Installed. Verify: Get-ScheduledTask -TaskName 'AlphaDesk-*'"
Write-Host "Console:     powershell -File scripts\ops_open_console.ps1"
Write-Host "IMPORTANT: Windows timezone = India Standard Time"
Write-Host "IMPORTANT: PC ON + awake on market days (sleep kills capture)"
Write-Host "IMPORTANT: Angel API stays OFF until ANGELONE_ALLOW_MARKETDATA=true"
Write-Host "Logs/journal: $Repo\logs\ops\"
