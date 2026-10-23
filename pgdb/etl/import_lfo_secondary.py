"""Validate or import a local LFO Distributed or L-LFO Distributed workbook into PostgreSQL.

    python import_lfo_secondary.py --workbook source.xlsx --dry-run
    python import_lfo_secondary.py --workbook source.xlsx
    python import_lfo_secondary.py --workbook source.xlsx --channel L-LFO --dry-run
    python import_lfo_secondary.py --workbook source.xlsx --channel L-LFO --fy 2026

--fy is only needed for L-LFO Distributed when the sheet itself names no
financial year; a year in the sheet always wins.
"""
import argparse
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import openpyxl
import lfo_data
import pgdata


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workbook', required=True, type=Path)
    parser.add_argument('--channel', choices=sorted(lfo_data.CHANNELS), default='LFO',
                        help='LFO reads LFO Distributed; L-LFO reads L-LFO Distributed (default LFO)')
    parser.add_argument('--fy', type=int, help='L-LFO only: financial year when the sheet names none, e.g. 2026')
    parser.add_argument('--dry-run', action='store_true', help='Validate without opening a database connection')
    args = parser.parse_args()
    wb = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)
    try:
        parsed = (lfo_data.parse_workbook(wb) if args.channel == 'LFO'
                  else lfo_data.parse_llfo_workbook(wb, fy=args.fy))
        actuals = [row for row in parsed['sales'] if row['amount'] is not None]
        print('Sheet:', parsed['sheet_name'], '- financial year', parsed['fy'])
        print('Distributors:', len(parsed['distributors']))
        print('Named stores:', sum(not s['isAggregate'] for s in parsed['stores']))
        print('Reported monthly amounts:', len(actuals))
        print('Secondary sales:', sum((s['amount'] for s in actuals), Decimal(0)))
        for warning in parsed['warnings']:
            print('Source note:', warning)
        if not args.dry_run:
            with closing(pgdata.connect()) as conn:
                result = lfo_data.import_workbook(wb, conn, actor='local-workbook-import',
                                                  channel=args.channel, fy=args.fy)
            print('Imported:', result['inserted'], 'Updated:', result['updated'],
                  'Manual entries preserved:', result['manual_preserved'])
    finally:
        wb.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(getattr(exc, 'public_message', 'Import failed. Check the workbook and PostgreSQL configuration.'))
        raise SystemExit(1)
