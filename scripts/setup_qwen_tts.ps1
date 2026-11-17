$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.runtime/qwen-tts/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv .runtime/qwen-tts
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the isolated environment.' }
}
$env:PIP_CACHE_DIR = Join-Path $taskRoot '.runtime/pip-cache'
& $taskPython -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed.' }
& $taskPython -m pip install -r scripts/qwen_tts_requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Qwen TTS installation failed.' }
$env:HF_HUB_DISABLE_XET = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
& .runtime/qwen-tts/Scripts/hf.exe download Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign --revision 5ecdb67327fd37bb2e042aab12ff7391903235d3 --local-dir models/qwen3-tts/VoiceDesign --exclude '*.safetensors' --max-workers 4
if ($LASTEXITCODE -ne 0) { throw 'VoiceDesign model download failed.' }
& .runtime/qwen-tts/Scripts/hf.exe download Qwen/Qwen3-TTS-12Hz-1.7B-Base --revision fd4b254389122332181a7c3db7f27e918eec64e3 --local-dir models/qwen3-tts/Base --exclude '*.safetensors' --max-workers 4
if ($LASTEXITCODE -ne 0) { throw 'Base model download failed.' }
& $taskPython scripts/download_qwen_tts.py
if ($LASTEXITCODE -ne 0) { throw 'Verified model weight download failed.' }
Write-Host 'Ready. Start Saya, then open Settings > Voice.'
