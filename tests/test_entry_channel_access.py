"""Channel authorization regressions with isolated fake SQL; no business writes."""
import csv
import io
import json
import re
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

import auth
import dashboard_policy as dp
import entry_web as entry

REAL_LOAD_OPTIONS = entry.load_options


CHANNELS = {1: 'LFO', 2: 'L-LFO', 3: 'D&R-S', 4: 'D&R-N', 5: 'Saloon', 6: 'Hospital'}
OPERATOR_NORTH = ['D&R-N', 'LFO']
OPERATOR_SOUTH = ['D&R-S', 'Saloon', 'L-LFO']
GRANTS = ['dashboard.view', 'registers.view', 'sales.create',
          'sales.courier.edit', 'sales.stock_supplied.edit',
          *(f'sales.{s}.edit' for s in entry.CREATE_SECTIONS),
          *(f'sales.{s}.view' for s in entry.SECTION_FIELDS)]


def account(channels=OPERATOR_NORTH, **changes):
    policy = dp.full_policy()
    policy['scopes']['channel'] = channels
    return {'username': 'operator-north', 'is_admin': False, 'disabled': False,
            'permissions_json': json.dumps(GRANTS),
            'dashboard_access_json': json.dumps(policy), **changes}


def record(identifier, channel):
    return {**{key: 1 for key in entry.DIMENSIONS},
            **{key: Decimal('0') for key in entry.AMOUNTS},
            'entry_id': identifier, 'channel_id': channel,
            'invoice_date': date(2026, 4, 11), 'invoice_no': 'INVOICE-' + CHANNELS[channel],
            'line_no': 1, 'pi_number': None, 'primary_qty': Decimal('2'),
            'primary_taxable': Decimal('100'), 'primary_with_tax': Decimal('118'),
            'order_type': 'First', 'stock_supplied_on': date(2026, 4, 12),
            'courier_docket_no': 'TRACK-' + str(identifier), 'weight_kg': Decimal('2'),
            'transport_service': 'Cargo', 'remarks': 'Original note',
            'payment_received_on': None, 'updated_at': datetime(2026, 9, 23, 10, 11, 12),
            'created_at': datetime(2026, 4, 11, 1, 1, 1), 'is_deleted': False}


def token(value):
    return re.sub('[^a-z0-9]', '', value.lower())


class Cursor:
    def __init__(self, connection):
        self.conn, self.description, self.rows = connection, [], []

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def execute(self, query, args=()):
        self.conn.calls.append((query, args))
        self.rows, self.description = [], []
        compact = ' '.join(query.split())
        if compact.startswith('select * from txn.sales_entry'):
            row = self.conn.records.get(int(args[0]))
            if row and not row['is_deleted']:
                self.description = [(key,) for key in row]
                self.rows = [tuple(row.values())]
        elif compact.startswith('select code from master.channel'):
            value = CHANNELS.get(int(args[0]))
            self.rows = [(value,)] if value else []
        elif ' from rpt.v_sales' in compact:
            rows = list(self.conn.records.values())
            if re.search(r'\bwhere \(?false\b', compact):
                rows = []
            arrays = [v for v in args if isinstance(v, (list, tuple))]
            if 'regexp_replace' in compact and arrays:
                allowed = set(arrays[0])
                rows = [r for r in rows if token(CHANNELS[r['channel_id']]) in allowed]
            if compact.startswith('select count(*)'):
                self.rows = [(len(rows), sum((r['primary_taxable'] for r in rows), Decimal(0)),
                              sum((r['primary_with_tax'] for r in rows), Decimal(0)),
                              sum((r['primary_qty'] for r in rows), Decimal(0)))]
            else:
                columns = compact.split(' from ')[0].removeprefix('select ').split(',')
                for row in rows:
                    display = {**row, 'customer': 'Customer ' + str(row['entry_id']),
                               'channel': CHANNELS[row['channel_id']], 'sub_channel': 'Direct',
                               'sales_person': 'Executive', 'brand': 'Doctor Towel',
                               'state': 'Tamil Nadu', 'district': 'Chennai', 'city': 'Chennai',
                               'courier': 'DTDC', 'credit_days': None}
                    self.rows.append(tuple(display[k] for k in columns))
        elif compact.startswith('select entry_id,is_deleted') and 'submission_id=' in compact:
            row = self.conn.submitted
            if row:
                names = compact.split(' from ')[0].removeprefix('select ').split(',')
                self.rows = [tuple(row[name] for name in names)]
        elif compact.startswith('select code from master.courier'):
            self.rows = [('DTDC',)]
        elif compact.startswith('select state_id,district_id'):
            self.rows = [(1, 1)]
        elif compact.startswith('select state_id from master.district'):
            self.rows = [(1,)]
        elif compact.startswith('select coalesce(max(line_no),0)+1'):
            self.rows = [(1,)]
        elif compact.startswith('select coalesce(updated_at,created_at)'):
            row = self.conn.records.get(int(args[0]))
            self.rows = [(row['updated_at'], row['channel_id'])] if row else []
        elif compact.startswith('select ') and ' from master.channel' in compact:
            options = list(CHANNELS.items())
            if re.search(r'\band \(?false\b|\bwhere \(?false\b', compact):
                options = []
            arrays = [v for v in args if isinstance(v, (list, tuple))]
            if 'regexp_replace' in compact and arrays:
                options = [(i, c) for i, c in options if token(c) in arrays[0]]
            self.rows = options
        elif compact.startswith('select ') and ' from master.' in compact:
            self.rows = [(1,)]
        elif compact.startswith('insert into master.customer'):
            self.conn.mutations.append((query, args))
            self.rows = [(10,)]
        elif compact.startswith('insert into txn.sales_entry'):
            self.conn.mutations.append((query, args))
            self.conn.saved = dict(zip(entry.COLUMNS, args))
            self.rows = [(77,)]
        elif compact.startswith('update txn.sales_entry'):
            self.conn.mutations.append((query, args))
            setters = query.split(' set ', 1)[1].split(' where ', 1)[0].split(',')
            columns = [part.split('=')[0] for part in setters if '=%s' in part]
            self.conn.saved = dict(zip(columns, args))
            self.rows = [(int(args[-1]),)]

    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows
    def __iter__(self): return iter(self.rows)


class Connection:
    def __init__(self):
        self.records = {7: record(7, 1), 8: record(8, 3), 9: record(9, 6)}
        self.calls, self.mutations, self.saved, self.submitted = [], [], None, None
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return Cursor(self)
    def close(self): pass


class ChannelAccessTests(unittest.TestCase):
    def setUp(self):
        self.conn = Connection()
        self.user = account()
        self.invalidations = 0
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='channel-scope-isolated-tests')
        entry.register(app, lambda request: self.user, lambda request: None,
                       lambda title, body, user: body, self.invalidate)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.invoice_load = patch.object(entry.invoice_items, 'load_invoice', return_value=None).start()
        self.addCleanup(patch.stopall)
        patch.object(entry.pgdata, 'enabled', return_value=True).start()
        patch.object(entry.pgdata, 'connect', return_value=self.conn).start()
        patch.object(entry.invoice_items, 'catalog_options', return_value=[]).start()
        patch.object(entry.invoice_items, 'colour_options', return_value=[]).start()
        patch.object(entry, 'load_options', return_value={
            **{key: [(1, 'Visible option', 1, 1)] for key in entry.DIMENSIONS},
            'courier_id': [(1, 'DTDC')]}).start()

    def invalidate(self): self.invalidations += 1

    def hidden(self, path='/entries?view=new'):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text[-1000:])
        return dict(re.findall('<input type="hidden" name="([^"]+)" value="([^"]*)"', response.text))

    def form(self, **changes):
        return {**self.hidden(), 'invoice_date': '11-04-2026', 'invoice_no': 'NEW-INVOICE',
                'customer_id': '1', 'channel_id': '1', 'brand_id': '1', 'state_id': '1',
                'primary_qty': '2', 'primary_taxable': '100', 'primary_with_tax': '118',
                'entry_mode': 'summary', **changes}

    def test_scopes_apply_to_register_count_rows_and_export_for_both_accounts(self):
        for assigned, expected, forbidden in ((OPERATOR_NORTH, 'INVOICE-LFO', 'INVOICE-D&amp;R-S'),
                                               (OPERATOR_SOUTH, 'INVOICE-D&amp;R-S', 'INVOICE-LFO')):
            self.user = account(assigned, permissions_json=json.dumps([*GRANTS, 'exports.access']))
            with self.subTest(channels=assigned):
                response = self.client.get('/entries')
                self.assertEqual(response.status_code, 200)
                self.assertIn(expected, response.text)
                self.assertNotIn(forbidden, response.text)
                self.assertNotIn('INVOICE-Hospital', response.text)
                queries = [q for q, a in self.conn.calls if ' from rpt.v_sales' in q]
                self.assertTrue(all('regexp_replace' in q and 'any' in q.lower() for q in queries))
                exported = self.client.get('/entries/export.csv')
                self.assertEqual(exported.status_code, 200)
                data = list(csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff'))))
                self.assertEqual({r['channel'] for r in data}, set(assigned) & {'LFO', 'D&R-S'})

    def test_search_and_dates_do_not_remove_channel_scope(self):
        where, args = entry.ledger_query({'q': 'secret', 'from': '01-04-2026', 'to': '30-04-2026'}, self.user)
        self.assertIn('regexp_replace', where)
        self.assertIn('invoice_date >= %s', where)
        self.assertTrue(any(isinstance(a, (list, tuple)) for a in args))
        self.assertIn('%secret%', args)

    def test_channel_dropdown_contains_only_assigned_channels_even_for_forged_current_value(self):
        for assigned in (OPERATOR_NORTH, OPERATOR_SOUTH, []):
            self.user = account(assigned)
            with self.subTest(channels=assigned):
                options = REAL_LOAD_OPTIONS(self.conn.cursor(), {'channel_id': 6}, {'channel_id'}, self.user)
                self.assertEqual({row[1] for row in options['channel_id']}, set(assigned))

    def test_channel_aliases_are_normalized_and_unknown_ids_deny(self):
        self.assertEqual(set(entry.entry_channels(account(['D&R - N', 'L - LFO']))), {'D&R-N', 'L-LFO'})
        entry.assert_entry_channel(self.conn.cursor(), 4, account(['North']))
        with self.assertRaises(entry.EntryAccessError):
            entry.assert_entry_channel(self.conn.cursor(), 999, self.user)

    def test_outside_edit_get_denies_before_invoice_or_options_are_loaded(self):
        response = self.client.get('/entries?edit=8')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('INVOICE-D&amp;R-S', response.text)
        self.invoice_load.assert_not_called()
        self.assertFalse(self.conn.mutations)

    def test_outside_dispatch_post_denies_even_if_posted_channel_is_allowed(self):
        fields = self.hidden('/entries?edit=7')
        self.conn.calls.clear()
        response = self.client.post('/entries/save', data={**fields, 'entry_id': '8',
            'channel_id': '1', 'courier_id': '1', 'courier_docket_no': 'FORGED'}, follow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.conn.mutations)
        self.assertEqual(self.invalidations, 0)

    def test_outside_create_cannot_write_customer_or_invoice(self):
        form = self.form(channel_id='3', customer_id='', new_customer='Must not be created')
        response = self.client.post('/entries/save', data=form, follow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.conn.mutations)
        self.assertEqual(self.invalidations, 0)

    def test_allowed_create_saves_in_assigned_channel(self):
        response = self.client.post('/entries/save', data=self.form(), follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[-1000:])
        self.assertEqual(self.conn.saved['channel_id'], 1)
        self.assertEqual(self.invalidations, 1)

    def test_submission_replay_cannot_reveal_outside_channel(self):
        form = self.form()
        self.conn.submitted = self.conn.records[8]
        response = self.client.post('/entries/save', data=form, follow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.conn.mutations)
        self.assertEqual(self.invalidations, 0)

    def test_allowed_submission_replay_returns_existing_entry_without_write(self):
        self.conn.submitted = self.conn.records[7]
        response = self.client.post('/entries/save', data=self.form(), follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[-1000:])
        self.assertIn('saved=7', response.headers['location'])
        self.assertIn('replayed=1', response.headers['location'])
        self.assertFalse(self.conn.mutations)

    def test_create_grants_do_not_unlock_existing_sales_fields(self):
        self.assertTrue(entry.may_create(self.user))
        self.assertTrue(entry.limited_dispatch_only(self.user))
        self.assertFalse(auth.can(self.user, 'sales.edit'))
        form = self.hidden('/entries?edit=7')
        response = self.client.post('/entries/save', data={**form,
            'invoice_no': 'FORGED', 'channel_id': '3', 'new_customer': 'FORGED',
            'primary_taxable': '999', 'stock_supplied_on': '12-04-2026',
            'courier_id': '1', 'courier_docket_no': 'NEW-AWB', 'weight_kg': '5',
            'transport_service': 'Airway'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[-1000:])
        self.assertEqual(self.conn.saved['courier_docket_no'], 'NEW-AWB')
        self.assertEqual(set(self.conn.saved), {*entry.COURIER_FIELDS, 'stock_supplied_on', 'updated_by'})
        self.assertEqual(self.conn.records[7]['invoice_no'], 'INVOICE-LFO')
        self.assertEqual(self.conn.records[7]['channel_id'], 1)
        self.assertFalse(any(q.startswith('insert into master.customer') for q, a in self.conn.mutations))

    def test_missing_legacy_policy_and_admin_preserve_existing_access(self):
        legacy = account()
        legacy.pop('dashboard_access_json')
        self.assertIsNone(entry.entry_channels(legacy))
        self.assertIsNone(entry.entry_channels(None))
        self.assertIsNone(entry.entry_channels(account(is_admin=True)))
        self.assertIsNone(entry.entry_channels(account(None)))
        for user in (legacy, account(is_admin=True), account(None)):
            with self.subTest(user=user.get('is_admin')):
                where, args = entry.ledger_query({}, user)
                self.assertNotIn('regexp_replace', where)

    def test_empty_or_malformed_policies_fail_closed_even_without_dashboard_grant(self):
        for stored in (json.dumps(dp.deny_policy()), '{bad-json', 'null'):
            self.user = account(dashboard_access_json=stored,
                                permissions_json=json.dumps([p for p in GRANTS if p != 'dashboard.view']))
            with self.subTest(stored=stored):
                self.assertEqual(entry.entry_channels(self.user), [])
                response = self.client.get('/entries?edit=7')
                self.assertEqual(response.status_code, 403)
                self.assertFalse(self.conn.mutations)

    def test_new_accounts_cannot_delete_or_export_without_separate_grants(self):
        for path in ('/entries?delete=7', '/entries/export.csv'):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post('/entries/delete', data={'entry_id': '7'}).status_code, 403)
        self.assertFalse(self.conn.mutations)

    def test_delete_helper_honors_scope_when_independent_delete_grant_exists(self):
        with self.assertRaises(entry.EntryAccessError):
            entry.delete_entry(8, self.conn.records[8]['updated_at'].isoformat(), 'operator-north', self.user)
        self.assertFalse(self.conn.mutations)


if __name__ == '__main__':
    unittest.main()
