"""Exact invoice arithmetic and persistence contracts; no live database writes."""
import json
from decimal import Decimal
from pathlib import Path
import unittest

import invoice_items as invoice


D = Decimal
ROOT = Path(__file__).resolve().parents[1]


def product(identifier=1, **changes):
    return {'product_id': identifier, 'catalog_key': f'TEST-{identifier}',
            'product_name': 'Towel <Blue> & Co', 'unit': None, 'mrp': D('118.00'),
            'common_mrp': D('118.00'), 'north_retail': D('106.20'),
            'gifting': None, 'lulu_retail': D('82.60'), 'is_active': True, **changes}


class Cursor:
    def __init__(self, products=(), colours=()):
        self.products = list(products)
        self.colours = list(colours)    # (name, hex, is_active)
        self.calls = []
        self.rows = []
        self.header = None
        self.items = []

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        if 'from master.product_catalog' in sql:
            ids = args[0]
            choices = [p for p in self.products if p['product_id'] in ids or
                       'where is_active' in sql and p['is_active']]
            self.rows = [tuple(p[key] for key in invoice.CATALOG_COLUMNS) for p in choices]
        elif 'from master.product_colour' in sql:
            self.rows = [(name, hex_code) for name, hex_code, active in self.colours
                         if active or name in args[0]]
        elif sql.startswith('select price_list from txn.sales_invoice'):
            self.rows = [(self.header,)] if self.header else []
        elif sql.startswith('select 1 from txn.sales_invoice'):
            self.rows = [(1,)] if self.header else []
        elif 'from txn.sales_invoice_item' in sql and sql.startswith('select'):
            self.rows = [tuple(item[key] for key in invoice.ITEM_COLUMNS) for item in self.items]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def form(items=None, **changes):
    return {'entry_mode': 'invoice', 'price_list': 'common_mrp',
            'invoice_items': json.dumps(items if items is not None else [
                {'product_id': 1, 'quantity': '1', 'unit_rate': '118', 'discount_percent': '0'}]),
            **changes}


class InvoiceItemsTests(unittest.TestCase):
    def test_inclusive_gst_discount_and_totals_use_exact_half_up_cents(self):
        cur = Cursor([product()])
        prepared = invoice.prepare_invoice(cur, form([
            {'product_id': 1, 'quantity': '2', 'unit_rate': '118', 'discount_percent': '10'},
            {'product_id': 1, 'quantity': '0.50', 'unit_rate': '0.01', 'discount_percent': '50'},
            {'product_id': 1, 'quantity': '1', 'unit_rate': '0.59', 'discount_percent': '0'},
        ]))
        first, half_cent, small = prepared['items']
        self.assertEqual([first[k] for k in ('gross_amount', 'discount_amount', 'total_amount', 'taxable_amount', 'gst_amount')],
                         list(map(D, ['236.00', '23.60', '212.40', '180.00', '32.40'])))
        self.assertEqual(half_cent['gross_amount'], D('0.01'))
        self.assertEqual(half_cent['discount_amount'], D('0.01'))
        self.assertEqual(half_cent['total_amount'], D('0.00'))
        self.assertEqual((small['taxable_amount'], small['gst_amount']), (D('0.50'), D('0.09')))
        self.assertEqual(prepared['primary_qty'], D('3.50'))
        self.assertEqual(prepared['primary_taxable'], D('180.50'))
        self.assertEqual(prepared['primary_with_tax'], D('212.99'))
        self.assertEqual(sum(i['gst_amount'] for i in prepared['items']), D('32.49'))

    def test_round_per_line_before_summing_not_invoice_wide(self):
        cur = Cursor([product()])
        lines = [{'product_id': 1, 'quantity': '1', 'unit_rate': '.03'}] * 2
        result = invoice.prepare_invoice(cur, form(lines))
        self.assertEqual(result['primary_with_tax'], D('.06'))
        self.assertEqual(result['primary_taxable'], D('.06'))
        self.assertEqual(sum(i['gst_amount'] for i in result['items']), 0)
        self.assertNotEqual(result['primary_taxable'], (D('.06') / D('1.18')).quantize(D('.01')))

    def test_zero_rate_and_full_discount_are_explicit_valid_zero_sales(self):
        cur = Cursor([product()])
        for line in ({'product_id': 1, 'quantity': 2, 'unit_rate': '0'},
                     {'product_id': 1, 'quantity': 2, 'unit_rate': '118', 'discount_percent': '100'}):
            result = invoice.prepare_invoice(cur, form([line]))
            self.assertEqual(result['primary_qty'], 2)
            self.assertEqual(result['primary_with_tax'], 0)
            self.assertEqual(result['primary_taxable'], 0)

    def test_invalid_inputs_fail_before_any_catalog_or_sales_write(self):
        base = {'product_id': 1, 'quantity': '1', 'unit_rate': '118', 'discount_percent': '0'}
        cases = {'product_id': [None, 0, -1, True, '1.0', '1 OR 1=1', 2147483648],
                 'quantity': ['', None, True, 0, -1, '.001', 'NaN', 'Infinity', '1e100', '1' * 65],
                 'unit_rate': ['', None, False, '-1', '.001', 'NaN', 'Infinity', '1000000000000'],
                 'discount_percent': ['', None, True, '-1', '100.01', '1.001', 'NaN']}
        for key, invalid in cases.items():
            for value in invalid:
                with self.subTest(key=key, value=value):
                    cur = Cursor([product()])
                    with self.assertRaises(invoice.InvoiceError):
                        invoice.prepare_invoice(cur, form([{**base, key: value}]))
                    self.assertEqual(cur.calls, [], 'Malformed values fail before database access')

    def test_payload_limits_invalid_lists_and_price_lists(self):
        for raw in ('{', 'null', '{}', '[]', '[1]', '[null]', 'x' * 100001,
                    json.dumps([{'product_id': 1, 'quantity': 1, 'unit_rate': 118}] * 101)):
            with self.subTest(raw=raw[:30]), self.assertRaises(invoice.InvoiceError):
                invoice.prepare_invoice(Cursor(), form(invoice_items=raw))
        with self.assertRaises(invoice.InvoiceError):
            invoice.prepare_invoice(Cursor(), form(price_list='common_mrp; DROP TABLE'))
        with self.assertRaises(invoice.InvoiceError):
            invoice.prepare_invoice(Cursor(), form(entry_mode='other'))

    def test_product_and_total_bounds_unknown_products_and_missing_prices(self):
        cur = Cursor([product(gifting=None)])
        with self.assertRaises(invoice.InvoiceError):
            invoice.prepare_invoice(cur, form([{'product_id': 99, 'quantity': 1, 'unit_rate': '118'}]))
        with self.assertRaises(invoice.InvoiceError):
            invoice.prepare_invoice(cur, form([{'product_id': 1, 'quantity': '999999999999.99', 'unit_rate': '2'}]))
        with self.assertRaises(invoice.InvoiceError):
            invoice.prepare_invoice(cur, form([{'product_id': 1, 'quantity': '999999999999.99', 'unit_rate': '0'}] * 2))
        result = invoice.prepare_invoice(cur, form(price_list='gifting'))
        self.assertIsNone(result['items'][0]['price_list_rate'])
        self.assertEqual(result['items'][0]['unit_rate'], D('118.00'), 'Manual entered rate survives a missing list price')
        with self.assertRaises(invoice.InvoiceError):
            invoice.prepare_invoice(cur, form([{'product_id': 1, 'quantity': 1, 'unit_rate': ''}], price_list='gifting'))

    def test_catalog_snapshots_are_retained_when_catalog_changes_or_is_inactive(self):
        old = invoice.prepare_invoice(Cursor([product()]), form())
        changed = Cursor([product(product_name='New name', mrp=D('5000'), common_mrp=D('5000'), unit='box', is_active=False)])
        edited = invoice.prepare_invoice(changed, form([{'product_id': 1, 'quantity': 3, 'unit_rate': 118}]), old)
        saved = edited['items'][0]
        self.assertEqual(saved['product_name'], 'Towel <Blue> & Co')
        self.assertEqual(saved['mrp'], D('118'))
        self.assertEqual(saved['price_list_rate'], D('118'))
        self.assertIsNone(saved['unit'])
        self.assertEqual(edited['primary_with_tax'], D('354'))
        with self.assertRaises(invoice.InvoiceError):
            invoice.prepare_invoice(changed, form())

    def test_posted_snapshots_and_totals_are_ignored_and_sql_is_parameterized(self):
        name = "Product '); DROP TABLE txn.sales_entry; -- <unsafe>"
        cur = Cursor([product(product_name=name)])
        line = {'product_id': 1, 'quantity': 1, 'unit_rate': 118, 'product_name': 'FORGED',
                'taxable_amount': '-100', 'total_amount': '9999', 'mrp': '0'}
        prepared = invoice.prepare_invoice(cur, form([line], primary_taxable='999999'))
        self.assertEqual(prepared['primary_taxable'], D('100'))
        self.assertEqual(prepared['items'][0]['product_name'], name)
        invoice.save_invoice(cur, 42, prepared)
        writes = [(sql, args) for sql, args in cur.calls if not sql.startswith('select')]
        self.assertEqual(len(writes), 3)
        self.assertTrue(writes[0][0].startswith('insert into txn.sales_invoice('))
        self.assertTrue(writes[1][0].startswith('delete from txn.sales_invoice_item'))
        self.assertTrue(writes[2][0].startswith('insert into txn.sales_invoice_item('))
        self.assertIn(name, writes[2][1])
        self.assertNotIn(name, writes[2][0])
        self.assertFalse(any('txn.sales_entry' in sql or 'commit' in sql.lower() for sql, _ in writes),
                         'Caller writes one parent and owns the transaction; item save never posts duplicate sales')

    def test_load_and_options_return_saved_values_and_only_requested_inactive_choices(self):
        cur = Cursor([product(), product(2, is_active=False)])
        options = invoice.catalog_options(cur)
        self.assertEqual([p['product_id'] for p in options], [1])
        self.assertEqual(options[0]['prices']['north_retail'], '106.20')
        self.assertIsNone(options[0]['prices']['gifting'])
        json.dumps(options)
        self.assertEqual([p['product_id'] for p in invoice.catalog_options(cur, [2])], [1, 2])
        self.assertIsNone(invoice.load_invoice(cur, 8))
        prepared = invoice.prepare_invoice(cur, form())
        cur.header, cur.items = prepared['price_list'], prepared['items']
        self.assertEqual(invoice.load_invoice(cur, 8)['items'], prepared['items'])

    def test_optional_colour_is_validated_against_the_list_and_saved_per_line(self):
        cur = Cursor([product(), product(2)], [('Navy Blue', '#1f2a52', True), ('Old Rose', '#e0708b', False)])
        lines = [{'product_id': 1, 'quantity': '1', 'unit_rate': '118', 'colour': ' Navy Blue '},
                 {'product_id': 2, 'quantity': '1', 'unit_rate': '118', 'colour': ''},
                 {'product_id': 2, 'quantity': '1', 'unit_rate': '118'}]
        prepared = invoice.prepare_invoice(cur, form(lines))
        self.assertEqual([item['colour'] for item in prepared['items']], ['Navy Blue', None, None])
        invoice.save_invoice(cur, 9, prepared)
        insert = [(sql, args) for sql, args in cur.calls if sql.startswith('insert into txn.sales_invoice_item(')]
        self.assertTrue(all('colour' in sql for sql, _ in insert))
        self.assertEqual([args[-1] for _, args in insert], ['Navy Blue', None, None])
        for colour in ('Purple', 'Old Rose', 7, 'x' * 61):
            with self.subTest(colour=colour), self.assertRaises(invoice.InvoiceError):
                invoice.prepare_invoice(cur, form([{**lines[0], 'colour': colour}]))
        # A retired colour stays valid on the invoice that already saved it.
        saved = {'price_list': 'common_mrp', 'items': [{**prepared['items'][0], 'colour': 'Old Rose'}]}
        kept = invoice.prepare_invoice(cur, form([{**lines[0], 'colour': 'Old Rose'}]), saved)
        self.assertEqual(kept['items'][0]['colour'], 'Old Rose')
        self.assertEqual([c['name'] for c in invoice.colour_options(cur)], ['Navy Blue'])
        self.assertEqual([c['name'] for c in invoice.colour_options(cur, ['Old Rose', None])], ['Navy Blue', 'Old Rose'])

    def test_lines_without_colours_never_query_the_colour_list(self):
        cur = Cursor([product()])
        invoice.prepare_invoice(cur, form())
        self.assertFalse(any('product_colour' in sql for sql, _ in cur.calls))

    def test_summary_mode_cannot_remove_saved_invoice_and_absent_items_never_erase(self):
        cur = Cursor([product()])
        self.assertEqual(invoice.prepare_invoice(cur, {})['entry_mode'], 'summary')
        existing = invoice.prepare_invoice(cur, form())
        for posted in ({'entry_mode': 'summary'}, {'entry_mode': 'invoice'}, {}):
            with self.subTest(posted=posted), self.assertRaises(invoice.InvoiceError):
                invoice.prepare_invoice(cur, posted, existing)
        cur.header = 'common_mrp'
        with self.assertRaises(invoice.InvoiceError):
            invoice.save_invoice(cur, 1, {'entry_mode': 'summary'})

    def test_catalog_import_validates_all_rows_before_writing_and_preserves_null(self):
        asset = {'schema_version': 1, 'tax_basis': 'inclusive', 'gst_rate': '18.00',
                 'products': [{'catalog_key': 'A', 'product_name': "A's towel", 'mrp': None,
                               'unit': None, 'prices': {key: None for key in invoice.PRICE_LISTS}}]}
        cur = Cursor()
        self.assertEqual(invoice.import_catalog(cur, asset), 1)
        sql, args = cur.calls[0]
        self.assertNotIn("A's towel", sql)
        self.assertEqual(args[1], "A's towel")
        self.assertTrue(all(value is None for value in args[2:8]))
        self.assertIn('is distinct from', sql)
        invalid = {**asset, 'products': asset['products'] * 2}
        cur = Cursor()
        with self.assertRaises(invoice.InvoiceError):
            invoice.import_catalog(cur, invalid)
        self.assertEqual(cur.calls, [])



    def test_late_item_failure_propagates_to_the_parent_transaction(self):
        class FailingCursor(Cursor):
            def execute(self, sql, args=()):
                super().execute(sql, args)
                if sql.startswith('insert into txn.sales_invoice_item(') and args[1] == 2:
                    raise RuntimeError('simulated item failure')
        cur = FailingCursor([product()])
        prepared = invoice.prepare_invoice(cur, form([
            {'product_id': 1, 'quantity': 1, 'unit_rate': 118},
            {'product_id': 1, 'quantity': 2, 'unit_rate': 118},
        ]))
        with self.assertRaisesRegex(RuntimeError, 'simulated item failure'):
            invoice.save_invoice(cur, 77, prepared)
        self.assertFalse(any('commit' in sql.lower() for sql, _ in cur.calls),
                         'An error must reach the caller transaction, never commit a partial invoice')


if __name__ == '__main__':
    unittest.main()
