# Install the monitor as a Windows Scheduled Task that starts at logon and is restarted automatically.
#   powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1            # install + start
#   powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1 -Remove    # stop + uninstall
#
# How restart works on Windows:
#   * Trigger 1: at logon -> starts supervisor.py (no console window, uses pythonw.exe).
#   * Trigger 2: every minute -> if the supervisor is not running (killed, crashed), the task starts it
#     again; if it is running, the new start is ignored (MultipleInstances = IgnoreNew).
#   * The supervisor restarts the collector and the dashboard within seconds.
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $PSScriptRoot
$TaskName = "EGN321 Sensor Monitor"

if ($Remove) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed task '$TaskName'."
    exit 0
}

$Python = Join-Path $Project ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Python)) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "pythonw.exe not found. Create the virtual environment first (see README)." }
    $Python = $cmd.Source
}

$action    = New-ScheduledTaskAction -Execute $Python -Argument "supervisor.py" -WorkingDirectory $Project
$atLogon   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$watchdog  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
               -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
               -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atLogon, $watchdog) -Settings $settings `
    -Principal $principal -Description "EGN321 Module 7 unattended sensor monitor (supervisor.py)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started '$TaskName'."
Write-Host "Dashboard: http://127.0.0.1:5000    Logs: $Project\logs\"
