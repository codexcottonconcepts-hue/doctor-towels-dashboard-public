"""Owner-only import of the pinned public geography reference; dry-run by default.

No database password is accepted on the command line or printed. Migration 25
must already exist for --apply. The application role cannot execute this import.
"""
import argparse
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from geo_reference import GeographyError, build_plan, import_reference, load_reference


def check_destination(where, *, hostname=None):
    """Allow only the dashboard server or an explicitly named local test DB."""
    host = where.get('host', '')
    database = where.get('dbname', '')
    if host not in ('127.0.0.1', 'localhost', '::1') or where.get('hostaddr', host) not in ('127.0.0.1', 'localhost', '::1'):
        raise GeographyError('Geography import requires an explicit loopback database address.')
    if database.endswith('_test') and len(database) > len('_test'):
        return
    hostname = hostname or socket.gethostname()
    if database != 'dstt' or str(where.get('port', '5432')) != '5433' or hostname.upper() != 'BACKUPSERVER':
        raise GeographyError('Production geography import is restricted to BACKUPSERVER / dstt / port 5433.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Append the verified reference in one owner transaction.')
    parser.add_argument('--dsn-file', type=Path, default=ROOT / '.local-postgres' / 'admin.url', help='Owner DSN file; its content is never printed.')
    parser.add_argument('--inventory-file', type=Path, help='Pure offline dry-run against a geography-only inventory JSON.')
    parser.add_argument('--actor', default='geography_reference_import', help='Audit actor, without credentials or personal data.')
    parser.add_argument('--output', type=Path, help='Optional JSON receipt path; use a private output directory.')
    args = parser.parse_args(argv)
    connection = None
    try:
        reference = load_reference()
        if args.inventory_file:
            if args.apply:
                raise GeographyError('--inventory-file is available only for dry runs.')
            inventory = json.loads(args.inventory_file.read_text(encoding='utf-8-sig'))
            plan = build_plan(reference, inventory)
            result = {'dry_run': True, 'offline': True, 'snapshot_id': reference['metadata']['snapshot_id'],
                      'summary': plan['summary'], 'existing_master_rows_unchanged': True}
        else:
            import psycopg2
            from psycopg2.extensions import parse_dsn
            dsn = args.dsn_file.read_text(encoding='utf-8-sig').strip()
            check_destination(parse_dsn(dsn))
            connection = psycopg2.connect(dsn)
            connection.set_session(isolation_level='REPEATABLE READ', readonly=not args.apply, autocommit=False)
            with connection.cursor() as cur:
                if args.apply:
                    cur.execute("select count(*),bool_and(pg_has_role(current_user,c.relowner,'MEMBER')) "
                                "from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                                "where n.nspname='master' and c.relname in ('state','district','city') and c.relkind='r'")
                    count, owns_all = cur.fetchone()
                    if count != 3 or not owns_all:
                        raise GeographyError('Only the owner of all geography masters may apply this reference.')
                result = import_reference(cur, reference, dry_run=not args.apply, actor=args.actor)
            if args.apply:
                connection.commit()
            else:
                connection.rollback()
        body = json.dumps({'ok': True, **result}, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            args.output.write_text(body, encoding='utf-8')
        print(body, end='')
        return 0
    except (GeographyError, OSError, ValueError) as exc:
        if connection is not None:
            connection.rollback()
        # Configuration values and database connection strings are never echoed.
        message = str(exc) if isinstance(exc, GeographyError) else 'Reference or local configuration could not be read.'
        print(json.dumps({'ok': False, 'error': message}), file=sys.stderr)
        return 1
    except Exception:
        if connection is not None:
            connection.rollback()
        print(json.dumps({'ok': False, 'error': 'Database import failed; its transaction was rolled back.'}), file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
