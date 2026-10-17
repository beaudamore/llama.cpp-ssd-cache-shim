@echo off
setlocal
title ssd-cache-shim
rem Runs the shim in this window. Config comes from .env next to the script (or set SHIM_ENV to another file).
rem llama-server must already be up with --slot-save-path <SLOT_SAVE_DIR>; the shim waits for it.
set "HERE=%~dp0"
if not defined SHIM_ENV set "SHIM_ENV=%HERE%.env"
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /b "SLOT_SAVE_DIR=" "%SHIM_ENV%"`) do set "SLOT_DIR=%%b"
if defined SLOT_DIR if not exist "%SLOT_DIR%" mkdir "%SLOT_DIR%"
python "%HERE%ssd_cache_shim.py" --env "%SHIM_ENV%"
