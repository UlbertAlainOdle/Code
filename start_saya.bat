@echo off
chcp 65001 > nul
title Saya+ Launcher
echo ===================================================
echo              Starting Saya+ System
echo ===================================================
echo.
echo [1/3] Activating Conda environment 'groundingdino'...
set "BILIBILI_COOKIE_FILE=%~dp0knowledge_base\bilibili_cookie.txt"
if exist "%BILIBILI_COOKIE_FILE%" (
    for /f "usebackq delims=" %%A in ("%BILIBILI_COOKIE_FILE%") do (
        set "BILIBILI_COOKIE=%%A"
        goto :bilibili_cookie_loaded
    )
)
:bilibili_cookie_loaded
if defined BILIBILI_COOKIE (
    echo Optional Bilibili cookie: configured.
) else (
    echo Optional Bilibili cookie: not configured.
    echo To improve Bilibili fetching, create knowledge_base\bilibili_cookie.txt with your browser Cookie.
)
set "YOUTUBE_COOKIE_FILE=%~dp0knowledge_base\youtube_cookie.txt"
if exist "%YOUTUBE_COOKIE_FILE%" (
    for /f "usebackq delims=" %%A in ("%YOUTUBE_COOKIE_FILE%") do (
        set "YOUTUBE_COOKIE=%%A"
        goto :youtube_cookie_loaded
    )
)
:youtube_cookie_loaded
if defined YOUTUBE_COOKIE (
    echo Optional YouTube cookie: configured.
) else (
    echo Optional YouTube cookie: not configured.
)
set "YOUTUBE_API_KEY_FILE=%~dp0knowledge_base\youtube_api_key.txt"
if exist "%YOUTUBE_API_KEY_FILE%" (
    for /f "usebackq delims=" %%A in ("%YOUTUBE_API_KEY_FILE%") do (
        set "YOUTUBE_API_KEY=%%A"
        goto :youtube_api_key_loaded
    )
)
:youtube_api_key_loaded
if defined YOUTUBE_API_KEY (
    echo Optional YouTube API key: configured.
) else (
    echo Optional YouTube API key: not configured.
    echo To fetch YouTube hot comments, create knowledge_base\youtube_api_key.txt with your API key.
)

:: Start backend in a separate window so the script can continue and open the browser
echo [2/3] Starting Backend Server in a new window...
start "Saya+ Backend Server" cmd /k "conda activate groundingdino && uvicorn server5-22B----:app --host 127.0.0.1 --port 8000"

echo [3/3] Waiting for server to initialize (8 seconds)...
timeout /t 8 /nobreak > nul

echo Opening Browser...
start http://127.0.0.1:8000/static/Chat4o5-Modern.html?v=g13-20260811-citation-memory

echo.
echo Done! The backend is running in the new command window.
echo You can safely close this launcher window.
pause
