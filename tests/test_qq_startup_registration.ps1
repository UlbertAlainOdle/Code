param([string]$Python = 'D:\Program\anaconda3\envs\Saya\python.exe')
$ErrorActionPreference = 'Stop'
$qqTestRoot = Split-Path -Parent $PSScriptRoot
$qqRegistration = Join-Path $qqTestRoot 'scripts\register_qq_service.ps1'
$qqDigest = [Security.Cryptography.SHA256]::Create()
try { $qqTestSuffix = ([BitConverter]::ToString($qqDigest.ComputeHash([Text.Encoding]::UTF8.GetBytes($qqTestRoot.ToLowerInvariant())))).Replace('-','').Substring(0,12) }
finally { $qqDigest.Dispose() }
$qqTestTaskName = "Saya-QQ-Validation-$qqTestSuffix"
if (Get-ScheduledTask -TaskName $qqTestTaskName -TaskPath '\' -ErrorAction SilentlyContinue) {
    throw 'A validation task already exists; it was preserved.'
}
$qqTestCreated = $false
try {
    & $qqRegistration -ValidationTask -Python $Python
    $qqTestCreated = $true
    $qqTask = Get-ScheduledTask -TaskName $qqTestTaskName -TaskPath '\'
    if ($qqTask.Actions.Count -ne 1 -or $qqTask.Actions[0].Arguments -notmatch '-ValidationOnly' -or
        $qqTask.Settings.MultipleInstances -ne 'IgnoreNew' -or $qqTask.Principal.RunLevel -ne 'Limited') {
        throw 'Registered task did not preserve the required safe settings.'
    }
    Start-ScheduledTask -TaskName $qqTestTaskName -TaskPath '\'
    $qqDeadline = [DateTime]::UtcNow.AddSeconds(25)
    do {
        Start-Sleep -Milliseconds 500
        $qqInfo = Get-ScheduledTaskInfo -TaskName $qqTestTaskName -TaskPath '\'
        $qqTask = Get-ScheduledTask -TaskName $qqTestTaskName -TaskPath '\'
    } while ([DateTime]::UtcNow -lt $qqDeadline -and ($qqInfo.LastRunTime.Year -lt 2026 -or $qqTask.State -eq 'Running'))
    if ($qqInfo.LastTaskResult -ne 0 -or $qqInfo.LastRunTime.Year -lt 2026) { throw 'Validation-only startup did not complete.' }
    & $qqRegistration -ValidationTask -Remove
    $qqTestCreated = $false
    if (Get-ScheduledTask -TaskName $qqTestTaskName -TaskPath '\' -ErrorAction SilentlyContinue) { throw 'Validation task was not removed.' }
    Write-Output 'PASS: current-user registration, validation-only run, precise removal; no QQ services started.'
} finally {
    if ($qqTestCreated) { & $qqRegistration -ValidationTask -Remove }
}
