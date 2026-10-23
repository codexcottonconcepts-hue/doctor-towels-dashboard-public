@echo off
setlocal
cd /d "%~dp0"
rem The launcher preserves an existing app and separates database/app logs.
rem Python reads database.url directly; credentials never pass through cmd.exe.
rem This computer's Python if present, otherwise the standard Python launcher,
rem so the same file works on another computer (for example after restore-backup.cmd).
set "PYEXE=C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe"
if exist "%PYEXE%" (
    "%PYEXE%" "%~dp0pgdb\start_local_app.py"
) else (
    py -3 "%~dp0pgdb\start_local_app.py"
)
exit /b %errorlevel%
