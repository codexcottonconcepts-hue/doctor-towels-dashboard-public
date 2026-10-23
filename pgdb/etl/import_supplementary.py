"""Explicit, additive import of the retained local supplementary snapshot.

Run the 09_supplementary_data.sql migration as the owner first, then:
  python pgdb/etl/import_supplementary.py snapshot.json

Use an owner/ETL connection via DATABASE_URL for the import (the application's
normal database role intentionally has read access only). Identical repeats do nothing;
different existing payloads abort the entire import instead of overwriting data.
No Google endpoint is contacted. The source date is the snapshot's recorded date,
not proof that the original workbook was current on that date.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys

from psycopg2.extras import Json

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pgdata

KEYS = ('hist', 'histLabels', 'lfo2', 'terr', 'cat', 'pipeline', 'mtd')


def read_source(path):
    raw = Path(path).read_bytes()
    archive = json.loads(raw.decode('utf-8-sig')).get('archive')
    if not isinstance(archive, dict) or any(key not in archive for key in KEYS):
        raise ValueError('Snapshot must contain all seven supplementary datasets.')
    for key in KEYS:
        expected = dict if key == 'mtd' else list
        if not isinstance(archive[key], expected):
            raise ValueError(f'Invalid supplementary dataset: {key}')
    try:
        recorded = datetime.datetime.strptime(archive['savedOn'], '%d %b %Y').date()
    except (KeyError, ValueError, TypeError):
        raise ValueError('Snapshot needs a valid archive.savedOn date.') from None
    return archive, recorded, hashlib.sha256(raw).hexdigest()


def import_snapshot(conn, path):
    archive, recorded, digest = read_source(path)
    inserted = 0
    with conn:
        with conn.cursor() as cur:
            # Prevent simultaneous imports from racing the first-data check.
            cur.execute("select pg_advisory_xact_lock(hashtext('dstt.supplementary.import'))")
            cur.execute('select dataset_key, payload from aux.dashboard_dataset')
            existing = dict(cur.fetchall())
            changed = [key for key in KEYS if key in existing and existing[key] != archive[key]]
            if changed:
                raise ValueError('Existing PostgreSQL datasets differ; no data changed: ' + ', '.join(changed))
            for key in KEYS:
                if key in existing:
                    continue
                cur.execute('''insert into aux.dashboard_dataset
                    (dataset_key, payload, source_file, source_sha256, source_recorded_date)
                    values (%s, %s, %s, %s, %s)''',
                    (key, Json(archive[key]), Path(path).name, digest, recorded))
                inserted += 1
    return {'inserted': inserted, 'unchanged': len(KEYS) - inserted,
            'source_recorded_date': recorded.isoformat(), 'source_sha256': digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.dry_run:
        archive, recorded, digest = read_source(args.snapshot)
        print(json.dumps({'datasets': {k: len(archive[k]) for k in KEYS},
                          'source_recorded_date': recorded.isoformat(), 'source_sha256': digest}))
        return
    conn = pgdata.connect()
    try:
        print(json.dumps(import_snapshot(conn, args.snapshot)))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
