"""Import a product catalog without changing any sales entries.

The default asset contains two fictional demonstration products and prices.
Use --catalog with your own private file for a real catalog.

Dry run is offline. A real import requires an explicit owner/ETL DSN file;
the connection string and individual catalog values are never printed.
"""
import argparse
from contextlib import closing
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invoice_items import InvoiceError, import_catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT / 'catalog' / 'products.example.json')
    parser.add_argument('--dsn-file', type=Path, help='Explicit owner or catalog-importer connection file')
    parser.add_argument('--actor', default='product_catalog_import')
    parser.add_argument('--dry-run', action='store_true', help='Validate all products without connecting or writing')
    args = parser.parse_args(argv)
    try:
        count = import_catalog(None, args.catalog, dry_run=True)
    except (OSError, ValueError, TypeError) as exc:
        print('Catalog validation failed: ' + str(exc), file=sys.stderr)
        return 1
    if args.dry_run:
        print(f'Validated {count} products. No database connection or writes performed.')
        return 0
    if args.dsn_file is None:
        parser.error('--dsn-file is required unless --dry-run is used')
    if not args.actor.strip() or len(args.actor) > 200:
        parser.error('--actor must be between 1 and 200 characters')
    try:
        import psycopg2
        dsn = args.dsn_file.read_text(encoding='utf-8-sig').strip()
        if not dsn:
            raise ValueError('Empty connection file')
        with closing(psycopg2.connect(dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute("select set_config('app.username', %s, true)", (args.actor,))
            import_catalog(cur, args.catalog)
    except (ImportError, OSError, ValueError):
        print('Catalog import could not start. Check the catalog, dependency and explicit connection file.', file=sys.stderr)
        return 1
    except Exception as exc:
        code = getattr(exc, 'pgcode', None) or 'connection error'
        print(f'Catalog import failed; its transaction was rolled back ({code}).', file=sys.stderr)
        return 1
    print(f'Imported or retained {count} catalog products. Sales and saved invoice snapshots were unchanged.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
