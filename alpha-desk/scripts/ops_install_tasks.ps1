# Install / refresh Alpha-desk Windows Scheduled Tasks (IST market hours).
# Run once in an elevated or normal PowerShell from the repo:
#   powershell -ExecutionPolicy Bypass -File scripts\ops_install_tasks.ps1
#
# Tasks run whether or not Cursor is open. Laptop must be ON and awake.

param(
    [switch]$RemoveOnly
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Ps = Join-Path $Repo "scripts\ops_daily.ps1"
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
    # Local time = machine clock; set Windows timezone to India Standard Time for IST.
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
        -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Host "registered $Name at $Time ($Mode)"
}

Register-AlphaTask -Name "$TaskPrefix-PreOpen" -Mode "preopen" -Time "09:00"
Register-AlphaTask -Name "$TaskPrefix-LiveAM" -Mode "live" -Time "09:14" -ExtraArgs "-Minutes 180"
Register-AlphaTask -Name "$TaskPrefix-LivePM" -Mode "live" -Time "12:20" -ExtraArgs "-Minutes 200"
Register-AlphaTask -Name "$TaskPrefix-EOD" -Mode "eod" -Time "15:45"
Register-AlphaTask -Name "$TaskPrefix-WeekendTrain" -Mode "train" -Time "10:00" -Days @("Saturday")

Write-Host ""
Write-Host "Installed. Verify with: Get-ScheduledTask -TaskName 'AlphaDesk-*'"
Write-Host "IMPORTANT: Windows clock timezone should be India Standard Time."
Write-Host "IMPORTANT: Keep laptop plugged in and awake on market days."
Write-Host "Logs: $Repo\logs\ops\"
