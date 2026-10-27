"""Customer profile validation and opt-in transactional PostgreSQL regressions.

Unit tests never open a database. The integration suite requires an explicitly
prepared localhost *_test database in CUSTOMER_DETAILS_TEST_DATABASE_URL. Each
test rolls its customer/geography/ledger fixtures back; no live DSN is read.
"""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extensions import parse_dsn
from starlette.middleware.sessions import SessionMiddleware

import customer_details as customer
import entry_web


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'pgdb' / 'sql' / '22_customer_details.sql'


def details(**changes):
    return customer.parse_fields({
        'customer_address': "12 Cotton Road\nSecond floor, O'Neil & Co",
        'customer_location': 'Near central market',
        'customer_gstin': '33ABCDE1234F1Z5',
        **changes,
    })


class CustomerValidationTests(unittest.TestCase):
    def test_optional_details_remain_null_and_do_not_invent_a_tax_registration(self):
        for form in ({}, dict.fromkeys(customer.PROFILE_FIELDS, ''),
                     dict.fromkeys(customer.PROFILE_FIELDS, ' \t\n ')):
            with self.subTest(form=form):
                self.assertEqual(customer.parse_fields(form),
                                 {'address': None, 'location': None, 'gstin': None})

    def test_text_is_trimmed_but_multiline_address_and_punctuation_are_preserved(self):
        parsed = details(customer_address="  12 Cotton Road\nSecond floor, O'Neil & Co  ",
                         customer_location='  Near central market  ',
                         customer_gstin='  33abcde1234f1z5  ')
        self.assertEqual(parsed['address'], "12 Cotton Road\nSecond floor, O'Neil & Co")
        self.assertEqual(parsed['location'], 'Near central market')
        self.assertEqual(parsed['gstin'], '33ABCDE1234F1Z5')

    def test_maximum_address_and_location_lengths_are_accepted_exactly(self):
        self.assertEqual(len(details(customer_address='A' * 2000)['address']), 2000)
        self.assertEqual(len(details(customer_location='L' * 200)['location']), 200)
        for field, length in (('customer_address', 2001), ('customer_location', 201)):
            with self.subTest(field=field), self.assertRaises(customer.CustomerError):
                details(**{field: 'X' * length})

    def test_malformed_gstin_never_reaches_database_or_silently_becomes_blank(self):
        for gstin in ('33ABCDE1234F1Z', '33ABCDE1234F1Z55', 'XXABCDE1234F1Z5',
                      '33ABCD11234F1Z5', '33ABCDE1234F1A5', '33 ABCDE1234F1Z5',
                      '<script>GST</script>', "'; drop table master.customer;--"):
            with self.subTest(gstin=gstin), self.assertRaises(customer.CustomerError):
                details(customer_gstin=gstin)

    def test_unsupported_control_characters_are_rejected_in_all_customer_text_fields(self):
        for field in customer.PROFILE_FIELDS:
            for control in ('\x00', '\x08', '\x1f'):
                with self.subTest(field=field, control=repr(control)), self.assertRaises(customer.CustomerError):
                    details(**{field: 'Customer' + control + 'text'})

    def test_invalid_geography_identifiers_are_rejected_before_database_queries(self):
        class NoDatabaseAccess:
            def execute(self, *_args):
                raise AssertionError('Malformed geography must be rejected before database access.')
        for identifier in (None, '', 0, -1, True, '1.5', '2147483648', '1 OR 1=1'):
            with self.subTest(identifier=identifier), self.assertRaises(customer.CustomerError):
                customer.validate_geography(NoDatabaseAccess(), {'state_id': identifier})


def account(*permissions):
    return {'username': 'customer-editor', 'is_admin': False,
            'permissions_json': json.dumps(list(permissions))}


class CustomerRouteTests(unittest.TestCase):
    def setUp(self):
        self.user = account('sales.edit', 'sales.invoice.edit', 'sales.location.edit')
        self.connections = []
        self.profile = {'customer_id': 7, 'name': 'Profile test customer',
                        'address': '12 Cotton Road', 'location': 'Central market',
                        'gstin': '33ABCDE1234F1Z5', 'state_id': 1, 'district_id': 2,
                        'city_id': 3, 'state': 'Test state', 'district': 'Test district',
                        'city': 'Test city', 'revision': '2026-10-27T10:20:30+00:00'}
        suite = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Connection:
            def __init__(self):
                self.cursor_object = Cursor()
                self.closed = False
                self.transaction_error = None

            def cursor(self):
                return self.cursor_object

            def __enter__(self):
                return self

            def __exit__(self, kind, *_args):
                self.transaction_error = kind
                return False

            def close(self):
                self.closed = True

        def connect():
            connection = Connection()
            suite.connections.append(connection)
            return connection

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='isolated-customer-profile-routes')
        entry_web.register(app, lambda _request: self.user, lambda _request: None,
                           lambda _title, body, _user: body, lambda: None)

        @app.get('/customer-test-token')
        def token(request: Request):
            return {'csrf_token': entry_web.csrf_token(request)}

        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for context in (patch.object(entry_web.pgdata, 'connect', side_effect=connect),
                        patch.object(entry_web, 'entry_channels', return_value=['LFO']),
                        patch.object(customer, 'get_profile', return_value=self.profile),
                        patch.object(customer, 'update_customer', return_value=self.profile),
                        patch.object(entry_web, 'save_entry')):
            mocked = context.start()
            self.addCleanup(context.stop)
            if context.attribute == 'get_profile':
                self.read_profile = mocked
            elif context.attribute == 'update_customer':
                self.update_profile = mocked
            elif context.attribute == 'save_entry':
                self.save_entry = mocked

    def form(self, **changes):
        token = self.client.get('/customer-test-token').json()['csrf_token']
        return {'csrf_token': token, 'revision': self.profile['revision'],
                'customer_address': 'Updated billing address', 'customer_location': 'Near office',
                'customer_gstin': '33abcde1234f1z5', 'state_id': '1', 'district_id': '2',
                'city_id': '3', **changes}

    def test_unauthenticated_profile_read_and_write_do_not_open_a_database(self):
        self.user = None
        self.assertEqual(self.client.get('/entries/customers/7').status_code, 401)
        self.assertEqual(self.client.post('/entries/customers/7', data={}).status_code, 401)
        self.assertEqual(self.connections, [])
        self.update_profile.assert_not_called()

    def test_dispatch_and_ungranted_users_cannot_read_or_update_customer_profiles(self):
        for permissions in ((), ('sales.courier.edit',),
                            ('sales.edit', 'sales.dispatch.edit'),
                            ('sales.edit', 'sales.invoice.edit'),
                            ('sales.edit', 'sales.location.edit')):
            with self.subTest(permissions=permissions):
                self.user = account(*permissions)
                self.assertEqual(self.client.get('/entries/customers/7').status_code, 403)
                self.assertEqual(self.client.post('/entries/customers/7', data={}).status_code, 403)
        self.assertEqual(self.connections, [])
        self.read_profile.assert_not_called()
        self.update_profile.assert_not_called()

    def test_profile_view_rights_do_not_allow_shared_customer_edits(self):
        self.user = account('registers.view', 'sales.invoice.view', 'sales.location.view')
        response = self.client.get('/entries/customers/7')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['customer'], self.profile)
        count = len(self.connections)
        self.assertEqual(self.client.post('/entries/customers/7', data=self.form()).status_code, 403)
        self.assertEqual(len(self.connections), count)
        self.update_profile.assert_not_called()

    def test_courier_edit_does_not_promote_section_grants_to_customer_edit_access(self):
        self.user = account('sales.courier.edit', 'sales.invoice.edit', 'sales.location.edit')
        self.assertFalse(entry_web.may_manage_customer(self.user))
        response = self.client.post('/entries/customers/7', data=self.form())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.connections, [])
        self.read_profile.assert_not_called()
        self.update_profile.assert_not_called()
        self.save_entry.assert_not_called()

    def test_customer_read_passes_assigned_channel_scope_and_disables_caching(self):
        response = self.client.get('/entries/customers/7')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        self.assertEqual(response.json()['customer']['gstin'], '33ABCDE1234F1Z5')
        self.read_profile.assert_called_once_with(self.connections[0].cursor_object, 7, channels=['LFO'])
        self.assertTrue(self.connections[0].closed)
        self.save_entry.assert_not_called()

    def test_unknown_or_out_of_scope_customer_returns_no_profile_details(self):
        self.read_profile.side_effect = customer.CustomerError('This customer is unavailable or outside your assigned channels.')
        response = self.client.get('/entries/customers/7')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('customer', response.json())
        self.assertNotIn(self.profile['address'], response.text)

    def test_customer_update_requires_csrf_before_opening_a_database(self):
        for posted in ({}, self.form(csrf_token='wrong')):
            with self.subTest(posted=posted):
                self.assertEqual(self.client.post('/entries/customers/7', data=posted).status_code, 403)
        self.assertEqual(self.connections, [])
        self.update_profile.assert_not_called()

    def test_explicit_profile_update_has_its_own_transaction_and_never_saves_an_invoice(self):
        response = self.client.post('/entries/customers/7', data=self.form())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['customer'], self.profile)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        self.update_profile.assert_called_once_with(self.connections[0].cursor_object, 7,
            {'address': 'Updated billing address', 'location': 'Near office', 'gstin': '33ABCDE1234F1Z5'},
            {'state_id': '1', 'district_id': '2', 'city_id': '3'},
            self.profile['revision'], 'customer-editor', channels=['LFO'])
        self.assertTrue(self.connections[0].closed)
        self.assertIsNone(self.connections[0].transaction_error)
        self.save_entry.assert_not_called()

    def test_invalid_profile_fields_do_not_call_update_and_transaction_is_rolled_back(self):
        response = self.client.post('/entries/customers/7', data=self.form(customer_gstin='invalid'))
        self.assertEqual(response.status_code, 400)
        self.update_profile.assert_not_called()
        self.assertIs(self.connections[0].transaction_error, customer.CustomerError)
        self.save_entry.assert_not_called()

    def test_stale_profile_error_is_actionable_and_never_falls_back_to_invoice_save(self):
        conflict = customer.CustomerError('Another user changed this customer. Reload its details before saving again.')
        conflict.status_code = 409
        self.update_profile.side_effect = conflict
        response = self.client.post('/entries/customers/7', data=self.form())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('Reload', response.json()['message'])
        self.assertIs(self.connections[0].transaction_error, customer.CustomerError)
        self.save_entry.assert_not_called()


@unittest.skipUnless(os.environ.get('CUSTOMER_DETAILS_TEST_DATABASE_URL'),
                     'Set CUSTOMER_DETAILS_TEST_DATABASE_URL to a prepared localhost *_test database')
class CustomerPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['CUSTOMER_DETAILS_TEST_DATABASE_URL']
        parsed = parse_dsn(cls.dsn)
        cls.database = parsed.get('dbname', '')
        if not cls.database.endswith('_test') or parsed.get('host') not in ('localhost', '127.0.0.1', '::1'):
            raise RuntimeError('Customer tests require an explicitly named localhost *_test database.')
        connection = psycopg2.connect(cls.dsn)
        try:
            connection.autocommit = True
            with connection.cursor() as cur:
                cur.execute("select current_database(), to_regclass('txn.sales_entry'), to_regclass('master.customer')")
                database, ledger, customers = cur.fetchone()
                if database != cls.database or not ledger or not customers:
                    raise RuntimeError('Prepare the isolated application schema before customer tests.')
                cur.execute('select count(*), coalesce(sum(primary_taxable),0), coalesce(sum(primary_with_tax),0) from txn.sales_entry')
                before = cur.fetchone()
                for _ in range(2):
                    cur.execute(MIGRATION.read_text(encoding='utf-8'))
                cur.execute('select count(*), coalesce(sum(primary_taxable),0), coalesce(sum(primary_with_tax),0) from txn.sales_entry')
                if cur.fetchone() != before:
                    raise AssertionError('Applying customer details migration changed primary sales.')
        finally:
            connection.close()

    def setUp(self):
        self.connection = psycopg2.connect(self.dsn)
        self.cur = self.connection.cursor()
        self.tag = uuid4().hex
        self.cur.execute('select count(*) from txn.sales_entry')
        self.ledger_before = self.cur.fetchone()[0]
        self.state = self.insert('master.state', 'state_id', name='Customer test state ' + self.tag)
        self.other_state = self.insert('master.state', 'state_id', name='Other test state ' + self.tag)
        self.district = self.insert('master.district', 'district_id', state_id=self.state,
                                    name='Customer test district ' + self.tag)
        self.other_district = self.insert('master.district', 'district_id', state_id=self.other_state,
                                          name='Other test district ' + self.tag)
        self.city = self.insert('master.city', 'city_id', state_id=self.state,
                                district_id=self.district, name='Customer test city ' + self.tag)
        self.other_city = self.insert('master.city', 'city_id', state_id=self.other_state,
                                      district_id=self.other_district, name='Other test city ' + self.tag)
        self.geo = {'state_id': self.state, 'district_id': self.district, 'city_id': self.city}
        self.name = 'Customer details test ' + self.tag

    def tearDown(self):
        try:
            self.connection.rollback()
        finally:
            self.cur.close()
            self.connection.close()

    def insert(self, table, identifier, **values):
        # All SQL identifiers in this test helper are local constants; fixture
        # values, including apostrophes, always use driver parameters.
        self.cur.execute('insert into ' + table + '(' + ','.join(values) + ') values (' +
                         ','.join(['%s'] * len(values)) + ') returning ' + identifier,
                         tuple(values.values()))
        return self.cur.fetchone()[0]

    def create_customer(self, name=None, **changes):
        return customer.create_customer(self.cur, name or self.name, details(**changes),
                                        self.geo, None, 'customer-test')

    def profile(self, identifier, channels=None, **kwargs):
        return customer.get_profile(self.cur, identifier, channels=channels, **kwargs)

    def assert_no_invoice_created(self):
        self.cur.execute('select count(*) from txn.sales_entry')
        self.assertEqual(self.cur.fetchone()[0], self.ledger_before,
                         'Saving customer details must not create an invoice.')

    def add_sale(self, identifier, channel, *, deleted=False):
        self.cur.execute('select channel_id from master.channel where lower(code)=lower(%s)', (channel,))
        found = self.cur.fetchone()
        channel_id = found[0] if found else self.insert('master.channel', 'channel_id', code=channel, name=channel)
        self.cur.execute('insert into master.financial_year(fy,label,start_date,end_date) '
                         "values (2026,'2026-27','2026-04-01','2027-03-31') on conflict (fy) do nothing")
        brand_id = self.insert('master.brand', 'brand_id', code='CUSTOMER-' + uuid4().hex,
                               name='Customer test brand ' + uuid4().hex)
        self.insert('txn.sales_entry', 'entry_id', fy=2026, invoice_date='2026-10-27',
                    invoice_no='CUSTOMER-TEST-' + uuid4().hex, customer_id=identifier,
                    channel_id=channel_id, brand_id=brand_id, state_id=self.state,
                    city_id=self.city, district_id=self.district, is_deleted=deleted)

    def test_create_then_explicit_update_saves_profile_without_an_invoice(self):
        identifier = self.create_customer()
        first = self.profile(identifier)
        self.assertEqual(first['customer_id'], identifier)
        self.assertEqual(first['name'], self.name)
        self.assertEqual(first['address'], details()['address'])
        self.assertEqual(first['location'], 'Near central market')
        self.assertEqual(first['gstin'], '33ABCDE1234F1Z5')
        for field in ('state_id', 'district_id', 'city_id'):
            self.assertEqual(first[field], self.geo[field])
        self.assertTrue(first['revision'])
        edited = customer.update_customer(self.cur, identifier,
            details(customer_address='Revised address', customer_gstin=''), self.geo,
            first['revision'], 'customer-test')
        self.assertEqual(edited['customer_id'], identifier)
        saved = self.profile(identifier)
        self.assertEqual(saved['address'], 'Revised address')
        self.assertIsNone(saved['gstin'])
        self.assertEqual(saved['name'], self.name)
        self.assertNotEqual(saved['revision'], first['revision'])
        self.cur.execute("select changed_by,old_row->>'address',new_row->>'address' from sec.audit_log "
                         "where table_name='master.customer' and row_pk=%s and action='UPDATE' "
                         'order by audit_id desc limit 1', (str(identifier),))
        self.assertEqual(self.cur.fetchone(), ('customer-test', first['address'], 'Revised address'))
        self.assert_no_invoice_created()

    def test_stale_customer_revision_does_not_overwrite_the_more_recent_edit(self):
        identifier = self.create_customer()
        first = self.profile(identifier)
        customer.update_customer(self.cur, identifier, details(customer_address='Latest edit'),
                                 self.geo, first['revision'], 'first-editor')
        before = self.profile(identifier)
        with self.assertRaises(customer.CustomerError):
            customer.update_customer(self.cur, identifier, details(customer_address='Stale edit'),
                                     self.geo, first['revision'], 'second-editor')
        self.assertEqual(self.profile(identifier), before)
        self.assert_no_invoice_created()

    def test_case_insensitive_duplicate_customer_does_not_merge_or_replace_details(self):
        identifier = self.create_customer()
        before = self.profile(identifier)
        with self.assertRaises(customer.CustomerError):
            self.create_customer(name=self.name.upper(), customer_address='Must not overwrite')
        self.assertEqual(self.profile(identifier), before)
        self.cur.execute('select count(*) from master.customer where lower(name)=lower(%s)', (self.name,))
        self.assertEqual(self.cur.fetchone()[0], 1)
        self.assert_no_invoice_created()

    def test_inconsistent_geography_is_rejected_without_partial_customer_write(self):
        identifier = self.create_customer()
        before = self.profile(identifier)
        bad_geographies = ({**self.geo, 'state_id': self.other_state},
                           {**self.geo, 'district_id': self.other_district},
                           {**self.geo, 'city_id': self.other_city},
                           {**self.geo, 'state_id': None})
        for geo in bad_geographies:
            with self.subTest(geo=geo), self.assertRaises(customer.CustomerError):
                customer.update_customer(self.cur, identifier, details(customer_address='Must not save'),
                                         geo, before['revision'], 'customer-test')
            self.assertEqual(self.profile(identifier), before)
        self.assert_no_invoice_created()

    def test_empty_channel_scope_denies_profile_read_and_write_even_for_known_id(self):
        identifier = self.create_customer()
        before = self.profile(identifier)
        with self.assertRaises(customer.CustomerError):
            self.profile(identifier, channels=[])
        with self.assertRaises(customer.CustomerError):
            customer.update_customer(self.cur, identifier, details(customer_address='Forbidden'),
                                     self.geo, before['revision'], 'restricted', channels=[])
        self.assertEqual(self.profile(identifier), before)

    def test_address_edit_retains_the_profiles_existing_inactive_city(self):
        identifier = self.create_customer()
        self.cur.execute('update master.city set is_active=false where city_id=%s', (self.city,))
        before = self.profile(identifier)
        edited = customer.update_customer(self.cur, identifier,
            details(customer_address='Address changed without moving customer'), self.geo,
            before['revision'], 'customer-test')
        self.assertEqual(edited['address'], 'Address changed without moving customer')
        self.assertEqual(edited['city_id'], self.city)
        self.assertEqual(edited['district_id'], self.district)
        self.assertEqual(edited['state_id'], self.state)
        self.assert_no_invoice_created()

    def test_new_or_changed_customers_cannot_select_another_inactive_city(self):
        identifier = self.create_customer()
        before = self.profile(identifier)
        self.cur.execute('update master.city set is_active=false where city_id=%s', (self.other_city,))
        other_geo = {'state_id': self.other_state, 'district_id': self.other_district,
                     'city_id': self.other_city}
        with self.assertRaises(customer.CustomerError):
            customer.create_customer(self.cur, 'Inactive city customer ' + self.tag,
                                     details(), other_geo, None, 'customer-test')
        with self.assertRaises(customer.CustomerError):
            customer.update_customer(self.cur, identifier, details(customer_address='Must not move'),
                                     other_geo, before['revision'], 'customer-test')
        self.assertEqual(self.profile(identifier), before)
        self.assert_no_invoice_created()

    def test_retaining_inactive_city_does_not_bypass_its_state_or_district(self):
        identifier = self.create_customer()
        self.cur.execute('update master.city set is_active=false where city_id=%s', (self.city,))
        before = self.profile(identifier)
        for invalid in ({**self.geo, 'state_id': self.other_state},
                        {**self.geo, 'district_id': self.other_district}):
            with self.subTest(geo=invalid), self.assertRaises(customer.CustomerError):
                customer.update_customer(self.cur, identifier, details(), invalid,
                                         before['revision'], 'customer-test')
        self.assertEqual(self.profile(identifier), before)
        self.assert_no_invoice_created()

    def test_profile_read_and_explicit_update_obey_the_customers_sales_channel(self):
        identifier = self.create_customer()
        first = self.profile(identifier)
        # A newly entered customer with no sales is selectable for the first
        # invoice. Once it has history, a direct customer ID cannot bypass scope.
        self.assertEqual(self.profile(identifier, channels=['LFO'])['customer_id'], identifier)
        self.add_sale(identifier, 'D&R-S')
        for inactive in (False, True):
            with self.subTest(include_inactive=inactive), self.assertRaises(customer.CustomerError):
                self.profile(identifier, channels=['LFO'], include_inactive=inactive)
        with self.assertRaises(customer.CustomerError):
            customer.update_customer(self.cur, identifier, details(customer_address='Forbidden'),
                                     self.geo, first['revision'], 'restricted', channels=['LFO'])
        self.assertEqual(self.profile(identifier), first)
        self.assertEqual(self.profile(identifier, channels=['D&R-S'])['customer_id'], identifier)
        self.add_sale(identifier, 'LFO')
        self.assertEqual(self.profile(identifier, channels=['LFO'])['customer_id'], identifier)

    def test_deleted_sales_do_not_turn_a_private_customer_into_an_unused_customer(self):
        identifier = self.create_customer()
        self.add_sale(identifier, 'D&R-S', deleted=True)
        with self.assertRaises(customer.CustomerError):
            self.profile(identifier, channels=['LFO'])

    def test_inactive_customer_can_be_read_only_when_explicitly_requested_and_cannot_be_updated(self):
        identifier = self.create_customer()
        self.cur.execute('update master.customer set is_active=false where customer_id=%s', (identifier,))
        inactive = self.profile(identifier, include_inactive=True)
        with self.assertRaises(customer.CustomerError):
            self.profile(identifier)
        with self.assertRaises(customer.CustomerError):
            customer.update_customer(self.cur, identifier, details(customer_address='Forbidden'),
                                     self.geo, inactive['revision'], 'customer-test')
        self.assertEqual(self.profile(identifier, include_inactive=True), inactive)
        self.assert_no_invoice_created()

    def test_city_resolves_its_district_when_customer_district_is_not_selected(self):
        identifier = self.create_customer()
        first = self.profile(identifier)
        customer.update_customer(self.cur, identifier, details(),
            {**self.geo, 'district_id': None}, first['revision'], 'customer-test')
        self.assertEqual(self.profile(identifier)['district_id'], self.district)
        self.assert_no_invoice_created()

    def test_customer_details_follow_the_callers_transaction_and_can_be_rolled_back(self):
        identifier = self.create_customer()
        first = self.profile(identifier)
        self.cur.execute('savepoint before_customer_update')
        customer.update_customer(self.cur, identifier, details(customer_address='Temporary edit'),
                                 self.geo, first['revision'], 'customer-test')
        self.assertEqual(self.profile(identifier)['address'], 'Temporary edit')
        self.cur.execute('rollback to savepoint before_customer_update')
        self.assertEqual(self.profile(identifier), first)
        self.assert_no_invoice_created()


if __name__ == '__main__':
    unittest.main()
