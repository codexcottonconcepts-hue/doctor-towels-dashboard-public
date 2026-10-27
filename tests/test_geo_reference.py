"""Geography planner and pinned-import boundaries; all small fixtures fictional."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import geo_reference as geo


def reference():
    ref = {
        'metadata': {'snapshot_id': 'fictional-fixture', 'source_date': '2026-10-27',
                     'source_uri': 'https://example.invalid/authority', 'mirror_uri': 'https://example.invalid/mirror',
                     'license_uri': 'https://example.invalid/license'},
        'states': [{'lgd_state_code': 1, 'name': 'Alpha'}, {'lgd_state_code': 35, 'name': 'Islands'}],
        'districts': [{'lgd_district_code': 10, 'lgd_state_code': 1, 'name': 'North'},
                      {'lgd_district_code': 11, 'lgd_state_code': 1, 'name': 'South'},
                      {'lgd_district_code': 350, 'lgd_state_code': 35, 'name': 'Island District'}],
        'ulbs': [], 'aliases': {'state': [], 'district': []},
        'supplements': [{'source_key': 'government-harbor', 'lgd_state_code': 35, 'lgd_district_code': 350,
                         'name': 'Harbor', 'kind': 'government_capital_locality', 'source_uri': 'https://example.invalid/capital'}],
    }
    for code, name, districts in ((100, 'Capital', [10]), (101, 'Twin', [10, 11]), (102, 'State Place', []),
                                  (103, 'Riverside', [10]), (104, 'Riverside', [11]),
                                  (105, 'Same District', [10]), (106, 'Same District', [10])):
        ref['ulbs'].append({'lgd_ulb_code': code, 'lgd_state_code': 1, 'name': name,
                            'local_body_type_code': 'Municipality', 'version': '1', 'district_codes': districts})
    ref['metadata']['expected_counts'] = {'states': 2, 'districts': 3, 'ulbs': 7, 'ulb_variants': 8,
                                         'supplements': 1, 'variants': 9}
    ref['_reference_sha256'] = 'fixture-only'
    return ref


def inventory():
    return {'state': [{'state_id': 1, 'name': 'Alpha', 'zone': 'Existing'}, {'state_id': 35, 'name': 'Foreign'}],
            'district': [{'district_id': 1, 'state_id': 1, 'name': 'North'}],
            'city': [{'city_id': 1, 'state_id': 1, 'district_id': 1, 'name': 'Capital', 'is_active': True}]}


def materialize(ref, inv, plan):
    """Simulate allocated surrogate IDs and immutable reference bindings."""
    inv = deepcopy(inv)
    bindings = {'snapshot': [{'snapshot_id': ref['metadata']['snapshot_id'], 'reference_sha256': ref['_reference_sha256']}],
                'state': [], 'district': [], 'city': [], 'supplement': []}
    state_ids, district_ids = {}, {}
    for kind, code_key in (('state', 'lgd_state_code'), ('district', 'lgd_district_code')):
        pk = kind + '_id'
        for row in plan[kind]:
            master_id = max(item[pk] for item in inv[kind]) + 1 if row['create'] else row['master_id']
            if row['create']:
                created = {pk: master_id, 'name': row['name']}
                if kind == 'district':
                    created['state_id'] = state_ids[row['lgd_state_code']]
                inv[kind].append(created)
            (state_ids if kind == 'state' else district_ids)[row[code_key]] = master_id
            binding = {code_key: row[code_key], pk: master_id}
            if kind == 'district': binding['lgd_state_code'] = row['lgd_state_code']
            bindings[kind].append(binding)
    for kind in ('city', 'supplement'):
        for row in plan[kind]:
            master_id = max(item['city_id'] for item in inv['city']) + 1 if row['create'] else row['master_id']
            if row['create']:
                inv['city'].append({'city_id': master_id, 'name': row['label'], 'state_id': state_ids[row['lgd_state_code']],
                                    'district_id': district_ids.get(row['lgd_district_code']), 'is_active': True})
            binding = {'city_id': master_id, 'master_label': row['label'], 'lgd_state_code': row['lgd_state_code'],
                       'lgd_district_code': row['lgd_district_code']}
            if kind == 'city':
                binding.update(lgd_ulb_code=row['lgd_ulb_code'], district_key=row['lgd_district_code'] or 0)
            else:
                binding['source_key'] = row['source_key']
            bindings[kind].append(binding)
    return inv, bindings


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.ref, self.inv = reference(), inventory()

    def plan(self): return geo.build_plan(self.ref, self.inv)

    def test_approved_pack_counts(self):
        counts = geo.validate_reference(geo.load_reference())
        self.assertEqual(counts, {'states': 36, 'districts': 784, 'ulbs': 5050,
                                 'ulb_variants': 5087, 'supplements': 1, 'variants': 5088})

    def test_lgd_codes_are_not_database_ids(self):
        row = next(row for row in self.plan()['state'] if row['lgd_state_code'] == 35)
        self.assertTrue(row['create']); self.assertNotEqual(row['master_id'], 35)
        self.assertEqual(self.inv['state'][1]['name'], 'Foreign')

    def test_existing_city_reused_only_with_matching_parent(self):
        row = next(row for row in self.plan()['city'] if row['lgd_ulb_code'] == 100)
        self.assertEqual(row['master_id'], 1); self.assertFalse(row['create'])

    def test_inactive_city_preserved_and_not_reactivated(self):
        self.inv['city'][0]['is_active'] = False
        row = next(row for row in self.plan()['city'] if row['lgd_ulb_code'] == 100)
        self.assertTrue(row['create']); self.assertNotEqual(row['label'], 'Capital')
        self.assertFalse(self.inv['city'][0]['is_active'])

    def test_wrong_parent_city_not_reparented(self):
        self.inv['city'][0]['district_id'] = None
        row = next(row for row in self.plan()['city'] if row['lgd_ulb_code'] == 100)
        self.assertTrue(row['create']); self.assertIn('North', row['label'])
        self.assertIsNone(self.inv['city'][0]['district_id'])

    def test_multidistrict_variants_keep_verified_parents(self):
        rows = [row for row in self.plan()['city'] if row['lgd_ulb_code'] == 101]
        self.assertEqual([row['lgd_district_code'] for row in rows], [10, 11])
        self.assertEqual([row['label'] for row in rows], ['Twin (North)', 'Twin (South)'])

    def test_missing_district_does_not_guess(self):
        row = next(row for row in self.plan()['city'] if row['lgd_ulb_code'] == 102)
        self.assertIsNone(row['lgd_district_code'])

    def test_same_state_names_have_distinct_qualified_labels(self):
        rows = [row for row in self.plan()['city'] if row['lgd_ulb_code'] in (103, 104)]
        self.assertEqual([row['label'] for row in rows], ['Riverside (North)', 'Riverside (South)'])

    def test_same_district_duplicate_source_codes_are_not_merged(self):
        rows = [row for row in self.plan()['city'] if row['lgd_ulb_code'] in (105, 106)]
        self.assertEqual(len(set(row['label'] for row in rows)), 2)
        self.assertTrue(all(row['create'] for row in rows))

    def test_government_supplement_has_no_fabricated_ulb_code(self):
        row = self.plan()['supplement'][0]
        self.assertNotIn('lgd_ulb_code', row); self.assertEqual(row['source_key'], 'government-harbor')

    def test_explicit_same_state_alias_reuses_master(self):
        self.inv['district'][0]['name'] = 'Northern District'
        self.ref['aliases']['district'] = [{'lgd_state_code': 1, 'lgd_district_code': 10,
                                          'names': ['Northern District'], 'evidence': ['https://example.invalid/evidence']}]
        row = next(row for row in self.plan()['district'] if row['lgd_district_code'] == 10)
        self.assertEqual(row['master_id'], 1); self.assertFalse(row['create'])

    def test_alias_never_crosses_state(self):
        self.inv['district'][0]['state_id'] = 35
        row = next(row for row in self.plan()['district'] if row['lgd_district_code'] == 10)
        self.assertTrue(row['create'])

    def test_ambiguous_alias_requires_review(self):
        self.inv['district'][0]['name'] = 'N District'
        self.inv['district'].append({'district_id': 2, 'state_id': 1, 'name': 'Northern District'})
        self.ref['aliases']['district'] = [{'lgd_state_code': 1, 'lgd_district_code': 10,
                                          'names': ['N District', 'Northern District'], 'evidence': ['https://example.invalid/evidence']}]
        with self.assertRaises(geo.GeographyError): self.plan()

    def test_invalid_coverage_parent_rejected(self):
        self.ref['ulbs'][0]['district_codes'] = [350]
        with self.assertRaises(geo.GeographyError): self.plan()

    def test_duplicate_code_rejected(self):
        self.ref['ulbs'][1]['lgd_ulb_code'] = 100
        with self.assertRaises(geo.GeographyError): self.plan()

    def test_wrong_expected_counts_rejected(self):
        self.ref['metadata']['expected_counts']['districts'] = 99
        with self.assertRaises(geo.GeographyError): self.plan()

    def test_state_alias_namespace_is_global(self):
        self.ref['aliases']['state'] = [{'lgd_state_code': code, 'names': ['Same alias'],
                                        'evidence': ['https://example.invalid/evidence']} for code in (1, 35)]
        with self.assertRaises(geo.GeographyError): self.plan()

    def test_supplement_controls_and_missing_evidence_rejected(self):
        self.ref['supplements'][0]['name'] = 'Bad\nName'
        with self.assertRaises(geo.GeographyError): self.plan()

    def test_planning_is_deterministic_and_does_not_mutate_inputs(self):
        before = deepcopy((self.ref, self.inv)); first = self.plan()
        self.assertEqual(first, self.plan()); self.assertEqual(before, (self.ref, self.inv))

    def test_repeat_plan_is_noop(self):
        inv, bindings = materialize(self.ref, self.inv, self.plan())
        summary = geo.build_plan(self.ref, inv, bindings)['summary']
        self.assertEqual([summary[name] for name in ('new_states', 'new_districts', 'new_cities', 'new_bindings')], [0, 0, 0, 0])

    def test_changed_binding_parent_rejected(self):
        inv, bindings = materialize(self.ref, self.inv, self.plan())
        inv['city'][0]['district_id'] = None
        with self.assertRaises(geo.GeographyError): geo.build_plan(self.ref, inv, bindings)

    def test_incomplete_snapshot_bindings_rejected(self):
        inv, bindings = materialize(self.ref, self.inv, self.plan())
        bindings['city'].pop()
        with self.assertRaises(geo.GeographyError): geo.build_plan(self.ref, inv, bindings)

    def test_snapshot_checksum_mismatch_rejected(self):
        inv, bindings = materialize(self.ref, self.inv, self.plan())
        bindings['snapshot'][0]['reference_sha256'] = '0' * 64
        with self.assertRaises(geo.GeographyError): geo.build_plan(self.ref, inv, bindings)

    def test_import_rejects_modified_marker_before_cursor_access(self):
        ref = geo.load_reference(); ref['_reference_sha256'] = '0' * 64
        with self.assertRaises(geo.GeographyError): geo.import_reference(None, ref, dry_run=False)

    def test_import_rejects_modified_body_before_cursor_access(self):
        ref = geo.load_reference(); ref['ulbs'][0]['name'] += ' changed'
        with self.assertRaises(geo.GeographyError): geo.import_reference(None, ref, dry_run=False)

    def test_modified_reference_file_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'changed.json'; path.write_text('{}', encoding='utf-8')
            with self.assertRaises(geo.GeographyError): geo.load_reference(path)


class DestinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(geo.__file__).parent / 'pgdb' / 'import_geo_reference.py'
        spec = importlib.util.spec_from_file_location('geo_import_cli', path)
        cls.cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.cli)

    def test_expected_server_and_explicit_local_test_only(self):
        self.cli.check_destination({'host': '127.0.0.1', 'port': '5433', 'dbname': 'dstt'}, hostname='BACKUPSERVER')
        self.cli.check_destination({'host': 'localhost', 'port': '5433', 'dbname': 'geo_reference_test'}, hostname='LOCAL')

    def test_other_server_database_and_host_are_rejected(self):
        for where, host in (({'host': '192.0.2.1', 'dbname': 'dstt', 'port': '5433'}, 'BACKUPSERVER'),
                            ({'host': '127.0.0.1', 'dbname': 'dstt', 'port': '5433'}, 'LOCAL'),
                            ({'host': '127.0.0.1', 'dbname': 'ticket', 'port': '5432'}, 'BACKUPSERVER'),
                            ({'host': '127.0.0.1', 'hostaddr': '192.0.2.1', 'dbname': 'geo_test'}, 'LOCAL')):
            with self.subTest(where=where):
                with self.assertRaises(geo.GeographyError): self.cli.check_destination(where, hostname=host)


if __name__ == '__main__':
    unittest.main()
