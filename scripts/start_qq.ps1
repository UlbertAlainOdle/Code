param(
    [string]$ConfigPath = '.env.qq',
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
if (-not [System.IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath = Join-Path $projectPath $ConfigPath
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw 'QQ config missing. Copy .env.qq.example to .env.qq and edit IDs/tokens first.'
}

# Parse data, never execute the config as PowerShell. Secret values are never printed.
$allowedNames = @(
    'ENABLED', 'BOT_ID', 'API_URL', 'ACCESS_TOKEN', 'WEBHOOK_SECRET',
    'WEBHOOK_TOKEN', 'ADMIN_TOKEN', 'ALLOWED_USERS', 'ALLOWED_GROUPS',
    'OWNER_IDS', 'SHARED_MEMORY', 'GROUP_MENTION_ONLY', 'AUTO_ACCEPT_OWNER_INVITES', 'OWNER_GROUP_AUTO_ENABLE', 'MODEL',
    'COOLDOWN_SECONDS', 'QUEUE_SIZE', 'MAX_EVENT_AGE', 'REPLY_TIMEOUT',
    'HISTORY_TURNS', 'RETENTION_DAYS', 'CHUNK_CHARS', 'MAX_REPLY_CHARS',
    'THINKING_EFFORT', 'AGENT_MODE', 'RECALL_MODE', 'RECALL_MEMORY_COUNT', 'MEMORY_TOKEN_BUDGET',
    'GRAPH_RECALL_MODE', 'MEMORY_WRITE_ENABLED', 'WEB_SEARCH_MODE', 'MAX_OUTPUT_TOKENS'
) | ForEach-Object { 'SAYA_QQ_' + $_ }
$allowedNames += @('SAYA_STARTUP_MODEL', 'API_KEY', 'API_URL', 'API_MODEL')
$parsedValues = @{}
$lineNumber = 0
foreach ($line in Get-Content -LiteralPath $ConfigPath -Encoding UTF8) {
    $lineNumber++
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    if ($trimmed -notmatch '^([A-Z][A-Z0-9_]*)\s*=(.*)$') {
        throw "Invalid config syntax at line $lineNumber. Expected NAME=value."
    }
    $variableName = $Matches[1]
    $variableValue = $Matches[2].Trim()
    if ($allowedNames -notcontains $variableName) {
        throw "Unsupported config variable at line $lineNumber."
    }
    if ($variableValue.Length -ge 2 -and (
        ($variableValue.StartsWith('"') -and $variableValue.EndsWith('"')) -or
        ($variableValue.StartsWith("'") -and $variableValue.EndsWith("'")))) {
        $variableValue = $variableValue.Substring(1, $variableValue.Length - 2)
    }
    if ($variableValue -like 'replace-with-*') {
        throw "Replace the example token at line $lineNumber before starting QQ."
    }
    $parsedValues[$variableName] = $variableValue
}

$previousValues = @{}
try {
    foreach ($variableName in $parsedValues.Keys) {
        $previousValues[$variableName] = [Environment]::GetEnvironmentVariable($variableName, 'Process')
        [Environment]::SetEnvironmentVariable($variableName, $parsedValues[$variableName], 'Process')
    }
    Push-Location -LiteralPath $projectPath
    try {
        Write-Host "Saya QQ status: http://127.0.0.1:$Port/static/qq.html"
        # One worker only: the reply consumer and model are process-owned.
        & $Python (Join-Path $PSScriptRoot 'run_qq_backend.py') --port $Port
        $resultCode = $LASTEXITCODE
    } finally { Pop-Location }
} finally {
    foreach ($variableName in $previousValues.Keys) {
        if ($null -eq $previousValues[$variableName]) {
            # Recent PowerShell/.NET can preserve null as an empty environment
            # entry. Remove it explicitly to restore an originally absent key.
            Remove-Item -LiteralPath ('Env:' + $variableName) -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($variableName, $previousValues[$variableName], 'Process')
        }
    }
}
if ($resultCode -ne 0) { throw "Saya exited with code $resultCode." }
