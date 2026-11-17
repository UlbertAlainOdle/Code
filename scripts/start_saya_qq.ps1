param([ValidateRange(1,65535)][int]$Port = 8000, [ValidateSet('panel','start','stop','restart','status')][string]$Action = 'panel')
$ErrorActionPreference = 'Stop'
try {
    $sayaStartRoot = Split-Path -Parent $PSScriptRoot
    $sayaStartInstall = Get-Content -LiteralPath (Join-Path $sayaStartRoot '.runtime\napcat\install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $sayaStartPython = $sayaStartInstall.python
    if (-not $sayaStartPython -or -not (Test-Path -LiteralPath $sayaStartPython -PathType Leaf)) { throw 'Configured Python is missing. Restore the managed installation first.' }
    Set-Location -LiteralPath $sayaStartRoot
    if ($Action -eq 'panel') {
        $sayaPanelPython = Join-Path (Split-Path -Parent $sayaStartPython) 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $sayaPanelPython)) { $sayaPanelPython = $sayaStartPython }
        $sayaPanelArgs = @(('"' + (Join-Path $PSScriptRoot 'start_saya_qq.py') + '"'), '--port', $Port, '--action', 'panel')
        $sayaPanelProcess = Start-Process -FilePath $sayaPanelPython -ArgumentList $sayaPanelArgs -WorkingDirectory $sayaStartRoot -WindowStyle Hidden -Wait -PassThru
        exit $sayaPanelProcess.ExitCode
    }
    & $sayaStartPython (Join-Path $PSScriptRoot 'start_saya_qq.py') --port $Port --action $Action
    exit $LASTEXITCODE
} catch {
    Write-Host ('Startup failed: ' + $_.Exception.Message)
    exit 1
}
