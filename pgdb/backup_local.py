"""Back up the dedicated local database and login files without printing secrets."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from psycopg2.extensions import parse_dsn
from connect_local import protect_file

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local-postgres'
BIN = Path(r'C:\Program Files\PostgreSQL\18\bin')


def main():
    for name in ('users.db', 'session.key', 'database.url'):
        if not (ROOT / name).is_file():
            raise RuntimeError('Required application recovery file is missing: ' + name)
    connection = parse_dsn((LOCAL / 'admin.url').read_text(encoding='utf-8-sig').strip())
    if (connection.get('host'), connection.get('port'), connection.get('dbname')) != ('127.0.0.1', '5433', 'dstt'):
        raise RuntimeError('Recovery configuration does not identify the dedicated local database.')
    backups = LOCAL / 'backups'
    if not backups.exists():
        backups.mkdir()
        protect_file(backups)
    stamp = datetime.now().strftime('%Y-%m-%d-%H%M%S-%f')
    folder = backups / stamp
    folder.mkdir()  # Never overwrite an earlier backup.
    protect_file(folder)
    env = dict(os.environ, PGHOST=connection['host'], PGPORT=connection['port'],
               PGDATABASE=connection['dbname'], PGUSER=connection['user'],
               PGPASSWORD=connection['password'], PGCONNECT_TIMEOUT='10')
    dump = folder / 'dstt.dump'
    result = subprocess.run([str(BIN / 'pg_dump.exe'), '--no-password', '--format=custom',
                             '--file', str(dump)], env=env, capture_output=True)
    if result.returncode:
        raise RuntimeError('Database backup failed. The incomplete backup folder was retained for inspection.')
    listing = subprocess.run([str(BIN / 'pg_restore.exe'), '--list', str(dump)],
                             capture_output=True, text=True)
    if listing.returncode or not all(table in listing.stdout for table in
                                     ('TABLE DATA txn sales_entry', 'TABLE DATA tgt target_plan',
                                      'TABLE DATA tgt target ')):
        raise RuntimeError('Backup archive verification failed. Inspect the backup before relying on it.')
    # pg_dump excludes cluster roles, membership and SCRAM password hashes.
    # Keep their separate recovery script within this same restricted folder.
    roles = subprocess.run([str(BIN / 'pg_dumpall.exe'), '--roles-only', '--no-password',
                            '--file', str(folder / 'roles.sql')], env=env, capture_output=True)
    if roles.returncode:
        raise RuntimeError('Database account backup failed. The incomplete backup folder was retained for inspection.')
    source = ROOT / 'users.db'
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original:
        with sqlite3.connect(folder / 'users.db') as copy:
            original.backup(copy)
            if copy.execute('pragma quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Login database backup integrity check failed.')
    for name in ('session.key', 'database.url'):
        shutil.copyfile(ROOT / name, folder / name)
    shutil.copyfile(LOCAL / 'admin.url', folder / 'admin.url')
    manifest = {'created_local': datetime.now().isoformat(timespec='seconds'),
                'database': 'dstt', 'archive_contents_verified': True,
                'restore_tested': False,
                'note': 'Copy this entire restricted folder to separate private backup storage.',
                'files': {}}
    for path in folder.iterdir():
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        manifest['files'][path.name] = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Backup created and archive contents verified: ' + str(folder))
    print('Includes sales, targets, database accounts, login accounts and private recovery files. Keep the entire folder private.')
    print('Copy it to separate private storage; this same-computer copy does not protect against disk failure.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Backup stopped: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__))
        raise SystemExit(1)
