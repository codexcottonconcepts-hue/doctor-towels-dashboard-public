"""Automatic PI identity and optional invoice regressions.

The SQL suite is opt-in, localhost-only and requires an explicitly prepared
*_test database. It never reads the normal dashboard DSN or contacts Warehouse.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from threading import Barrier
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg2
from psycopg2.extensions import parse_dsn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

import entry_web
import pi_numbers
try:
    import stock_bridge
except ModuleNotFoundError as exc:
    if exc.name != 'stock_bridge':
        raise
    stock_bridge = None


ROOT = Path(__file__).resolve().parents[1]
ADMIN = {'username': 'pi-regression', 'is_admin': True}


def form(**changes):
    return {'invoice_date': '27-10-2026', 'invoice_no': '', 'pi_number': '',
            'submission_id': str(uuid4()), 'customer_id': '1', 'channel_id': '1',
            'brand_id': '1', 'state_id': '1', 'primary_qty': '1',
            'primary_taxable': '100', 'primary_with_tax': '105', **changes}


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.controls = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('input', 'select', 'textarea') and attrs.get('name') and attrs.get('type') != 'hidden':
            self.controls.append(attrs)


class PIFormTests(unittest.TestCase):
    def test_blank_invoice_number_is_nullable_and_never_invented_by_the_parser(self):
        for invoice in ('', '  '):
            with self.subTest(invoice=invoice):
                parsed = entry_web.parse_entry(form(invoice_no=invoice))
                self.assertIsNone(parsed['invoice_no'])
                self.assertIsNone(parsed['pi_number'])

    def test_real_invoice_and_existing_pi_text_are_not_changed_by_parser(self):
        parsed = entry_web.parse_entry(form(invoice_no=' BILL/26-27/0007 ', pi_number=' LEGACY/PI/0009 '))
        self.assertEqual(parsed['invoice_no'], 'BILL/26-27/0007')
        self.assertEqual(parsed['pi_number'], 'LEGACY/PI/0009')

    def test_pi_is_first_readonly_field_and_invoice_number_is_optional(self):
        options = {key: [] for key in entry_web.DIMENSIONS}
        html = entry_web.entry_form(options, {'invoice_date': date(2026, 10, 27)}, 'csrf', user=ADMIN)
        controls = Controls(html).controls
        fields = {item['name']: item for item in controls}
        names = [item['name'] for item in controls]
        self.assertLess(names.index('pi_number'), names.index('invoice_date'))
        self.assertLess(names.index('pi_number'), names.index('invoice_no'))
        self.assertIn('readonly', fields['pi_number'])
        self.assertNotIn('required', fields['invoice_no'])

    def test_existing_pi_display_is_preserved_and_stays_readonly(self):
        options = {key: [] for key in entry_web.DIMENSIONS}
        html = entry_web.entry_form(options, {'entry_id': 9, 'invoice_date': date(2026, 10, 27),
            'invoice_no': None, 'pi_number': 'LEGACY/PI/0009'}, 'csrf', user=ADMIN)
        fields = {item['name']: item for item in Controls(html).controls}
        self.assertEqual(fields['pi_number']['value'], 'LEGACY/PI/0009')
        self.assertIn('readonly', fields['pi_number'])
        self.assertNotIn('required', fields['invoice_no'])


class PIPreviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.user = dict(ADMIN)
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='isolated-pi-preview-routes')
        entry_web.register(app, lambda _request: self.user, lambda _request: None,
                           lambda _title, body, _user: body, lambda: None)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for context in (patch.object(entry_web.pgdata, 'connect', return_value=self.connection),
                        patch.object(pi_numbers, 'preview_next', return_value='DST-PI26-001'),
                        patch.object(pi_numbers, 'allocate_next', side_effect=AssertionError('A preview must never reserve a number.'))):
            mocked = context.start()
            self.addCleanup(context.stop)
            if context.attribute == 'connect':
                self.connect = mocked
            elif context.attribute == 'preview_next':
                self.preview = mocked
            else:
                self.allocate = mocked

    def test_unauthenticated_preview_is_denied_before_opening_a_database(self):
        self.user = None
        response = self.client.get('/entries/pi-next?date=27-10-2026')
        self.assertEqual(response.status_code, 401)
        self.connect.assert_not_called()
        self.preview.assert_not_called()
        self.allocate.assert_not_called()

    def test_preview_requires_complete_create_access_before_opening_a_database(self):
        for permissions in ([], ['sales.create'], ['sales.edit', 'sales.invoice.edit', 'sales.location.edit'],
                            ['sales.courier.edit']):
            self.user = {'username': 'pi-preview-limited', 'is_admin': False,
                         'permissions_json': json.dumps(permissions)}
            with self.subTest(permissions=permissions):
                response = self.client.get('/entries/pi-next?date=27-10-2026')
                self.assertEqual(response.status_code, 403)
        self.connect.assert_not_called()
        self.preview.assert_not_called()
        self.allocate.assert_not_called()

    def test_preview_returns_date_financial_year_without_reservation_or_caching(self):
        response = self.client.get('/entries/pi-next?date=31-03-2027')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'pi_number': 'DST-PI26-001', 'financial_year': 2026, 'reserved': False})
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        self.preview.assert_called_once_with(self.cursor, 2026)
        self.allocate.assert_not_called()
        self.connection.close.assert_called_once()

    def test_invalid_preview_dates_fail_before_database_access(self):
        for value in ('', '31-02-2026', '2026/10/27', 'not-a-date'):
            with self.subTest(date=value):
                response = self.client.get('/entries/pi-next', params={'date': value})
                self.assertEqual(response.status_code, 400)
        self.connect.assert_not_called()
        self.preview.assert_not_called()
        self.allocate.assert_not_called()


@unittest.skipUnless(os.environ.get('PI_TEST_DATABASE_URL'),
                     'Set PI_TEST_DATABASE_URL to a prepared localhost *_test database')
class PINumberPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['PI_TEST_DATABASE_URL']
        parsed = parse_dsn(cls.dsn)
        cls.database = parsed.get('dbname', '')
        if not cls.database.endswith('_test') or parsed.get('host') not in ('127.0.0.1', 'localhost', '::1'):
            raise RuntimeError('PI regressions require an explicitly named localhost *_test database.')
        migration = ROOT / 'pgdb' / 'sql' / '24_sales_pi_numbers.sql'
        if not migration.is_file():
            raise RuntimeError('The tested PI migration 24 is required.')
        with closing(psycopg2.connect(cls.dsn)) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute('select current_database(),inet_server_port()')
                if cur.fetchone()[0] != cls.database:
                    raise RuntimeError('Unexpected database identity.')
                # Put a real legacy PI in this disposable schema before the
                # repeated upgrade. An empty-ledger digest cannot prove that
                # existing document identities survive a migration.
                tag = uuid4().hex
                cur.execute('insert into master.state(name) values(%s) returning state_id', ('Legacy PI state ' + tag,))
                state_id = cur.fetchone()[0]
                cur.execute('insert into master.customer(name) values(%s) returning customer_id', ('Legacy PI customer ' + tag,))
                customer_id = cur.fetchone()[0]
                cur.execute("select channel_id from master.channel where code='LFO'")
                found = cur.fetchone()
                if found:
                    channel_id = found[0]
                else:
                    cur.execute("insert into master.channel(code,name) values('LFO','LFO') returning channel_id")
                    channel_id = cur.fetchone()[0]
                cur.execute("select brand_id from master.brand where code='DT'")
                found = cur.fetchone()
                if found:
                    brand_id = found[0]
                else:
                    cur.execute("insert into master.brand(code,name) values('DT','Doctor Towel') returning brand_id")
                    brand_id = cur.fetchone()[0]
                cur.execute("insert into master.financial_year(fy,label,start_date,end_date) values "
                            "(2026,'2026-27','2026-04-01','2027-03-31') on conflict (fy) do nothing")
                cls.legacy_pi = 'DST-PI26-1250A'
                cur.execute('insert into txn.sales_entry(fy,invoice_date,invoice_no,pi_number,customer_id,channel_id,brand_id,state_id) '
                            'values(2026,%s,NULL,%s,%s,%s,%s,%s) returning entry_id',
                            ('2026-10-27', cls.legacy_pi, customer_id, channel_id, brand_id, state_id))
                cls.legacy_entry_id = cur.fetchone()[0]
                cur.execute("select md5(coalesce(string_agg(to_jsonb(e)::text,'' order by entry_id),'')) from txn.sales_entry e")
                before = cur.fetchone()[0]
                for _ in range(2):
                    cur.execute(migration.read_text(encoding='utf-8'))
                cur.execute("select md5(coalesce(string_agg(to_jsonb(e)::text,'' order by entry_id),'')) from txn.sales_entry e")
                if cur.fetchone()[0] != before:
                    raise AssertionError('PI migration rewrote existing sales or PI identities.')

    def setUp(self):
        self.tag = uuid4().hex
        with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
            def insert(table, identifier, **values):
                cur.execute('insert into ' + table + '(' + ','.join(values) + ') values (' +
                            ','.join(['%s'] * len(values)) + ') returning ' + identifier, tuple(values.values()))
                return cur.fetchone()[0]
            self.state = insert('master.state', 'state_id', name='PI regression state ' + self.tag)
            self.customer = insert('master.customer', 'customer_id', name='PI regression customer ' + self.tag)
            cur.execute("select channel_id from master.channel where code='LFO'")
            selected = cur.fetchone()
            self.channel = selected[0] if selected else insert('master.channel', 'channel_id', code='LFO', name='LFO')
            self.brands = []
            for code, label in (('DT', 'Doctor Towel'), ('TT', 'Thirsty Towel')):
                cur.execute('select brand_id from master.brand where code=%s', (code,))
                selected = cur.fetchone()
                self.brands.append(selected[0] if selected else insert('master.brand', 'brand_id', code=code, name=label))
            cur.execute("insert into master.financial_year(fy,label,start_date,end_date) values "
                        "(2026,'2026-27','2026-04-01','2027-03-31') on conflict (fy) do nothing")
        self.connect = patch.object(entry_web.pgdata, 'connect', side_effect=lambda: psycopg2.connect(self.dsn))
        self.connect.start()
        self.addCleanup(self.connect.stop)

    def posted(self, **changes):
        return form(**{'customer_id': str(self.customer), 'channel_id': str(self.channel),
                       'brand_id': str(self.brands[0]), 'state_id': str(self.state), **changes})

    def save(self, posted):
        return entry_web.save_entry(posted, ADMIN['username'], ADMIN)

    def query(self, sql, args=()):
        with closing(psycopg2.connect(self.dsn)) as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    def saved(self, entry_id):
        with closing(psycopg2.connect(self.dsn)) as conn, conn.cursor() as cur:
            cur.execute('select * from txn.sales_entry where entry_id=%s', (entry_id,))
            return dict(zip((col[0] for col in cur.description), cur.fetchone()))

    def edit_form(self, entry_id, **changes):
        saved = self.saved(entry_id)
        return {**{key: '' if value is None else str(value) for key, value in saved.items()},
                'revision': (saved['updated_at'] or saved['created_at']).isoformat(), **changes}

    def test_blank_invoice_is_stored_null_and_new_pi_is_server_assigned(self):
        identifier, replayed = self.save(self.posted())
        self.assertFalse(replayed)
        saved = self.saved(identifier)
        self.assertIsNone(saved['invoice_no'])
        self.assertTrue(saved['pi_number'])
        self.assertEqual(saved['line_no'], 1)
        self.assertEqual(saved['primary_taxable'], Decimal('100'))
        self.assertRegex(saved['pi_number'], r'^DST-PI26-[0-9]+$')

    def test_readonly_pi_preview_does_not_reserve_or_consume_the_next_number(self):
        with closing(psycopg2.connect(self.dsn)) as conn:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                first = pi_numbers.preview_next(cur, 2026)
                repeated = pi_numbers.preview_next(cur, 2026)
        self.assertEqual(first, repeated)
        self.assertRegex(first, r'^DST-PI26-[0-9]+$')
        self.assertGreaterEqual(int(first.rsplit('-', 1)[1]), 1251)
        identifier, _ = self.save(self.posted())
        self.assertEqual(self.saved(identifier)['pi_number'], first)

    def test_pi_prefix_follows_april_march_financial_year_and_uses_three_digit_minimum(self):
        with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute("insert into master.financial_year(fy,label,start_date,end_date) values "
                        "(2027,'2027-28','2027-04-01','2028-03-31') on conflict (fy) do nothing")
        march_id, _ = self.save(self.posted(invoice_date='31-03-2027'))
        april_id, _ = self.save(self.posted(invoice_date='01-04-2027'))
        self.assertRegex(self.saved(march_id)['pi_number'], r'^DST-PI26-[0-9]{3,}$')
        self.assertRegex(self.saved(april_id)['pi_number'], r'^DST-PI27-[0-9]{3,}$')
        self.assertEqual(self.saved(march_id)['fy'], 2026)
        self.assertEqual(self.saved(april_id)['fy'], 2027)

    def test_new_form_post_cannot_choose_or_reuse_a_pi_number(self):
        first_id, _ = self.save(self.posted(pi_number='FORGED/PI/1'))
        first = self.saved(first_id)
        self.assertRegex(first['pi_number'], r'^DST-PI26-[0-9]+$')
        self.assertNotEqual(first['pi_number'], 'FORGED/PI/1')
        second_id, _ = self.save(self.posted(pi_number=first['pi_number']))
        self.assertNotEqual(self.saved(second_id)['pi_number'], first['pi_number'])

    def test_legacy_pi_and_null_pi_are_never_replaced_by_posted_preview_values_on_edit(self):
        first = self.saved(self.legacy_entry_id)
        self.save(self.edit_form(self.legacy_entry_id, pi_number='FORGED/NEW/1'))
        self.assertEqual(self.saved(self.legacy_entry_id)['pi_number'], first['pi_number'])
        identifier, _ = self.save(self.posted())
        with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute('update txn.sales_entry set pi_number=NULL where entry_id=%s', (identifier,))
        self.save(self.edit_form(identifier, pi_number='FORGED/NEW/2'))
        self.assertIsNone(self.saved(identifier)['pi_number'])

    def test_higher_imported_deleted_brand_split_pi_suffix_is_skipped_without_rewriting_history(self):
        first_id, _ = self.save(self.posted())
        first_number = int(self.saved(first_id)['pi_number'].rsplit('-', 1)[1])
        legacy_number = first_number + 100
        imported_pi = 'DST-PI26-' + str(legacy_number) + 'B'
        with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute("insert into master.financial_year(fy,label,start_date,end_date) values "
                        "(2025,'2025-26','2025-04-01','2026-03-31') on conflict (fy) do nothing")
            cur.execute('insert into txn.sales_entry(fy,invoice_date,pi_number,customer_id,channel_id,brand_id,state_id,is_deleted) '
                        'values(2025,%s,%s,%s,%s,%s,%s,true) returning entry_id',
                        ('2025-10-27', imported_pi, self.customer, self.channel, self.brands[1], self.state))
            imported_id = cur.fetchone()[0]
        second_id, _ = self.save(self.posted())
        self.assertEqual(self.saved(second_id)['pi_number'], 'DST-PI26-' + str(legacy_number + 1))
        self.assertEqual(self.saved(imported_id)['pi_number'], imported_pi)
        self.assertTrue(self.saved(imported_id)['is_deleted'])

    def test_concurrent_new_forms_receive_distinct_pi_numbers_without_invoice_numbers(self):
        count = 6
        barrier = Barrier(count)
        def save_one(_index):
            posted = self.posted()
            barrier.wait(timeout=15)
            return self.save(posted)
        with ThreadPoolExecutor(max_workers=count) as executor:
            results = list(executor.map(save_one, range(count)))
        self.assertTrue(all(not replayed for _identifier, replayed in results))
        identifiers = [identifier for identifier, _replayed in results]
        rows = [self.saved(identifier) for identifier in identifiers]
        self.assertEqual(len(set(identifiers)), count)
        self.assertEqual(len({row['pi_number'].casefold() for row in rows}), count)
        self.assertTrue(all(row['invoice_no'] is None and row['line_no'] == 1 for row in rows))

    def test_simultaneous_same_form_retry_returns_one_entry_and_one_pi_identity(self):
        posted = self.posted()
        barrier = Barrier(2)
        def save_same(_index):
            barrier.wait(timeout=15)
            return self.save(dict(posted))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save_same, range(2)))
        self.assertEqual(len({identifier for identifier, _replayed in results}), 1)
        self.assertEqual(sorted(replayed for _identifier, replayed in results), [False, True])
        saved = self.saved(results[0][0])
        self.assertEqual(self.query('select count(*) from txn.sales_entry where submission_id=%s',
                                   (posted['submission_id'],))[0][0], 1)
        self.assertTrue(saved['pi_number'])
        self.assertIsNone(saved['invoice_no'])
        following_id, _ = self.save(self.posted())
        following = self.saved(following_id)
        first_number = re.search(r'([0-9]+)$', saved['pi_number'])
        following_number = re.search(r'([0-9]+)$', following['pi_number'])
        self.assertIsNotNone(first_number)
        self.assertIsNotNone(following_number)
        self.assertEqual(int(following_number.group(1)), int(first_number.group(1)) + 1,
                         'Replaying the same form must not consume another PI number.')

    def test_same_real_invoice_for_different_brands_remains_two_valid_rows(self):
        invoice = 'BRAND-SPLIT-' + self.tag
        first, _ = self.save(self.posted(invoice_no=invoice))
        second_form = self.posted(invoice_no=invoice)
        second_form['brand_id'] = str(self.brands[1])
        second, _ = self.save(second_form)
        self.assertNotEqual(first, second)
        rows = [self.saved(identifier) for identifier in (first, second)]
        self.assertEqual({row['brand_id'] for row in rows}, set(self.brands))
        self.assertEqual([row['invoice_no'] for row in rows], [invoice, invoice])
        self.assertEqual([row['line_no'] for row in rows], [1, 1])
        self.assertEqual(len({row['pi_number'] for row in rows}), 2)

    def test_editing_an_invoice_that_has_no_invoice_number_keeps_its_original_pi(self):
        identifier, _ = self.save(self.posted())
        first = self.saved(identifier)
        self.save(self.edit_form(identifier, remarks='Updated without inventing an invoice number'))
        saved = self.saved(identifier)
        self.assertEqual(saved['pi_number'], first['pi_number'])
        self.assertIsNone(saved['invoice_no'])
        self.assertEqual(saved['line_no'], 1)

    def test_editing_a_legacy_null_invoice_preserves_its_existing_internal_line_number(self):
        identifier, _ = self.save(self.posted())
        with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute('update txn.sales_entry set line_no=3 where entry_id=%s', (identifier,))
        first = self.saved(identifier)
        self.save(self.edit_form(identifier, remarks='Legacy internal line remains unchanged'))
        after = self.saved(identifier)
        self.assertEqual(after['line_no'], 3)
        self.assertEqual(after['pi_number'], first['pi_number'])
        self.assertIsNone(after['invoice_no'])

    def test_upgraded_legacy_pi_is_not_renumbered_when_its_invoice_details_are_edited(self):
        before = self.saved(self.legacy_entry_id)
        self.assertEqual(before['pi_number'], self.legacy_pi)
        self.save(self.edit_form(self.legacy_entry_id, remarks='Legacy PI details edited'))
        after = self.saved(self.legacy_entry_id)
        self.assertEqual(after['pi_number'], self.legacy_pi)
        self.assertIsNone(after['invoice_no'])

    @unittest.skipIf(stock_bridge is None, 'Warehouse integration is not part of this source copy')
    def test_warehouse_handoff_uses_the_assigned_pi_and_replay_keeps_one_payload(self):
        posted = self.posted()
        identifier, _ = self.save(posted)
        saved = self.saved(identifier)
        prepared = {'items': [{'warehouse_product_id': 1, 'warehouse_sku': 'PI-TEST-' + self.tag,
            'quantity': Decimal('1'), 'stock_quantity': Decimal('1'), 'units_per_sales_unit': Decimal('1'),
            'mrp': Decimal('105'), 'unit_rate': Decimal('105'), 'total_amount': Decimal('105'),
            'discount_percent': Decimal('0'), 'stock_unit': 'PCS', 'sales_unit': 'PCS'}]}
        with patch.object(stock_bridge, '_source_id', return_value=str(uuid4())), \
             patch.object(stock_bridge.requests, 'get', side_effect=AssertionError('No Warehouse HTTP during a SQL handoff test')), \
             patch.object(stock_bridge.requests, 'post', side_effect=AssertionError('No Warehouse HTTP during a SQL handoff test')):
            with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
                self.assertTrue(stock_bridge.queue_pi(cur, identifier, prepared, saved, ADMIN['username']))
                cur.execute('select external_id,payload,payload_hash from txn.warehouse_order_outbox where entry_id=%s', (identifier,))
                first = cur.fetchone()
                self.assertEqual(first[1]['pi_number'], saved['pi_number'])
                self.assertTrue(stock_bridge.queue_pi(cur, identifier, prepared, saved, ADMIN['username']))
                cur.execute('select external_id,payload,payload_hash from txn.warehouse_order_outbox where entry_id=%s', (identifier,))
                self.assertEqual(cur.fetchone(), first)
            self.assertEqual(self.save(posted), (identifier, True))
        self.assertEqual(self.query('select count(*) from txn.warehouse_order_outbox where entry_id=%s',
                                   (identifier,))[0][0], 1)
        self.assertEqual(self.query('select external_id,payload,payload_hash from txn.warehouse_order_outbox where entry_id=%s',
                                   (identifier,))[0], first)

    def test_invalid_form_never_creates_a_partial_pi_sales_entry(self):
        posted = self.posted(state_id='2147483647')
        with self.assertRaises(entry_web.EntryError):
            self.save(posted)
        self.assertEqual(self.query('select count(*) from txn.sales_entry where submission_id=%s',
                                   (posted['submission_id'],))[0][0], 0)


if __name__ == '__main__':
    unittest.main()
