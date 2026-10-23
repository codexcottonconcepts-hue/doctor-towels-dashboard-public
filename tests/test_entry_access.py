"""Sales access regression tests using a fake connection; never writes business data."""
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

import entry_web


def account(*permissions):
    return {'username': 'limited-operator', 'is_admin': False,
            'permissions_json': json.dumps(list(permissions))}


def record():
    return {**{key: 1 for key in entry_web.DIMENSIONS},
            **{key: Decimal('0') for key in entry_web.AMOUNTS},
            'entry_id': 7, 'invoice_date': date(2026, 4, 11), 'invoice_no': 'PRIVATE-INVOICE-92',
            'line_no': 3, 'pi_number': None, 'primary_taxable': Decimal('432109.87'),
            'primary_with_tax': Decimal('509889.65'), 'order_type': 'First',
            'stock_supplied_on': date(2026, 4, 12), 'courier_docket_no': 'PUBLIC-TRACKING-51',
            'weight_kg': Decimal('7.125'), 'transport_service': 'Cargo',
            'remarks': 'Delivery notes', 'payment_received_on': None,
            'updated_at': datetime(2026, 9, 23, 10, 11, 12),
            'created_at': datetime(2026, 4, 11, 1, 1, 1),
            'is_deleted': False}


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, args=()):
        self.connection.calls.append((query, args))
        row = self.connection.row
        self.rows = []
        if query.startswith('select * from txn.sales_entry'):
            self.description = [(key,) for key in row]
            self.rows = [tuple(row.values())] if not row['is_deleted'] else []
        elif query.startswith('select count(*)'):
            self.rows = [(1, row['primary_taxable'], row['primary_with_tax'], Decimal(10))]
        elif ' from rpt.v_sales' in query:
            columns = query.split(' from ')[0].removeprefix('select ').split(',')
            display = {**row, 'customer': 'PRIVATE-CUSTOMER-72', 'channel': 'PRIVATE-CHANNEL-38',
                       'sub_channel': 'PRIVATE-SUB-42', 'sales_person': 'PRIVATE-PERSON-16',
                       'brand': 'PRIVATE-BRAND-26', 'state': 'PRIVATE-STATE-81',
                       'district': 'PRIVATE-DISTRICT-71', 'city': 'PRIVATE-CITY-31', 'courier': 'DTDC',
                       'credit_days': 45}
            self.description = [(key,) for key in columns]
            self.rows = [tuple(display[key] for key in columns)]
        elif query.startswith('select coalesce(updated_at,created_at)'):
            self.rows = [(row['updated_at'],)] if not row['is_deleted'] else []
        elif query.startswith('select state_id,district_id'):
            self.rows = [(1, 1)]
        elif query.startswith('select code from master.courier'):
            self.rows = [('DTDC',)]
        elif ' from master.' in query:
            self.rows = [(1,)]
        elif query.startswith('update txn.sales_entry set is_deleted=true'):
            self.connection.deleted = True
            row['is_deleted'] = True
        elif query.startswith('update txn.sales_entry set '):
            setters = query.split(' set ', 1)[1].split(' where ', 1)[0].split(',')
            columns = [part.split('=')[0] for part in setters if '=%s' in part]
            self.connection.saved = {key: row[key] for key in entry_web.COLUMNS}
            self.connection.saved.update(zip(columns, args))
            self.rows = [(row['entry_id'],)]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Connection:
    def __init__(self):
        self.row = record()
        self.calls = []
        self.saved = None
        self.deleted = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return Cursor(self)

    def close(self):
        pass


class EntryAccessTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='isolated-sales-access-test')
        self.user = account()
        self.invalidations = 0
        def invalidate():
            self.invalidations += 1
        entry_web.register(app, lambda request: self.user, lambda request: None,
                           lambda title, body, user: body, invalidate)
        self.connection = Connection()
        options = {key: [(1, 'PRIVATE-OPTION-53', 1, 1)] for key in entry_web.DIMENSIONS}
        options['courier_id'] = [(1, 'DTDC')]
        for context in (patch.object(entry_web.pgdata, 'enabled', return_value=True),
                        patch.object(entry_web.pgdata, 'connect', return_value=self.connection),
                        patch.object(entry_web.invoice_items, 'catalog_options', return_value=[]),
                        patch.object(entry_web.invoice_items, 'colour_options', return_value=[]),
                        patch.object(entry_web, 'load_options', return_value=options)):
            context.start()
            self.addCleanup(context.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def form_fields(self, path='/entries?edit=7'):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text[-500:])
        return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', response.text))

    def test_direct_endpoints_deny_without_grants_before_database_access(self):
        for path in ('/entries', '/entries?view=new', '/entries?edit=7',
                     '/entries?delete=7', '/entries/export.csv'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post('/entries/save', data={}).status_code, 403)
        self.assertEqual(self.client.post('/entries/save', data={'entry_id': '7'}).status_code, 403)
        self.assertEqual(self.client.post('/entries/delete', data={'entry_id': '7'}).status_code, 403)
        self.assertEqual(self.connection.calls, [])

    def test_dispatch_register_and_export_never_render_forbidden_sections(self):
        self.user = account('registers.view', 'sales.dispatch.view', 'exports.access')
        for path in ('/entries', '/entries?edit=7', '/entries/export.csv'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('PUBLIC-TRACKING-51', response.text)
                for private in ('PRIVATE-INVOICE-92', 'PRIVATE-CUSTOMER-72', 'PRIVATE-OPTION-53',
                                'PRIVATE-CHANNEL-38', 'PRIVATE-STATE-81', '432109.87', '4,32,109.87'):
                    self.assertNotIn(private, response.text)
                self.assertNotIn('Save changes', response.text)
        exported = list(csv.DictReader(io.StringIO(self.client.get('/entries/export.csv').text.lstrip('\ufeff'))))
        self.assertEqual(set(exported[0]), set(entry_web.EXPORT_FIELDS['dispatch']))

    def test_export_needs_both_register_and_export_rights(self):
        for rights in (('registers.view',), ('exports.access',)):
            self.user = account(*rights)
            self.assertEqual(self.client.get('/entries/export.csv').status_code, 403)

    def test_hidden_invoice_filters_are_rejected_and_search_only_uses_dispatch(self):
        self.user = account('registers.view', 'sales.dispatch.view', 'exports.access')
        for path in ('/entries?from=11-04-2026', '/entries/export.csv?to=12-04-2026'):
            self.assertEqual(self.client.get(path).status_code, 400)
        self.assertEqual(self.client.get('/entries?q=PRIVATE-INVOICE-92').status_code, 200)
        query = next(q for q, args in reversed(self.connection.calls) if ' from rpt.v_sales' in q)
        condition = query.split(' where ')[1].split(' order by ')[0]
        self.assertIn('courier_docket_no ilike %s', condition)
        self.assertNotIn('invoice_no', condition)
        self.assertNotIn('customer', condition)

    def test_create_prerequisites_and_create_without_register(self):
        self.user = account('sales.create', 'sales.dispatch.edit')
        response = self.client.get('/entries?view=new')
        self.assertEqual(response.status_code, 403)
        self.assertIn('Creating a sales entry requires', response.text)
        self.user = account('sales.create', *(f'sales.{s}.edit' for s in entry_web.CREATE_SECTIONS))
        response = self.client.get('/entries?view=new')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="ledger"', response.text)
        self.assertNotIn('Sales totals', response.text)
        self.assertNotIn('Dispatch details', response.text)
        self.assertFalse(any('rpt.v_sales' in q for q, _ in self.connection.calls))
        self.assertEqual(self.client.get('/entries/export.csv').status_code, 403)

    def test_dispatch_edit_preserves_all_other_sections_despite_forged_post(self):
        self.user = account('registers.view', 'sales.edit', 'sales.dispatch.edit')
        form = self.form_fields()
        forged = {**form, 'invoice_no': 'FORGED-INVOICE', 'customer_id': '999',
                  'channel_id': '999', 'brand_id': '999', 'state_id': '999',
                  'primary_taxable': '1', 'primary_with_tax': '1',
                  'stock_supplied_on': '12-04-2026', 'courier_id': '1',
                  'courier_docket_no': 'NEW-TRACKING-99', 'remarks': 'Edited dispatch'}
        response = self.client.post('/entries/save', data=forged, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[-500:])
        self.assertEqual(self.connection.saved['courier_docket_no'], 'NEW-TRACKING-99')
        for section in ('invoice', 'classification', 'location', 'values'):
            for key in entry_web.SECTION_FIELDS[section]:
                if key != 'new_customer':
                    self.assertEqual(self.connection.saved[key], self.connection.row[key], key)
        self.assertEqual(self.invalidations, 1)
        self.assertTrue(any(q.endswith('for update') for q, _ in self.connection.calls))

    def test_global_edit_and_section_edit_are_both_required(self):
        for rights in (('sales.edit', 'sales.dispatch.view'), ('sales.dispatch.edit',)):
            self.user = account('registers.view', *rights)
            form = self.form_fields()
            self.assertEqual(self.client.post('/entries/save', data=form).status_code, 403)
        self.assertIsNone(self.connection.saved)

    def test_full_register_view_with_only_courier_editing(self):
        self.user = account('sales.courier.edit', *(f'sales.{s}.view' for s in entry_web.SECTION_FIELDS))
        response = self.client.get('/entries')
        self.assertEqual(response.status_code, 200)
        for value in ('PRIVATE-INVOICE-92', 'PRIVATE-CUSTOMER-72', 'PRIVATE-STATE-81', '4,32,109.87', 'Edit dispatch'):
            self.assertIn(value, response.text)
        for path in ('/entries?view=new', '/entries?delete=7', '/entries/export.csv'):
            self.assertEqual(self.client.get(path).status_code, 403)
        response = self.client.get('/entries?edit=7')
        self.assertIn('Save dispatch details', response.text)
        self.assertIn('PRIVATE-INVOICE-92', response.text)
        for field in ('invoice_no', 'invoice_date', 'customer_id', 'channel_id', 'state_id',
                      'primary_taxable', 'stock_supplied_on', 'remarks'):
            self.assertNotRegex(response.text, r'<(?:input|select|textarea)\b[^>]*\bid="' + field + '"')
        self.assertNotIn('view only', response.text.lower())
        self.assertNotIn('Courier fields only', response.text)

    def test_courier_only_update_ignores_every_other_posted_field(self):
        # A section edit without the general Edit grant must not be activated
        # accidentally by the independent courier permission.
        self.user = account('sales.courier.edit', 'sales.invoice.edit')
        form = self.form_fields()
        response = self.client.post('/entries/save', data={**form,
            'invoice_date': '01-01-2025', 'invoice_no': 'FORGED', 'line_no': '99',
            'customer_id': '999', 'new_customer': 'FORGED', 'brand_id': '999',
            'primary_taxable': '1', 'state_id': '999', 'stock_supplied_on': '01-01-2025',
            'remarks': 'FORGED', 'courier_id': '1', 'courier_docket_no': '000012345'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[-500:])
        self.assertEqual(self.connection.saved['courier_docket_no'], '000012345')
        for key in entry_web.COLUMNS:
            if key not in entry_web.COURIER_FIELDS:
                self.assertEqual(self.connection.saved[key], self.connection.row[key], key)
        updates = [q for q, args in self.connection.calls if q.startswith('update txn.sales_entry')]
        self.assertEqual(len(updates), 1)
        assigned = updates[0].split(' set ', 1)[1].split(' where ', 1)[0]
        self.assertEqual({part.split('=')[0] for part in assigned.split(',')},
                         {*entry_web.COURIER_FIELDS, 'updated_by', 'updated_at'})
        self.assertEqual(self.connection.saved['weight_kg'], self.connection.row['weight_kg'])
        self.assertEqual(self.connection.saved['transport_service'], self.connection.row['transport_service'])
        self.assertEqual(self.invalidations, 1)
        self.assertFalse(any(q.startswith('insert into') for q, _ in self.connection.calls))

    def test_courier_only_requires_csrf_revision_and_valid_courier(self):
        self.user = account('sales.courier.edit')
        form = {**self.form_fields(), 'courier_id': '1', 'courier_docket_no': '000012345'}
        self.assertEqual(self.client.post('/entries/save', data={**form, 'csrf_token': 'wrong'}).status_code, 403)
        for changes in ({'revision': 'stale'}, {'courier_id': '-1'},
                        {'courier_id': ''}, {'courier_docket_no': 'X' * 121}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post('/entries/save', data={**form, **changes}).status_code, 400)
        self.assertIsNone(self.connection.saved)
        self.assertEqual(self.client.post('/entries/delete', data={**form, 'confirm': 'yes'}).status_code, 403)
        self.assertEqual(self.client.post('/entries/save', data={**form, 'entry_id': ''}).status_code, 403)

    def test_mixed_courier_and_invoice_edit_does_not_unlock_dispatch_dates(self):
        self.user = account('sales.edit', 'sales.invoice.edit', 'sales.courier.edit')
        form = self.form_fields()
        response = self.client.post('/entries/save', data={**form,
            'invoice_date': '11-04-2026', 'invoice_no': self.connection.row['invoice_no'],
            'customer_id': '1', 'courier_id': '1', 'courier_docket_no': '0000999',
            'stock_supplied_on': '01-01-2025', 'remarks': 'FORGED'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[-500:])
        self.assertEqual(self.connection.saved['stock_supplied_on'], self.connection.row['stock_supplied_on'])
        self.assertEqual(self.connection.saved['remarks'], self.connection.row['remarks'])
        self.assertEqual(self.connection.saved['courier_docket_no'], '0000999')

    def test_date_and_courier_permissions_enable_only_the_granted_dispatch_fields(self):
        self.user = account('sales.stock_supplied.edit', 'sales.courier.edit',
                            *(f'sales.{s}.view' for s in entry_web.SECTION_FIELDS))
        response = self.client.get('/entries?edit=7')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Save dispatch details', response.text)
        stock = re.search(r'<input id="stock_supplied_on"[^>]*>', response.text).group()
        self.assertNotIn('disabled', stock)
        self.assertNotRegex(response.text, r'<textarea\b[^>]*\bid="remarks"')
        self.assertNotIn('view only', response.text.lower())
        form = self.form_fields()
        response = self.client.post('/entries/save', data={**form, 'stock_supplied_on': '15-04-2026',
            'courier_id': '1', 'courier_docket_no': '000042', 'remarks': 'FORGED',
            'invoice_no': 'FORGED', 'primary_taxable': '1'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[-500:])
        self.assertEqual(self.connection.saved['stock_supplied_on'], date(2026, 4, 15))
        self.assertEqual(self.connection.saved['courier_docket_no'], '000042')
        for key in entry_web.COLUMNS:
            if key not in (*entry_web.COURIER_FIELDS, 'stock_supplied_on'):
                self.assertEqual(self.connection.saved[key], self.connection.row[key], key)

    def test_stock_date_permission_does_not_grant_courier_or_other_edits(self):
        self.user = account('sales.stock_supplied.edit')
        response = self.client.get('/entries?edit=7')
        self.assertNotRegex(response.text, r'<select\b[^>]*\bid="courier_id"')
        self.assertNotRegex(response.text, r'<input\b[^>]*\bid="courier_docket_no"')
        form = self.form_fields()
        response = self.client.post('/entries/save', data={**form, 'stock_supplied_on': '15-04-2026',
            'courier_id': '-999', 'courier_docket_no': 'FORGED', 'remarks': 'FORGED'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        for key in entry_web.COLUMNS:
            expected = date(2026, 4, 15) if key == 'stock_supplied_on' else self.connection.row[key]
            self.assertEqual(self.connection.saved[key], expected, key)

    def test_stock_date_validation_payment_order_and_revision(self):
        self.user = account('sales.stock_supplied.edit')
        self.connection.row['payment_received_on'] = date(2026, 4, 16)
        form = self.form_fields()
        self.assertEqual(self.client.post('/entries/save', data={**form, 'csrf_token': 'bad'}).status_code, 403)
        for changes in ({'stock_supplied_on': '31-04-2026'}, {'stock_supplied_on': '29-02-2026'},
                        {'stock_supplied_on': '17-04-2026'}, {'revision': 'stale', 'stock_supplied_on': '15-04-2026'}):
            with self.subTest(changes=changes):
                response = self.client.post('/entries/save', data={**form, **changes})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('16-04-2026', response.text)
        self.assertIsNone(self.connection.saved)
        self.assertEqual(self.client.get('/entries?view=new').status_code, 403)
        self.assertEqual(self.client.post('/entries/delete', data={**form, 'confirm': 'yes'}).status_code, 403)

    def test_stock_date_omission_preserves_date_and_explicit_blank_is_rejected(self):
        self.user = account('sales.stock_supplied.edit')
        form = self.form_fields()
        response = self.client.post('/entries/save', data=form, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.connection.saved['stock_supplied_on'], self.connection.row['stock_supplied_on'])
        self.connection.saved = None
        response = self.client.post('/entries/save', data={**form, 'stock_supplied_on': ''}, follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.connection.saved)

    def test_dispatch_editor_gets_invoice_context_without_invoice_controls(self):
        self.user = account('registers.view', 'sales.edit', 'sales.dispatch.edit', 'sales.invoice.view')
        response = self.client.get('/entries?edit=7')
        self.assertNotRegex(response.text, r'<input\b[^>]*\bid="invoice_no"')
        self.assertNotRegex(response.text, r'<select\b[^>]*\bid="customer_id"')
        self.assertIn('PRIVATE-INVOICE-92', response.text)
        self.assertNotIn('primary_taxable', response.text)

    def test_save_requires_csrf_and_current_revision(self):
        self.user = account('sales.edit', 'sales.dispatch.edit')
        fields = self.form_fields()
        self.assertEqual(self.client.post('/entries/save', data={**fields, 'csrf_token': 'forged'}).status_code, 403)
        response = self.client.post('/entries/save', data={**fields, 'revision': 'stale'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Another user changed', response.text)
        self.assertIsNone(self.connection.saved)

    def test_delete_confirmation_csrf_revision_and_soft_delete(self):
        self.user = account('registers.view', 'sales.delete')
        response = self.client.get('/entries?delete=7')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('PRIVATE-INVOICE-92', response.text)
        fields = self.form_fields('/entries?delete=7')
        self.assertEqual(self.client.post('/entries/delete', data={**fields, 'csrf_token': 'bad', 'confirm': 'yes'}).status_code, 403)
        self.assertEqual(self.client.post('/entries/delete', data=fields).status_code, 400)
        self.assertEqual(self.client.post('/entries/delete', data={**fields, 'confirm': 'yes', 'revision': 'stale'}).status_code, 400)
        self.assertFalse(self.connection.deleted)
        response = self.client.post('/entries/delete', data={**fields, 'confirm': 'yes'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(self.connection.deleted)
        self.assertEqual(self.invalidations, 1)
        self.assertTrue(any(q.startswith("select set_config('app.username'") and args == ('limited-operator',)
                            for q, args in self.connection.calls))
        self.assertFalse(any(q.lower().startswith('delete from') for q, _ in self.connection.calls))


if __name__ == '__main__':
    unittest.main()
