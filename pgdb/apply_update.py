"""Apply the database updates a new program version needs, on this computer.

    py pgdb\\apply_update.py            (or double-click apply-update.cmd)

Runs each repeatable migration in UPDATES against this project's own
PostgreSQL cluster as its owner, using .local-postgres/admin.url. Every file
carries its own transaction and is safe to re-run; nothing is deleted. It never
prints a password and never touches another PostgreSQL service on the computer
(such as a Ticket system on port 5432): it connects only to the address saved
in admin.url.

The dashboard keeps working while this runs. Restart the dashboard afterwards
so the new program is loaded.
"""
import sys
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extensions import parse_dsn
except ImportError:
    sys.exit('psycopg2 is not installed. Run: py -m pip install -r requirements.txt')

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / '.local-postgres' / 'admin.url'
SQL = ROOT / 'pgdb' / 'sql'
UPDATES = ('15_target_planner.sql', '16_shipping_details.sql', '17_product_invoice.sql', '18_invoice_colour.sql', '22_customer_details.sql', '23_customer_contact.sql')


def apply(dsn, say=print):
    """Apply UPDATES as the owner through dsn. Raises RuntimeError on a failure."""
    conn = psycopg2.connect(dsn)
    conn.autocommit = True               # each file has its own begin/commit
    try:
        for name in UPDATES:
            body = (SQL / name).read_text(encoding='utf-8')
            try:
                with conn.cursor() as cur:
                    cur.execute(body)
            except psycopg2.Error as exc:
                raise RuntimeError(f'Database update {name} failed: {str(exc).strip()} Nothing in that file was applied.') from None
            say(f'  ok  {name}')
        with conn.cursor() as cur:
            cur.execute("select pg_get_constraintdef(oid) from pg_constraint where conname='distributor_channel_check'")
            row = cur.fetchone()
        if not row or 'Saloon' not in row[0]:
            raise RuntimeError('The database update ran but its channel check was not found. Ask for help before using the planner.')
    finally:
        conn.close()


def main():
    if not ADMIN.is_file():
        sys.exit(f'{ADMIN} was not found. Run this on the computer that hosts the dashboard database.')
    dsn = ADMIN.read_text(encoding='utf-8-sig').strip()
    where = parse_dsn(dsn)
    print(f"Database {where.get('dbname', '?')} on {where.get('host', '?')}:{where.get('port', '5432')}")
    try:
        apply(dsn)
    except RuntimeError as exc:
        sys.exit(str(exc))
    print('Database is up to date. If the dashboard was started before this update, restart it.')


if __name__ == '__main__':
    main()
