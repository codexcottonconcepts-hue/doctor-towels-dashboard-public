"""Public distribution examples and unconfigured imports never use live data."""
import csv
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import invoice_items
import lfo_data
import online_sales_data
import online_sales_runtime
import sheet_sync_runtime


ROOT = Path(__file__).resolve().parents[1]


class ForbiddenConnection:
    def cursor(self, *args, **kwargs):
        raise AssertionError('An unconfigured import must not access a database')


class PublicExamplesTests(unittest.TestCase):
    def test_example_catalog_has_only_fictional_products_and_preserves_missing_prices(self):
        path = ROOT / 'catalog' / 'products.example.json'
        asset = json.loads(path.read_text(encoding='utf-8'))
        self.assertIn('Fictional', asset['description'])
        self.assertEqual([p['catalog_key'] for p in asset['products']], ['DEMO-001', 'DEMO-002'])
        self.assertIsNone(asset['products'][1]['prices']['gifting'])

        class RecordingCursor:
            def __init__(self): self.calls = []
            def execute(self, query, args=()): self.calls.append((query, args))

        cursor = RecordingCursor()
        self.assertEqual(invoice_items.import_catalog(cursor, path), 2)
        self.assertEqual(len(cursor.calls), 2)
        self.assertTrue(all(query.startswith('insert into master.product_catalog')
                            for query, _ in cursor.calls))
        self.assertEqual(invoice_items.import_catalog(ForbiddenConnection(), path, dry_run=True), 2)

    def test_target_example_is_explicitly_demo_scoped(self):
        path = ROOT / 'pgdb' / 'etl' / 'targets.example.csv'
        with path.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows)
        self.assertTrue(all('Fictional' in row.get('notes', '') for row in rows))
        self.assertTrue(all(row.get('period_month') and row.get('target_value') for row in rows))

    def test_blank_sources_fail_before_network_or_database_access(self):
        with patch.object(lfo_data, 'SOURCE_ID', ''), \
                patch.object(sheet_sync_runtime, 'urlopen') as direct_network:
            with self.assertRaises(ValueError):
                sheet_sync_runtime._fetch_workbook('')
            direct_network.assert_not_called()
            with self.assertRaises(lfo_data.LFOValidationError):
                lfo_data.import_workbook(object(), ForbiddenConnection())

        with patch.object(online_sales_data, 'SOURCE_ID', ''), \
                patch.object(online_sales_runtime, 'urlopen') as online_network:
            with self.assertRaises(ValueError):
                online_sales_runtime._fetch_workbook('')
            online_network.assert_not_called()
            with self.assertRaises(online_sales_data.OnlineSalesValidationError):
                online_sales_data.sync_workbook(object(), '', ForbiddenConnection())


if __name__ == '__main__':
    unittest.main()
