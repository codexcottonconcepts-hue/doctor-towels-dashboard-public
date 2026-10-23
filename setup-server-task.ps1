<#
    Install the dashboard web app as a startup service on this machine.

    Run this ON your own server, in an ELEVATED PowerShell, from the
    folder the project was copied into:

        powershell -ExecutionPolicy Bypass -File "D:\doctor-towels-dashboard\setup-server-task.ps1"

    It installs the Python dependencies, registers a scheduled task that starts
    app.py at boot as SYSTEM, opens the port in the firewall, then waits for the
    app to answer and explains where to find the first-run account details.

    app.py rebuilds the page on a timer using PostgreSQL only.
    Restart the task after changing database configuration.

    Parameters:
      -Port         listen port. Default 8090, matching the router forward.
      -Bind         0.0.0.0 serves the LAN and the forwarded public IP.
      -PublishDir   where a copy of the rendered index.html is also written.
                    Empty by default, and that is the safe default: the app
                    serves the page itself behind a login, so a static copy in
                    the IIS web root would be the same financials with no login
                    at all -- on a forwarded port, the one thing worth not
                    having. Pass -PublishDir 'D:\DST&TT' to keep the old static
                    page alive alongside it. Never point it at this folder: it
                    holds application source and private configuration.
      -RefreshSeconds  how stale the data may get before a re-read.
#>

param(
    [int]    $Port           = 8090,
    [string] $Bind           = '0.0.0.0',
    [string] $PublishDir     = '',
    [int]    $RefreshSeconds = 900,
    # PostgreSQL connection string. If omitted, existing configuration is kept.
    # PostgreSQL is required; there is no Google Sheets fallback. It is written to
    # database.url next to this script, not into run-app.cmd, so the one file
    # holding a password can be locked down on its own.
    [string] $DatabaseUrl    = ''
)

$TaskName = 'DashboardApp'
$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- guards ---------------------------------------------------------------

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Run this in an elevated PowerShell (right-click -> Run as administrator)." -ForegroundColor Red
    exit 1
}
foreach ($f in @('app.py', 'auth.py', 'refresh.py', 'template.html', 'requirements.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Here $f))) {
        Write-Host "$f is missing from $Here - copy the whole project folder, not just this script." -ForegroundColor Red
        exit 1
    }
}

# --- never publish into the folder holding the source ---------------------
# Whatever web server is pointed at the publish folder serves everything in it.
# If that is this folder, it serves session.key -- which signs the login cookie,
# so handing it out lets anyone forge a session and skip the login altogether --
# along with users.db and private database configuration. A subfolder keeps
# the rendered page reachable and the source not.
if ($PublishDir) {
    $pubFull  = [IO.Path]::GetFullPath($PublishDir.TrimEnd('\'))
    $hereFull = [IO.Path]::GetFullPath($Here.TrimEnd('\'))
    if ($pubFull -eq $hereFull) {
        Write-Host "-PublishDir is this folder. Refusing." -ForegroundColor Red
        Write-Host "Anything serving it would also serve session.key (which signs the login" -ForegroundColor Red
        Write-Host "cookie), users.db, and refresh.py. Use a subfolder instead:" -ForegroundColor Red
        Write-Host "    -PublishDir '$hereFull\public'" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path -LiteralPath $pubFull)) {
        New-Item -ItemType Directory -Path $pubFull -Force | Out-Null
        Write-Host "Created publish folder $pubFull" -ForegroundColor Cyan
    }
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Host "Python is not on PATH on this machine. Install it, tick 'Add to PATH', then re-run." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $python" -ForegroundColor Cyan

# --- is the port free? ----------------------------------------------------

$busy = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($busy) {
    $who = ($busy | ForEach-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName } |
            Sort-Object -Unique) -join ', '
    Write-Host "Port $Port is already in use by: $who. Nothing changed." -ForegroundColor Red
    if ($who -match 'w3wp|iisexpress|System') {
        Write-Host ""
        Write-Host "That looks like the IIS 'Dashboard' site. This app replaces it -- it serves" -ForegroundColor Yellow
        Write-Host "the same page but behind a login, which the static IIS site cannot do. Stop" -ForegroundColor Yellow
        Write-Host "it and re-run:" -ForegroundColor Yellow
        Write-Host "    Import-Module WebAdministration; Stop-Website -Name 'Dashboard'" -ForegroundColor Yellow
    }
    exit 1
}

# --- dependencies ---------------------------------------------------------

Write-Host "Installing Python dependencies ..." -ForegroundColor Cyan
& $python -m pip install --quiet --disable-pip-version-check -r (Join-Path $Here 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed. Fix that first - the app will not start without it." -ForegroundColor Red
    exit 1
}

# --- launcher -------------------------------------------------------------
# Task Scheduler has no clean way to carry environment variables, so the
# settings live in a wrapper that the task runs instead.

$urlFile = Join-Path $Here 'database.url'

if ($DatabaseUrl) {
    Set-Content -LiteralPath $urlFile -Value $DatabaseUrl -Encoding ascii -NoNewline
    # The only file here that holds a password. Take it off the inherited ACL
    # and leave SYSTEM (the task runs as SYSTEM) and Administrators on it.
    $acl = Get-Acl -LiteralPath $urlFile
    $acl.SetAccessRuleProtection($true, $false)
    $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
    foreach ($who in 'NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators') {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $who, 'FullControl', 'Allow')))
    }
    Set-Acl -LiteralPath $urlFile -AclObject $acl
    Write-Host "Wrote $urlFile (SYSTEM and Administrators only)" -ForegroundColor Cyan
    Write-Host "  the dashboard will read PostgreSQL" -ForegroundColor Cyan
}
elseif (Test-Path -LiteralPath $urlFile) {
    Write-Host "Keeping the existing $urlFile - the dashboard reads PostgreSQL." -ForegroundColor Cyan
    Write-Host "  PostgreSQL is required; keep this private connection file." -ForegroundColor DarkGray
}
else {
    throw 'PostgreSQL is required. Supply -DatabaseUrl or restore the private database.url file.'
}

$cmd = Join-Path $Here 'run-app.cmd'
@(
    '@echo off',
    'setlocal',
    'rem Let Windows PowerShell initialize its own module paths.',
    'set "PSModulePath="',
    "cd /d ""$Here""",
    "set PORT=$Port",
    "set BIND=$Bind",
    "set PUBLISH_DIR=$PublishDir",
    "set REFRESH_SECONDS=$RefreshSeconds",
    'rem Start the separate application database if it has been initialized.',
    "if exist ""$Here\.local-postgres\data\PG_VERSION"" (",
    "    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""$Here\pgdb\local_postgres.ps1"" -Action Start >> ""$Here\app.log"" 2>&1",
    '    if errorlevel 1 exit /b 1',
    ')',
    'rem Python reads database.url directly; credentials never pass through cmd.exe.',
    'rem DATABASE_URL, when configured in the environment, takes precedence.',
    'rem -u keeps stdout unbuffered. Without it the diagnostics sit in a',
    'rem buffer and app.log stays empty for hours while the app runs fine.',
    """$python"" -u app.py >> ""$Here\app.log"" 2>&1"
) | Set-Content -LiteralPath $cmd -Encoding ascii
Write-Host "Wrote $cmd (output goes to app.log)" -ForegroundColor Cyan

# --- scheduled task -------------------------------------------------------

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Task '$TaskName' exists - replacing it." -ForegroundColor Cyan
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
$action   = New-ScheduledTaskAction -Execute $cmd
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -User 'SYSTEM' | Out-Null
Write-Host "Registered scheduled task '$TaskName' (at startup, as SYSTEM)." -ForegroundColor Cyan

# --- firewall -------------------------------------------------------------

if (-not (Get-NetFirewallRule -DisplayName "Dashboard $Port" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Dashboard $Port" -Direction Inbound -Protocol TCP `
        -LocalPort $Port -Action Allow -Profile Any | Out-Null
    Write-Host "Firewall: opened TCP $Port" -ForegroundColor Cyan
} else {
    Write-Host "Firewall: rule for $Port already present" -ForegroundColor Cyan
}

# --- start and wait -------------------------------------------------------

Start-ScheduledTask -TaskName $TaskName
Write-Host "`nStarting - the first build reads the whole workbook, give it a moment ..." -ForegroundColor Cyan

$up = $false
foreach ($attempt in 1..40) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 5
        if (($r.Content | ConvertFrom-Json).ok) { $up = $true; break }
    } catch { }
}

Write-Host ""
if (-not $up) {
    Write-Host "No healthy answer on /healthz after two minutes." -ForegroundColor Yellow
    Write-Host "Check the tail of $Here\app.log for the reason." -ForegroundColor Yellow
    exit 1
}

Write-Host "OK - the dashboard is up." -ForegroundColor Green
Write-Host "     local   http://localhost:$Port/" -ForegroundColor Green
Write-Host "     LAN     use this server's hostname or IP on port $Port" -ForegroundColor Green
Write-Host "     users   /admin/users" -ForegroundColor Green

# --- first-run administrator ---------------------------------------------

$log = Join-Path $Here 'app.log'
$defaultLive = $false
if (Test-Path -LiteralPath $log) {
    $defaultLive = [bool](Select-String -LiteralPath $log -Pattern 'still has the default password' -Quiet)
    if (Select-String -LiteralPath $log -Pattern 'FIRST RUN' -Quiet) {
        Write-Host ""
        Write-Host "The log contains first-run account details. View app.log privately on this server." -ForegroundColor Green
        Write-Host "Use its generated password, or the DASH_ADMIN_PASSWORD you configured." -ForegroundColor Green
        Write-Host "If needed, reset the intended account privately with: python auth.py passwd USERNAME" -ForegroundColor Green
    }
}

if ($defaultLive) {
    Write-Host ""
    Write-Host ("!" * 68) -ForegroundColor Yellow
    Write-Host "  The admin account still has the default password." -ForegroundColor Yellow
    Write-Host "  Change it at /admin/users BEFORE you forward port $Port on the router." -ForegroundColor Yellow
    Write-Host "  'admin' / 'Admin@123' is the first pair any scanner tries, and this" -ForegroundColor Yellow
    Write-Host "  dashboard carries the company's turnover." -ForegroundColor Yellow
    Write-Host ("!" * 68) -ForegroundColor Yellow
}
