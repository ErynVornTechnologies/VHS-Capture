@echo off
:: ============================================================
::  BORING STUFF #09 - VHS Capture - Installer (Windows 10/11)
::  Installs: Python 3.12, FFmpeg and a local .venv with the GUI
::  packages. Your global Python packages are never touched.
::  Erynvorn Technologies | https://erynvorntechnologies.com
:: ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title VHS Capture - Installer

echo.
echo ============================================================
echo   VHS CAPTURE - automatic installer
echo ============================================================
echo.

echo [1/4] Checking Python 3.12...
py -3.12 -c "import sys" >nul 2>&1
if %errorlevel% neq 0 (
    winget --version >nul 2>&1
    if errorlevel 1 (
        echo       [ERROR] Python 3.12 and winget are both missing.
        echo       Install Python 3.12 from python.org, then run this file again.
        pause & exit /b 1
    )
    echo       Installing Python 3.12 with winget...
    winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo       [!] Close this window and run INSTALL.bat again.
    pause & exit /b 0
)
py -3.12 --version

echo.
echo [2/4] Checking FFmpeg...
set "HAVE_FF=0"
if exist "C:\ffmpeg\bin\ffmpeg.exe" set "HAVE_FF=1"
ffmpeg -version >nul 2>&1 && set "HAVE_FF=1"
if "%HAVE_FF%"=="1" (
    echo       FFmpeg found.
) else (
    echo       Installing FFmpeg with winget...
    winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    echo       [!] FFmpeg is added to PATH for NEW windows only.
)

echo.
echo [3/4] Creating local environment (.venv) and installing packages...
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 ( echo       [ERROR] venv creation failed. & pause & exit /b 1 )
)
set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 ( echo       [ERROR] package install failed. & pause & exit /b 1 )

echo.
echo [4/4] Looking for capture devices...
"%PY%" capture.py --list

echo.
echo ============================================================
echo   INSTALLATION FINISHED
echo   Start the app:   run.bat
echo   No grabber listed above? Plug it in and run: .venv\Scripts\python.exe capture.py --list
echo.
echo   Boring Stuff by Erynvorn Technologies
echo   https://erynvorntechnologies.com
echo   https://github.com/ErynVornTechnologies
echo ============================================================
echo.
pause
endlocal
