<#
.SYNOPSIS
    Start, stop, or inspect this application's separate local PostgreSQL 18.
.DESCRIPTION
    Operates only on .local-postgres\data beside this project's pgdb folder.
    It does not create a database, change credentials, install a service, or
    manage the existing PostgreSQL installation's service on port 5432.
#>
[CmdletBinding()]
param(
    [ValidateSet('Start', 'Status', 'Stop')]
    [string] $Action = 'Status'
)

$ErrorActionPreference = 'Stop'
$ProjectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$ClusterDirectory = [IO.Path]::GetFullPath((Join-Path $ProjectDirectory '.local-postgres'))
$DataDirectory = [IO.Path]::GetFullPath((Join-Path $ClusterDirectory 'data'))
$PgBinDirectory = 'C:\Program Files\PostgreSQL\18\bin'
$PgControl = Join-Path $PgBinDirectory 'pg_ctl.exe'
$PgServer = Join-Path $PgBinDirectory 'postgres.exe'
$LogFile = Join-Path $ClusterDirectory 'postgres.log'
$LocalPort = 5433
$LocalAddress = '127.0.0.1'

function Assert-ClusterPath {
    $expectedPath = Join-Path $ProjectDirectory '.local-postgres\data'
    if (-not $DataDirectory.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The local database directory failed its project path check.'
    }
    foreach ($directory in @($ClusterDirectory, $DataDirectory)) {
        $item = Get-Item -LiteralPath $directory -Force -ErrorAction SilentlyContinue
        if (-not $item -or -not $item.PSIsContainer) {
            throw 'The separate local database is not initialized. Complete local PostgreSQL setup first.'
        }
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Refusing to manage a database through a symbolic link or junction.'
        }
    }
    $versionPath = Join-Path $DataDirectory 'PG_VERSION'
    if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf)) {
        throw 'PG_VERSION is missing. This directory is not an initialized database cluster.'
    }
    if ((Get-Content -LiteralPath $versionPath -Raw).Trim() -ne '18') {
        throw 'This launcher manages PostgreSQL 18 clusters only.'
    }
    if (-not (Test-Path -LiteralPath $PgControl -PathType Leaf) -or
        -not (Test-Path -LiteralPath $PgServer -PathType Leaf)) {
        throw 'The PostgreSQL 18 binaries are missing from their installed location.'
    }
}

function Assert-RunningCluster {
    # PostgreSQL writes its own identity and listener settings into this file.
    # Read only these non-secret values before declaring the endpoint ready.
    $pidFile = Join-Path $DataDirectory 'postmaster.pid'
    $lines = @(Get-Content -LiteralPath $pidFile)
    if ($lines.Count -lt 6) { throw 'The local PostgreSQL process identity is incomplete.' }
    $recordedDirectory = [IO.Path]::GetFullPath($lines[1].Trim()).TrimEnd('\')
    if (-not $recordedDirectory.Equals($DataDirectory, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The running PostgreSQL process belongs to a different database directory.'
    }
    $serverProcessId = 0
    if (-not [int]::TryParse($lines[0].Trim(), [ref]$serverProcessId)) {
        throw 'The local PostgreSQL process identity is invalid.'
    }
    $serverProcess = Get-Process -Id $serverProcessId -ErrorAction SilentlyContinue
    if (-not $serverProcess -or -not $serverProcess.Path -or
        -not $serverProcess.Path.Equals($PgServer, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The recorded process is not the expected PostgreSQL 18 executable.'
    }
    if ($lines[3].Trim() -ne "$LocalPort" -or $lines[5].Trim() -ne $LocalAddress) {
        throw 'The local database is running with unexpected network settings. Review its configuration before proceeding.'
    }
}

try {
    Assert-ClusterPath
    $statusOutput = & $PgControl status -D $DataDirectory 2>&1
    $statusCode = $LASTEXITCODE
    if ($statusCode -notin @(0, 3)) {
        throw "Unable to inspect this application's PostgreSQL process (pg_ctl exit $statusCode)."
    }
    $running = $statusCode -eq 0
    if ($running) { Assert-RunningCluster }

    switch ($Action) {
        'Status' {
            if ($running) {
                Write-Output "Application PostgreSQL is running at ${LocalAddress}:$LocalPort."
                exit 0
            }
            Write-Output 'Application PostgreSQL is stopped.'
            exit 3
        }
        'Start' {
            if ($running) {
                Write-Output "Application PostgreSQL is already running at ${LocalAddress}:$LocalPort."
                exit 0
            }
            $occupied = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue
            if ($occupied) {
                throw "Port $LocalPort is occupied by another process. No service was stopped or changed."
            }
            & $PgControl start -D $DataDirectory -l $LogFile -o "-p $LocalPort -h $LocalAddress" -w -t 60
            if ($LASTEXITCODE -ne 0) {
                throw "Application PostgreSQL could not start. Review $LogFile locally."
            }
            Assert-RunningCluster
            Write-Output "Application PostgreSQL is ready at ${LocalAddress}:$LocalPort."
        }
        'Stop' {
            if (-not $running) {
                Write-Output 'Application PostgreSQL is already stopped.'
                exit 0
            }
            & $PgControl stop -D $DataDirectory -m fast -w -t 60
            if ($LASTEXITCODE -ne 0) { throw 'Application PostgreSQL did not stop cleanly.' }
            Write-Output 'Application PostgreSQL stopped cleanly.'
        }
    }
    exit 0
}
catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
