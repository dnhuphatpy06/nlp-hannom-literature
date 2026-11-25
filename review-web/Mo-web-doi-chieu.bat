@echo off
setlocal
where python.exe >nul 2>&1
if errorlevel 1 (
    py -3 "%~dp0..\review_local.py" %*
) else (
    python "%~dp0..\review_local.py" %*
)
if errorlevel 1 pause
