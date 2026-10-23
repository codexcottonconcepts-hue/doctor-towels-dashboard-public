"""Start the local database and dashboard without sharing their log handles.

Safe to call repeatedly from run-app.cmd or a Windows sign-in shortcut. An
existing listener is preserved. Each new launch gets separate startup and app
logs, so a background process retaining an older handle cannot block a restart.
"""
from datetime import datetime
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def _port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def _start(env, port, log_directory):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + str(os.getpid())
    startup_log = log_directory / f'startup-{stamp}.log'
    app_log = log_directory / f'dashboard-{stamp}.log'
    quiet = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if (ROOT / '.local-postgres' / 'data' / 'PG_VERSION').is_file():
        # Close the PostgreSQL launch handle before creating the app log. Never
        # give pg_ctl/postgres the app's output handle, even indirectly via cmd.
        try:
            with startup_log.open('a', encoding='utf-8') as log:
                started = subprocess.run(
                    ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                     '-File', str(ROOT / 'pgdb' / 'local_postgres.ps1'), '-Action', 'Start'],
                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=quiet, close_fds=True, timeout=75,
                )
        except (OSError, subprocess.TimeoutExpired):
            print(f'Local database startup did not finish. Review {startup_log} locally.')
            return 1
        if started.returncode:
            print(f'Local database startup failed. Review {startup_log} locally.')
            return 1
    # Another launcher could have reached the port during database startup.
    if _port_open(port):
        print(f'Port {port} is already serving; the running process was preserved.')
        return 0
    try:
        with app_log.open('a', encoding='utf-8') as log:
            process = subprocess.Popen(
                [sys.executable, '-u', str(ROOT / 'app.py')], cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=quiet, close_fds=True,
            )
    except OSError:
        print(f'Dashboard could not start. Review {app_log} locally.')
        return 1
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print(f'Dashboard exited during startup. Review {app_log} locally.')
            return 1
        if _port_open(port):
            print(f'Dashboard is ready at http://127.0.0.1:{port}/. Log: {app_log}')
            return 0
        time.sleep(0.25)
    print(f'Dashboard is still starting. Review {app_log} locally; its process was preserved.')
    return 1


def main():
    try:
        port = int(os.environ.get('PORT', '8090'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        print('PORT must be a number from 1 to 65535.')
        return 1
    # Do not open any logs or start PostgreSQL when the website is already up.
    if _port_open(port):
        print(f'Port {port} is already serving; the running process was preserved.')
        return 0
    env = {k: v for k, v in os.environ.items() if k.lower() != 'psmodulepath'}
    env.setdefault('PORT', str(port))
    env.setdefault('BIND', '0.0.0.0')
    env.setdefault('PUBLISH_DIR', str(ROOT / 'public'))
    env.setdefault('REFRESH_SECONDS', '120')
    log_directory = ROOT / 'logs'
    log_directory.mkdir(exist_ok=True)
    # Windows byte-range locking prevents two shortcuts launching duplicate
    # app processes before either has had time to bind its port.
    import msvcrt
    with (log_directory / 'launcher.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print('Dashboard startup is already in progress.')
            return 0
        try:
            return _start(env, port, log_directory)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == '__main__':
    raise SystemExit(main())
