@echo off
setlocal
cd /d "%~dp0"

set "OLLAMA_EXE=C:\Program Files\APP\WORK\Ollama\ollama app.exe"

set "NO_PROXY=127.0.0.1,localhost,::1"
set "no_proxy=%NO_PROXY%"
set "PYTHONUTF8=1"

echo [1/4] starting docker containers ...
docker compose -f docker\docker-compose.yaml up -d
if errorlevel 1 (
    echo       ERROR: docker not available. Is Docker Desktop running?
    pause
    exit /b 1
)

echo [2/4] checking ollama ...
curl -s -o nul --noproxy "*" http://localhost:11434/api/tags
if errorlevel 1 (
    echo       ollama not responding - trying to start it ...
    if exist "%OLLAMA_EXE%" (
        start "" "%OLLAMA_EXE%"
        timeout /t 8 >nul
    ) else (
        echo       WARNING: not found at "%OLLAMA_EXE%", start it manually.
    )
)

echo [3/4] LAN address of this machine:
ipconfig | findstr /C:"IPv4"

echo.
echo [4/4] starting web server on 0.0.0.0:8000
echo       others open:  http://[LAN-IP]:8000
echo       keep this window open; press Ctrl+C to stop
echo.

uv run uvicorn main:app --host 0.0.0.0 --port 8000

endlocal
