[CmdletBinding(SupportsShouldProcess=$true)]
param([switch]$Remove, [string]$Python = '', [ValidateRange(1,65535)][int]$Port = 8000, [switch]$ValidationTask)
$ErrorActionPreference = 'Stop'
$qqRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$qqBytes = [Text.Encoding]::UTF8.GetBytes($qqRoot.ToLowerInvariant())
$qqHash = [Security.Cryptography.SHA256]::Create()
try { $qqSuffix = ([BitConverter]::ToString($qqHash.ComputeHash($qqBytes))).Replace('-','').Substring(0,12) }
finally { $qqHash.Dispose() }
$qqTaskName = "Saya-QQ-$qqSuffix"
if ($ValidationTask) { $qqTaskName = "Saya-QQ-Validation-$qqSuffix" }
$qqLauncher = Join-Path $PSScriptRoot 'start_qq_managed.ps1'
$qqDescription = "Saya QQ workspace=$qqSuffix; validation=$ValidationTask"
$qqExisting = @(Get-ScheduledTask -TaskName $qqTaskName -TaskPath '\' -ErrorAction SilentlyContinue)
$qqExpectedArgs = '^-NoLogo -NoProfile -WindowStyle Hidden -File "' + [regex]::Escape($qqLauncher) + '" -RespectStopIntent -Port [0-9]+(?: -ValidationOnly)?(?: -Python "[^"\r\n]+")?$'
if ($qqExisting.Count -gt 0 -and ($qqExisting.Count -ne 1 -or @($qqExisting[0].Actions).Count -ne 1 -or
    $qqExisting[0].Description -ne $qqDescription -or
    $qqExisting[0].Actions[0].Arguments -notmatch $qqExpectedArgs -or
    [IO.Path]::GetFileName($qqExisting[0].Actions[0].Execute) -notin @('pwsh.exe','powershell.exe'))) {
    throw 'An unrelated task has the same name. It was preserved.'
}
if ($Remove) {
    if ($qqExisting.Count -gt 0 -and $PSCmdlet.ShouldProcess($qqTaskName,'Remove only this workspace login task')) {
        Unregister-ScheduledTask -TaskName $qqTaskName -TaskPath '\' -Confirm:$false
    }
    return
}
$qqShell = (Get-Process -Id $PID).Path
$qqArgs = '-NoLogo -NoProfile -WindowStyle Hidden -File "' + $qqLauncher + '" -RespectStopIntent -Port ' + $Port
if ($ValidationTask) { $qqArgs += ' -ValidationOnly' }
if ($Python) {
    if ($Python.Contains('"')) { throw 'Python path contains an invalid quote.' }
    $qqArgs += ' -Python "' + $Python + '"'
}
$qqIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($PSCmdlet.ShouldProcess($qqTaskName,'Register optional current-user login startup')) {
    $qqAction = New-ScheduledTaskAction -Execute $qqShell -Argument $qqArgs -WorkingDirectory $qqRoot
    $qqTrigger = New-ScheduledTaskTrigger -AtLogOn -User $qqIdentity
    $qqPrincipal = New-ScheduledTaskPrincipal -UserId $qqIdentity -LogonType Interactive -RunLevel Limited
    $qqSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $qqTaskName -TaskPath '\' -Description $qqDescription -Action $qqAction -Trigger $qqTrigger -Principal $qqPrincipal -Settings $qqSettings -Force | Out-Null
    Write-Host "Current-user login startup registered: $qqTaskName"
}
