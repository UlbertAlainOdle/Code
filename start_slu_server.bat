@echo off
rem Saya 本地理解模型（微调 SLU）服务：OpenAI 兼容接口，供后端 slu_backend=model 使用
rem 关闭本窗口即停止服务；Saya 后端在任何失败时会自动回落到原有流程。
cd /d "%~dp0"
"D:\Program\anaconda3\envs\Dear0\python.exe" scripts\serve_slu_model.py --model "D:\hf_cache\slu_merged_qwen3_4b" --port 8555
