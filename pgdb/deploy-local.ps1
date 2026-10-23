<#
.SYNOPSIS
    Retired local deployment helper. Displays the current procedure only.
.DESCRIPTION
    This legacy entry point no longer provisions databases or changes services.
    The dashboard now uses its separate application PostgreSQL on port 5433.
    Legacy parameters are accepted so older commands receive this explanation.
#>
[CmdletBinding()]
param(
    [string] $Database = 'dstt',
    [string] $Excel = '',
    [switch] $SkipLoad
)

$guidePath = Join-Path $PSScriptRoot 'LOCAL_POSTGRES.md'
$launcherPath = Join-Path $PSScriptRoot 'start_local_app.py'
$connectionHelperPath = Join-Path $PSScriptRoot 'connect_local.py'

Write-Host 'This old deployment helper is retired. No changes were made.' -ForegroundColor Yellow
Write-Host ''
Write-Host 'This application now uses its separate local PostgreSQL at 127.0.0.1:5433.'
Write-Host 'Open the current setup and recovery guidance:'
Write-Host ('    ' + $guidePath)
Write-Host ''
Write-Host 'To start the configured dashboard manually, run the guarded launcher:'
Write-Host ('    pythonw.exe "' + $launcherPath + '"')
Write-Host 'The launcher preserves an application already listening on port 8090.'
Write-Host ''
Write-Host 'For an intentionally different existing PostgreSQL server, review:'
Write-Host ('    ' + $connectionHelperPath)
Write-Host 'That interactive helper is optional and was not started.'
Write-Host 'The legacy Database, Excel and SkipLoad arguments are not applied.'
exit 1
