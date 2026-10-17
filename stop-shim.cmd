@echo off
setlocal
rem Saves every dirty idle slot to SSD, then stops the shim. Run this BEFORE stop-llama.cmd so nothing is lost.
set "HERE=%~dp0"
if not defined SHIM_ENV set "SHIM_ENV=%HERE%.env"
python "%HERE%ssd_cache_shim.py" --env "%SHIM_ENV%" --flush
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /b "LISTEN_PORT=" "%SHIM_ENV%"`) do set "PORT=%%b"
if not defined PORT set "PORT=8090"
for /f "tokens=5" %%p in ('netstat -ano -p TCP ^| findstr /R /C:":%PORT% .*LISTENING"') do taskkill /PID %%p /F >nul 2>&1
echo shim stopped.
