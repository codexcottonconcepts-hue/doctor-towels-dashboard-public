@echo off
setlocal
cd /d "%~dp0"
rem This computer's Python if present, otherwise the standard Python launcher.
set "PYEXE=C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe"
if exist "%PYEXE%" (
    "%PYEXE%" pgdb\backup_local.py
) else (
    py -3 pgdb\backup_local.py
)
if errorlevel 1 (
    echo Backup did not finish. Review the message above.
)
pause
