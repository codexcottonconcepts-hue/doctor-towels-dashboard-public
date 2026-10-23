@echo off
setlocal
cd /d "%~dp0"
rem Applies the database updates the current program needs. Safe to run again.
rem This computer's Python if present, otherwise the standard Python launcher.
set "PYEXE=C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe"
if exist "%PYEXE%" (
    "%PYEXE%" pgdb\apply_update.py
) else (
    py -3 pgdb\apply_update.py
)
if errorlevel 1 (
    echo The database update did not finish. Review the message above.
)
pause
