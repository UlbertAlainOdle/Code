@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 > nul
title MMD Try Showcase Launcher
echo ===================================================
echo              Starting MMD Try Showcase
echo ===================================================
echo.
set "SHOWCASE_PORT=8001"
set "SHOWCASE_ENV=newenv"
set "CONDA_ROOT=D:\Program\anaconda3"
set "PYTHON_EXE=%CONDA_ROOT%\envs\%SHOWCASE_ENV%\python.exe"

echo [1/3] Checking Conda environment '!SHOWCASE_ENV!'...
if not exist "!PYTHON_EXE!" (
    echo Conda environment '!SHOWCASE_ENV!' was not found.
    echo Falling back to Conda environment 'groundingdino'...
    set "SHOWCASE_ENV=groundingdino"
    set "PYTHON_EXE=%CONDA_ROOT%\envs\groundingdino\python.exe"
)
"!PYTHON_EXE!" -c "import fastapi, uvicorn" > nul 2> nul
if errorlevel 1 (
    echo Conda environment '!SHOWCASE_ENV!' is missing FastAPI/Uvicorn.
    echo Falling back to Conda environment 'groundingdino'...
    set "SHOWCASE_ENV=groundingdino"
    set "PYTHON_EXE=%CONDA_ROOT%\envs\groundingdino\python.exe"
    "!PYTHON_EXE!" -c "import fastapi, uvicorn" > nul 2> nul
    if errorlevel 1 (
        echo.
        echo ERROR: Could not start because FastAPI/Uvicorn was not found in either environment.
        echo Python command: "!PYTHON_EXE!"
        echo Please install project requirements in newenv or groundingdino.
        echo.
        pause
        exit /b 1
    )
)
echo Using Conda environment '!SHOWCASE_ENV!'.

echo [2/3] Starting Showcase Server in a new window...
start "MMD Try Showcase Server" /D "%~dp0" cmd /k ""!PYTHON_EXE!" -m uvicorn mmd_try_showcase_server:app --host 0.0.0.0 --port !SHOWCASE_PORT!"

echo [3/3] Waiting for server to initialize (8 seconds)...
timeout /t 8 /nobreak > nul

echo Opening Browser...
start http://127.0.0.1:!SHOWCASE_PORT!/static/mmd_try_showcase.html

echo.
echo Done! The showcase server is running in the new command window.
echo You can safely close this launcher window.
pause
