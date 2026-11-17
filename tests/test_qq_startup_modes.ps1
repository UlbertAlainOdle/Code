param([string]$Python = 'D:\Program\anaconda3\envs\Saya\python.exe')
$ErrorActionPreference = 'Stop'
$qqOriginalRoot = Split-Path -Parent $PSScriptRoot
$qqFixtureRoot = Join-Path $qqOriginalRoot ('.runtime\qq-upgrade\startup-mode-test-' + [Guid]::NewGuid().ToString('N'))
$qqFixtureScripts = Join-Path $qqFixtureRoot 'scripts'
$qqFixtureState = Join-Path $qqFixtureRoot '.runtime\qq-service'
New-Item -ItemType Directory -Path $qqFixtureScripts,$qqFixtureState -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $qqOriginalRoot 'scripts\start_qq_managed.ps1') -Destination $qqFixtureScripts
$qqFakeManager = "from pathlib import Path`nPath(__file__).with_suffix('.started').write_text('fake manager only')`n"
Set-Content -LiteralPath (Join-Path $qqFixtureScripts 'manage_qq_service.py') -Value $qqFakeManager -Encoding utf8
$qqStartFixture = Join-Path $qqFixtureScripts 'start_qq_managed.ps1'
$qqMarker = Join-Path $qqFixtureScripts 'manage_qq_service.started'
$qqIntentFixture = Join-Path $qqFixtureState 'intent.json'
& $qqStartFixture -Python $Python -RespectStopIntent
if (Test-Path -LiteralPath $qqMarker) { throw 'Missing intent unexpectedly started a service.' }
foreach ($qqMode in @('manual_stop','maintenance')) {
    @{mode=$qqMode;revision=1} | ConvertTo-Json | Set-Content -LiteralPath $qqIntentFixture -Encoding utf8
    & $qqStartFixture -Python $Python -RespectStopIntent
    if (Test-Path -LiteralPath $qqMarker) { throw 'Deliberate stop unexpectedly started a service.' }
}
Set-Content -LiteralPath $qqIntentFixture -Value '{broken' -Encoding utf8
$qqRejected = $false
try { & $qqStartFixture -Python $Python -RespectStopIntent } catch { $qqRejected = $true }
if (-not $qqRejected -or (Test-Path -LiteralPath $qqMarker)) { throw 'Broken intent did not fail closed.' }
@{mode='running';revision=2} | ConvertTo-Json | Set-Content -LiteralPath $qqIntentFixture -Encoding utf8
& $qqStartFixture -Python $Python -ValidationOnly -RespectStopIntent
if (Test-Path -LiteralPath $qqMarker) { throw 'Validation-only unexpectedly started a service.' }
& $qqStartFixture -Python $Python -RespectStopIntent
if (-not (Test-Path -LiteralPath $qqMarker)) { throw 'Explicit running intent did not reach the isolated fake manager.' }
Write-Output 'PASS: absent/stopped/maintenance/corrupted/running/validation-only startup intents; isolated fake manager, no QQ processes.'
