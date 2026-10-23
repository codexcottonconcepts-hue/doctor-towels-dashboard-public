"""Provision a separate SCRAM-authenticated local cluster for this application.

Never connects to the existing PostgreSQL service. Existing data/config files
cause setup to stop instead of overwriting credentials or databases.
"""
from contextlib import closing
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

import psycopg2
from psycopg2 import sql

from connect_local import protect_file

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local-postgres"
BIN = Path(r"C:\Program Files\PostgreSQL\18\bin")
PORT = 5433


def private_text(path, value):
    with path.open('x', encoding='utf-8'):
        pass
    protect_file(path)
    path.write_text(value, encoding='utf-8')


def main():
    if (LOCAL / 'data').exists() or (LOCAL / 'admin.url').exists() or (ROOT / 'database.url').exists():
        raise RuntimeError('An application database or connection already exists. It was preserved; review its status instead of reprovisioning.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', PORT)) == 0:
            raise RuntimeError('Port 5433 is already in use. No changes made.')
    LOCAL.mkdir(exist_ok=True)
    protect_file(LOCAL)
    password = secrets.token_hex(32)
    owner_uri = f'postgresql://doctor_towels_owner:{password}@127.0.0.1:{PORT}/dstt?connect_timeout=5'
    private_text(LOCAL / 'admin.url', owner_uri)
    private_text(LOCAL / 'init-password', password)
    subprocess.run([str(BIN/'initdb.exe'), '-D', str(LOCAL/'data'), '-U', 'doctor_towels_owner',
                    '-A', 'scram-sha-256', '--pwfile', str(LOCAL/'init-password'),
                    '--encoding=UTF8', '--locale=C', '-c', 'listen_addresses=127.0.0.1',
                    '-c', f'port={PORT}'], check=True)
    # Clear the initialization-only copy. Recovery credentials remain in the
    # protected admin.url, and neither value is printed or put on a command line.
    (LOCAL/'init-password').write_text('', encoding='utf-8')
    subprocess.run([str(BIN/'pg_ctl.exe'), '-D',str(LOCAL/'data'), '-l',str(LOCAL/'postgres.log'),
                    '-w','-t','60','start'],check=True)
    with closing(psycopg2.connect(owner_uri.replace('/dstt?', '/postgres?'))) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute('create database dstt')
    env = dict(os.environ, DATABASE_URL=owner_uri, PYTHONIOENCODING='utf-8')
    subprocess.run([sys.executable,str(ROOT/'pgdb'/'deploy.py'),'--schema-only'],env=env,cwd=ROOT,check=True)
    app_password = secrets.token_hex(32)
    with closing(psycopg2.connect(owner_uri)) as conn, conn:
        with conn.cursor() as cur:
            cur.execute("create role doctor_towels_app login password %s nosuperuser nocreatedb nocreaterole noreplication inherit in role dstt_app",(app_password,))
            cur.execute('grant connect on database dstt to doctor_towels_app')
    private_text(LOCAL/'app.url.pending', f'postgresql://doctor_towels_app:{app_password}@127.0.0.1:{PORT}/dstt?connect_timeout=5')
    print('Separate local PostgreSQL is ready on 127.0.0.1:5433. The live app connection has not been switched yet.')


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        # Database errors can contain configuration details; do not dump DSNs.
        print('Setup stopped: '+(str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__))
        raise SystemExit(1)
