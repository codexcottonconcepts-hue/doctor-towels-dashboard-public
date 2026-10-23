@echo off
setlocal
cd /d "%~dp0"
rem Rebuilds the local database from the newest folder in .local-postgres\backups
rem and starts the dashboard. For a new computer; see pgdb\LOCAL_POSTGRES.md.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 pgdb\restore_backup.py %*
) else (
    python pgdb\restore_backup.py %*
)
if errorlevel 1 echo Restore did not finish. Review the message above.
pause
