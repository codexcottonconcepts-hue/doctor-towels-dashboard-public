"""Rebuild this application's local PostgreSQL from a backup folder, e.g. on a new computer.

Run it on the computer that will serve the dashboard, from that computer's copy
of this project (double-click restore-backup.cmd, or):

    py pgdb\\restore_backup.py                  newest folder in .local-postgres\\backups
    py pgdb\\restore_backup.py --backup FOLDER  a particular backup_local.py folder

Stops at the first problem. In order, it:
  1. checks every file in the backup against its manifest (size and SHA-256);
  2. moves an existing .local-postgres\\data aside, never deleting it -- a data
     folder copied from another computer while it ran is not a reliable database;
  3. creates a new PostgreSQL 18 cluster with the settings provision_isolated.py
     uses, the owner's password taken from the backup's admin.url;
  4. restores the database accounts (roles.sql) and then the data (dstt.dump),
     then applies this program version's database updates (apply_update.py);
  5. signs in with the application's own login (database.url) and prints row counts;
  6. puts the backup's users.db, session.key, database.url and admin.url in place,
     keeping any different existing copy beside it;
  7. starts the dashboard, first asking before it stops one already on the port.

Passwords are never printed or put on a command line.
"""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import psycopg2
from psycopg2.extensions import make_dsn, parse_dsn

import apply_update
from connect_local import protect_file

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local-postgres'
BIN = Path(r'C:\Program Files\PostgreSQL\18\bin')
OWNER = 'doctor_towels_owner'
TABLES = ('txn.sales_entry', 'tgt.target', 'lfo.monthly_sale', 'lfo.store', 'aux.dashboard_dataset')
PRIVATE = (('users.db', ROOT), ('session.key', ROOT), ('database.url', ROOT), ('admin.url', LOCAL))


def say(message):
    print(message, flush=True)


def newest_backup():
    folders = sorted(p for p in (LOCAL / 'backups').glob('*') if (p / 'manifest.json').is_file())
    if not folders:
        raise RuntimeError(r'No backup folder with a manifest.json was found in .local-postgres\backups.')
    return folders[-1]


def verify(folder):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    for name in ('dstt.dump', 'roles.sql', 'admin.url', 'database.url', 'users.db', 'session.key'):
        if name not in manifest.get('files', {}):
            raise RuntimeError(f'The backup manifest does not list {name}; this is not a complete backup.')
    for name, meta in manifest['files'].items():
        path = folder / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if digest != meta['sha256'] or path.stat().st_size != meta['bytes']:
            raise RuntimeError(f'{name} does not match the backup manifest. Copy the backup folder again.')
    return manifest


def port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def run(args, env=None, what='A PostgreSQL step'):
    result = subprocess.run([str(a) for a in args], env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise RuntimeError(f'{what} failed: ' + ' | '.join(detail))
    return result


def move_aside(path, stamp):
    target = path.with_name(f'{path.name}.before-restore-{stamp}')
    path.rename(target)
    return target


def stop_copied_cluster(data):
    """A data folder copied from another computer should not be running; stop it if it is."""
    if not (data / 'PG_VERSION').is_file():
        return
    status = subprocess.run([str(BIN / 'pg_ctl.exe'), 'status', '-D', str(data)], capture_output=True)
    if status.returncode == 0:
        say('  A database is running from the existing data folder; stopping it before it is set aside.')
        run([BIN / 'pg_ctl.exe', 'stop', '-D', data, '-m', 'fast', '-w', '-t', '60'],
            what='Stopping the existing database')


def restore(folder, port, stamp):
    owner = parse_dsn((folder / 'admin.url').read_text(encoding='utf-8-sig').strip())
    app = parse_dsn((folder / 'database.url').read_text(encoding='utf-8-sig').strip())
    if owner.get('user') != OWNER or app.get('dbname') != 'dstt':
        raise RuntimeError('The backup connection files are not this application\'s local database.')
    if port_open(port):
        raise RuntimeError(f'Port {port} is already in use on this computer. Stop that database first; nothing was changed.')

    if not LOCAL.exists():
        LOCAL.mkdir()
        protect_file(LOCAL)
    data = LOCAL / 'data'
    if data.exists():
        stop_copied_cluster(data)
        say(f'  Existing data folder moved aside to {move_aside(data, stamp).name} (not deleted).')

    # initdb reads the owner's password from a file that only this user can read,
    # and the file is emptied straight after, as provision_isolated.py does.
    pwfile = LOCAL / f'init-password.restore-{stamp}'
    with pwfile.open('x', encoding='utf-8'):
        pass
    protect_file(pwfile)
    try:
        pwfile.write_text(owner['password'], encoding='utf-8')
        run([BIN / 'initdb.exe', '-D', data, '-U', OWNER, '-A', 'scram-sha-256', '--pwfile', pwfile,
             '--encoding=UTF8', '--locale=C', '-c', 'listen_addresses=127.0.0.1', '-c', f'port={port}'],
            what='Creating the database cluster')
    finally:
        pwfile.write_text('', encoding='utf-8')
        pwfile.unlink()
    say(f'  New PostgreSQL 18 cluster created in {data}.')
    # The server outlives pg_ctl and would inherit captured output pipes, leaving
    # this script waiting for them forever; it logs to postgres.log instead.
    started = subprocess.run([str(BIN / 'pg_ctl.exe'), 'start', '-D', str(data), '-l', str(LOCAL / 'postgres.log'),
                              '-o', f'-p {port} -h 127.0.0.1', '-w', '-t', '60'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=90)
    if started.returncode:
        raise RuntimeError(r'Starting the database failed. See .local-postgres\postgres.log.')

    env = dict(os.environ, PGHOST='127.0.0.1', PGPORT=str(port), PGUSER=OWNER,
               PGPASSWORD=owner['password'], PGCONNECT_TIMEOUT='10')
    # roles.sql recreates every account with its password hash. Its first statement
    # for the owner fails harmlessly: initdb already created that account.
    roles = subprocess.run([str(BIN / 'psql.exe'), '-X', '-q', '--no-password', '-d', 'postgres',
                            '-v', 'ON_ERROR_STOP=0', '-f', str(folder / 'roles.sql')],
                           env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    unexpected = [line for line in roles.stderr.splitlines()
                  if 'ERROR' in line and f'role "{OWNER}" already exists' not in line]
    if roles.returncode or unexpected:
        raise RuntimeError('Restoring the database accounts failed: ' + ' | '.join(unexpected[-3:] or ['psql failed']))
    say('  Database accounts restored.')
    run([BIN / 'pg_restore.exe', '--create', '--exit-on-error', '--no-password', '-d', 'postgres',
         folder / 'dstt.dump'], env=env, what='Restoring the data')
    say('  Sales, targets and secondary data restored.')
    # A backup made before a program update lacks that update's database
    # changes; apply them now so the restored database matches this program.
    apply_update.apply(make_dsn(**dict(owner, host='127.0.0.1', port=str(port), dbname='dstt')), say=say)
    say('  Database updates for this program version applied.')

    # The application's own login must work exactly as the dashboard will use it.
    with closing(psycopg2.connect(**dict(app, port=str(port)))) as conn, conn.cursor() as cur:
        counts = {}
        for table in TABLES:
            cur.execute(f'select count(*) from {table}')
            counts[table] = cur.fetchone()[0]
    say('  Checked with the dashboard\'s own login: ' +
        ', '.join(f'{table} {count:,}' for table, count in counts.items()))
    return counts


def place_private_files(folder, stamp):
    for name, directory in PRIVATE:
        source, target = folder / name, directory / name
        if target.is_file() and target.read_bytes() == source.read_bytes():
            continue
        if target.exists():
            say(f'  Kept the previous {name} as {move_aside(target, stamp).name}.')
        shutil.copyfile(source, target)
        protect_file(target)
    say('  Login accounts, session key and connection settings are in place.')


def stop_running_dashboard(yes):
    """A dashboard started before the database existed has no data; offer to stop it.

    Returns True when the dashboard port is free for the restored dashboard.
    """
    port = int(os.environ.get('PORT', '8090'))
    if not port_open(port):
        return True
    say(f'  A dashboard is already running on port {port}. It started before this database '
        'existed, so it shows no data.')
    answer = 'yes' if yes else input('  Stop it and start the restored dashboard? [yes/no]: ').strip().lower()
    if answer != 'yes':
        return False
    listeners = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
         f'(Get-NetTCPConnection -State Listen -LocalPort {port}).OwningProcess | Sort-Object -Unique'],
        capture_output=True, text=True)
    for pid in {line.strip() for line in listeners.stdout.splitlines() if line.strip().isdigit()}:
        subprocess.run(['taskkill', '/PID', pid, '/F'], capture_output=True)
    for _ in range(40):
        if not port_open(port):
            return True
        time.sleep(0.5)
    raise RuntimeError(f'The old dashboard on port {port} did not stop. Close it, then run run-app.cmd.')


def main():
    parser = argparse.ArgumentParser(description='Rebuild the local database from a backup_local.py folder.')
    parser.add_argument('--backup', type=Path, help='backup folder (default: the newest one)')
    parser.add_argument('--port', type=int, default=5433, help='database port (the dashboard expects 5433)')
    parser.add_argument('--no-start', action='store_true', help='do not start the dashboard afterwards')
    parser.add_argument('--yes', action='store_true', help='stop a dashboard already on the port without asking')
    args = parser.parse_args()
    for tool in ('initdb.exe', 'pg_ctl.exe', 'psql.exe', 'pg_restore.exe'):
        if not (BIN / tool).is_file():
            raise RuntimeError(f'PostgreSQL 18 is not installed at {BIN}. Install it first '
                               '(the default options are fine), then run this again.')
    folder = (args.backup or newest_backup()).resolve()
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    say(f'Restoring from {folder}')
    manifest = verify(folder)
    say(f'  Backup verified: {len(manifest["files"])} files match its manifest '
        f'(made {manifest.get("created_local", "unknown time")}).')
    restore(folder, args.port, stamp)
    # The old dashboard may hold users.db open, so it stops before the files change.
    port_free = False if args.no_start else stop_running_dashboard(args.yes)
    place_private_files(folder, stamp)
    say('Database restored. Keep one computer for entering sales: entries made on two computers '
        'go into two separate databases.')
    if args.no_start:
        return 0
    if not port_free:
        say('The old dashboard was left running. Stop it, then run run-app.cmd.')
        return 0
    return subprocess.run([sys.executable, str(ROOT / 'pgdb' / 'start_local_app.py')], cwd=ROOT).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Database errors can carry connection details; show only the step's own message.
        print('Restore stopped: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__))
        raise SystemExit(1)
