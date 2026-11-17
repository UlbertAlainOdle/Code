param([string]$Python = '', [ValidateRange(1,65535)][int]$Port = 8000, [switch]$RespectStopIntent, [switch]$SuperviseNapCat, [switch]$ValidationOnly)
$ErrorActionPreference = 'Stop'
if ($ValidationOnly) { Write-Host 'QQ startup validation only; no services started.'; return }
$qqRoot = Split-Path -Parent $PSScriptRoot
if ($RespectStopIntent) {
    $qqIntentPath = Join-Path $qqRoot '.runtime\qq-service\intent.json'
    if (-not (Test-Path -LiteralPath $qqIntentPath)) { Write-Host 'QQ startup is not enabled.'; return }
    $qqIntent = Get-Content -LiteralPath $qqIntentPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($qqIntent.mode -ne 'running') { Write-Host 'Manual stop or maintenance preserved.'; return }
}
$qqInstallPath = Join-Path $qqRoot '.runtime\napcat\install.json'
if (Test-Path -LiteralPath $qqInstallPath) {
    $qqInstall = Get-Content -LiteralPath $qqInstallPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $Python) { $Python = $qqInstall.python }
}
if (-not $Python) { $Python = 'python' }
if (Test-Path -LiteralPath $qqInstallPath) {
    & $Python (Join-Path $PSScriptRoot 'manage_qq_service.py') napcat-start --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Dedicated NapCat startup could not be confirmed; existing processes were preserved.' }
}
if ($SuperviseNapCat) {
    & $Python (Join-Path $PSScriptRoot 'manage_qq_service.py') napcat-enable --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'NapCat supervision could not be enabled.' }
}
& $Python (Join-Path $PSScriptRoot 'manage_qq_service.py') start --port $Port
if ($LASTEXITCODE -ne 0) { throw 'QQ service manager could not start. Check the local service status.' }
