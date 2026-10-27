"""Pinned public geography, appended without changing any existing master row.

The caller owns the owner-role transaction. Dry runs perform SELECTs only.
No customer, invoice, sales, target or warehouse table is accessed here.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import unicodedata

REFERENCE_SHA256 = '1d851ac27ed0ff1f7d0f16ac22da66b3afa44a9c6397d7e70f8e8bebe061dff6'
REFERENCE_CONTENT_SHA256 = 'a94a859556d5e769183936054c4c7016c68553f23e96280d788f1d963f0da2d7'
REFERENCE_PATH = Path(__file__).resolve().with_name('india_lgd_reference.json')
BINDING_TABLES = {'snapshot': 'lgd_reference_snapshot', 'state': 'lgd_state_binding',
                  'district': 'lgd_district_binding', 'city': 'lgd_city_binding',
                  'supplement': 'geo_place_supplement_binding'}


class GeographyError(ValueError):
    pass


def _key(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 2147483647


def validate_reference(ref):
    """Validate codes, parents, explicit aliases and expected snapshot counts."""
    try:
        metadata = ref['metadata']
        date.fromisoformat(metadata['source_date'])
        for name in ('snapshot_id', 'source_uri', 'mirror_uri', 'license_uri'):
            if not isinstance(metadata[name], str) or not metadata[name]:
                raise GeographyError('Reference provenance is incomplete.')
        maps = {}
        for kind, field in (('states', 'lgd_state_code'), ('districts', 'lgd_district_code'), ('ulbs', 'lgd_ulb_code')):
            rows = ref[kind]
            maps[kind] = {row[field]: row for row in rows}
            if len(maps[kind]) != len(rows) or any(not _positive(row[field]) for row in rows):
                raise GeographyError('Reference codes must be unique positive integers: ' + kind)
            if any(not isinstance(row.get('name'), str) or not row['name'].strip()
                   or len(row['name']) > 300 or any(ord(ch) < 32 for ch in row['name']) for row in rows):
                raise GeographyError('Reference place names are invalid.')
        for row in ref['districts'] + ref['ulbs']:
            if row['lgd_state_code'] not in maps['states']:
                raise GeographyError('Reference state parent is missing.')
        variants = 0
        for row in ref['ulbs']:
            codes = row['district_codes']
            if not isinstance(codes, list) or len(set(codes)) != len(codes):
                raise GeographyError('ULB district coverage must contain distinct codes.')
            for code in codes:
                district = maps['districts'].get(code)
                if not district or district['lgd_state_code'] != row['lgd_state_code']:
                    raise GeographyError('ULB district coverage crosses a state or is unknown.')
            if not isinstance(row.get('local_body_type_code'), str) or not row['local_body_type_code']:
                raise GeographyError('ULB type is missing.')
            variants += max(1, len(codes))
        supplements = ref.get('supplements', [])
        if len({row['source_key'] for row in supplements}) != len(supplements):
            raise GeographyError('Supplement source keys must be unique.')
        for row in supplements:
            district = maps['districts'].get(row['lgd_district_code'])
            if not district or district['lgd_state_code'] != row['lgd_state_code']:
                raise GeographyError('Supplement parent geography is invalid.')
            if (not isinstance(row.get('source_key'), str) or not row['source_key']
                    or any(ch not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for ch in row['source_key'])
                    or not isinstance(row.get('name'), str) or not row['name'].strip()
                    or len(row['name']) > 300 or any(ord(ch) < 32 for ch in row['name'])
                    or not row.get('source_uri', '').startswith('https://')):
                raise GeographyError('Supplement provenance is missing.')
        counts = {'states': len(ref['states']), 'districts': len(ref['districts']), 'ulbs': len(ref['ulbs']),
                  'ulb_variants': variants, 'supplements': len(supplements), 'variants': variants + len(supplements)}
        if counts != metadata['expected_counts']:
            raise GeographyError('Reference row counts differ from the approved snapshot.')
        for kind, code_field in (('state', 'lgd_state_code'), ('district', 'lgd_district_code')):
            seen = {}
            for alias in ref.get('aliases', {}).get(kind, []):
                target = maps['states' if kind == 'state' else 'districts'].get(alias[code_field])
                if not target or (kind == 'district' and alias['lgd_state_code'] != target['lgd_state_code']):
                    raise GeographyError('An explicit alias has the wrong parent or target.')
                if not alias.get('evidence') or any(not str(uri).startswith('https://') for uri in alias['evidence']):
                    raise GeographyError('Alias evidence is missing.')
                for name in alias['names']:
                    if not isinstance(name, str) or not name.strip() or any(ord(ch) < 32 for ch in name):
                        raise GeographyError('An explicit alias has an invalid name.')
                    token = (target['lgd_state_code'] if kind == 'district' else None, _key(name))
                    if token in seen and seen[token] != alias[code_field]:
                        raise GeographyError('An explicit alias identifies multiple places.')
                    seen[token] = alias[code_field]
        return counts
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, GeographyError):
            raise
        raise GeographyError('The geography reference structure is invalid.') from None


def load_reference(path=None):
    raw = Path(path or REFERENCE_PATH).read_bytes()
    if hashlib.sha256(raw).hexdigest() != REFERENCE_SHA256:
        raise GeographyError('Reference checksum differs from the approved public snapshot.')
    ref = json.loads(raw)
    validate_reference(ref)
    ref['_reference_sha256'] = REFERENCE_SHA256
    return ref


def _aliases(ref, kind, code):
    field = 'lgd_state_code' if kind == 'state' else 'lgd_district_code'
    return [name for row in ref.get('aliases', {}).get(kind, []) if row[field] == code for name in row['names']]


def _choose(rows, names, canonical):
    allowed = {_key(name) for name in names}
    candidates = [row for row in rows if _key(row['name']) in allowed]
    exact = [row for row in candidates if _key(row['name']) == _key(canonical)]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) > 1:
        raise GeographyError('Several existing masters match an explicit place alias; review them first.')
    return candidates[0] if candidates else None


def build_plan(ref, inventory, bindings=None):
    """Pure deterministic plan. Negative IDs are simulation tokens, never LGD IDs."""
    counts = validate_reference(ref)
    bindings = bindings or {}
    inventory = deepcopy(inventory)
    states_by_id = {row['state_id']: row for row in inventory['state']}
    districts_by_id = {row['district_id']: row for row in inventory['district']}
    cities_by_id = {row['city_id']: row for row in inventory['city']}
    bound_states = {row['lgd_state_code']: row for row in bindings.get('state', [])}
    bound_districts = {row['lgd_district_code']: row for row in bindings.get('district', [])}
    bound_cities = {(row['lgd_ulb_code'], row['district_key']): row for row in bindings.get('city', [])}
    bound_supplements = {row['source_key']: row for row in bindings.get('supplement', [])}
    claimed_states = {row['state_id']: code for code, row in bound_states.items()}
    claimed_districts = {row['district_id']: code for code, row in bound_districts.items()}
    claimed_cities = {row['city_id'] for row in list(bound_cities.values()) + list(bound_supplements.values())}
    state_plan, district_plan, city_plan, supplement_plan = [], [], [], []
    for source in sorted(ref['states'], key=lambda row: row['lgd_state_code']):
        code = source['lgd_state_code']; names = [source['name'], *_aliases(ref, 'state', code)]
        bound = bound_states.get(code)
        found = states_by_id.get(bound['state_id']) if bound else _choose(inventory['state'], names, source['name'])
        if bound and (not found or _key(found['name']) not in {_key(name) for name in names}):
            raise GeographyError('An LGD state binding changed identity; existing masters were preserved.')
        if found and found['state_id'] in claimed_states and claimed_states[found['state_id']] != code:
            raise GeographyError('A state master is already bound to a different LGD code.')
        state_plan.append({**source, 'master_id': found['state_id'] if found else -code,
                           'create': not bool(found), 'bind': not bool(bound)})
    state_map = {row['lgd_state_code']: row['master_id'] for row in state_plan}
    for source in sorted(ref['districts'], key=lambda row: row['lgd_district_code']):
        code = source['lgd_district_code']; state_id = state_map[source['lgd_state_code']]
        names = [source['name'], *_aliases(ref, 'district', code)]
        bound = bound_districts.get(code)
        candidates = [row for row in inventory['district'] if row['state_id'] == state_id]
        found = districts_by_id.get(bound['district_id']) if bound else _choose(candidates, names, source['name'])
        if bound and (bound['lgd_state_code'] != source['lgd_state_code'] or not found or found['state_id'] != state_id
                      or _key(found['name']) not in {_key(name) for name in names}):
            raise GeographyError('An LGD district binding changed parent or identity.')
        if found and found['district_id'] in claimed_districts and claimed_districts[found['district_id']] != code:
            raise GeographyError('A district master is already bound to a different LGD code.')
        district_plan.append({**source, 'master_id': found['district_id'] if found else -code,
                              'create': not bool(found), 'bind': not bool(bound)})
    district_map = {row['lgd_district_code']: row['master_id'] for row in district_plan}
    source_districts = {row['lgd_district_code']: row for row in ref['districts']}
    variants = [{**row, 'lgd_district_code': district} for row in ref['ulbs'] for district in row['district_codes'] or [None]]
    names = Counter((row['lgd_state_code'], _key(row['name'])) for row in ref['ulbs'])
    identities = Counter((row['lgd_state_code'], row['lgd_district_code'], _key(row['name'])) for row in variants)
    reserved = {(row['state_id'], _key(row['name'])) for row in inventory['city']}
    next_city = -1

    def place(source, bound, *, supplement=False):
        nonlocal next_city
        state_id = state_map[source['lgd_state_code']]
        district_code = source['lgd_district_code']
        district_id = district_map[district_code] if district_code is not None else None
        found = cities_by_id.get(bound['city_id']) if bound else None
        if bound:
            if (bound['lgd_state_code'] != source['lgd_state_code'] or bound['lgd_district_code'] != district_code
                    or not found or found['state_id'] != state_id or found['district_id'] != district_id
                    or not found['is_active'] or found['name'] != bound['master_label']):
                raise GeographyError('A city reference binding changed label, activity or parent; no master was rewritten.')
        else:
            candidates = [row for row in inventory['city'] if row['state_id'] == state_id
                          and row['district_id'] == district_id and row['is_active']
                          and _key(row['name']) == _key(source['name']) and row['city_id'] not in claimed_cities]
            unique_source = supplement or identities[(source['lgd_state_code'], district_code, _key(source['name']))] == 1
            if len(candidates) == 1 and unique_source:
                found = candidates[0]
        label = found['name'] if found else source['name']
        if not found:
            district_name = source_districts[district_code]['name'] if district_code is not None else None
            qualify = not supplement and (len(source['district_codes']) > 1 or names[(source['lgd_state_code'], _key(source['name']))] > 1)
            if qualify and district_name:
                label = source['name'] + ' (' + district_name + ')'
            if (state_id, _key(label)) in reserved and district_name:
                label = source['name'] + ' (' + district_name + ')'
            if (state_id, _key(label)) in reserved:
                discriminator = source['source_key'] if supplement else 'LGD ' + str(source['lgd_ulb_code'])
                label = source['name'] + ' (' + (district_name + ' · ' if district_name else '') + discriminator + ')'
            if (state_id, _key(label)) in reserved:
                raise GeographyError('A qualified city name still collides; review the source identities.')
            reserved.add((state_id, _key(label)))
        master_id = found['city_id'] if found else next_city
        if not found: next_city -= 1
        if found: claimed_cities.add(master_id)
        return {**source, 'master_id': master_id, 'label': label, 'create': not bool(found), 'bind': not bool(bound)}

    for source in sorted(variants, key=lambda row: (row['lgd_state_code'], row['lgd_ulb_code'], row['lgd_district_code'] or 0)):
        city_plan.append(place(source, bound_cities.get((source['lgd_ulb_code'], source['lgd_district_code'] or 0))))
    for source in sorted(ref.get('supplements', []), key=lambda row: row['source_key']):
        supplement_plan.append(place(source, bound_supplements.get(source['source_key']), supplement=True))
    existing_snapshot = [row for row in bindings.get('snapshot', []) if row['snapshot_id'] == ref['metadata']['snapshot_id']]
    if existing_snapshot:
        if existing_snapshot[0]['reference_sha256'] != ref.get('_reference_sha256', hashlib.sha256(_json(ref).encode()).hexdigest()):
            raise GeographyError('Existing snapshot provenance differs from this reference.')
        if any(row['bind'] for rows in (state_plan, district_plan, city_plan, supplement_plan) for row in rows):
            raise GeographyError('Existing snapshot bindings are incomplete; no duplicate masters were added.')
    summary = {**counts, 'new_states': sum(row['create'] for row in state_plan),
               'new_districts': sum(row['create'] for row in district_plan),
               'new_cities': sum(row['create'] for row in city_plan + supplement_plan),
               'reused_city_variants': sum(not row['create'] for row in city_plan + supplement_plan),
               'new_bindings': sum(row['bind'] for rows in (state_plan, district_plan, city_plan, supplement_plan) for row in rows),
               'snapshot_exists': bool(existing_snapshot)}
    return {'metadata': ref['metadata'], 'state': state_plan, 'district': district_plan,
            'city': city_plan, 'supplement': supplement_plan, 'summary': summary}


def read_inventory(cur):
    result = {}
    for table, field in (('state', 'state_id'), ('district', 'district_id'), ('city', 'city_id')):
        cur.execute('select to_jsonb(t) from master.' + table + ' t order by ' + field)
        result[table] = [row[0] for row in cur.fetchall()]
    return result


def read_bindings(cur):
    existence = []
    for table in BINDING_TABLES.values():
        cur.execute('select to_regclass(%s)', ('aux.' + table,))
        existence.append(cur.fetchone()[0] is not None)
    if not any(existence):
        return None
    if not all(existence):
        raise GeographyError('The geography reference schema is incomplete. Review migration 25.')
    result = {}
    for key, table in BINDING_TABLES.items():
        cur.execute('select to_jsonb(t) from aux.' + table + ' t')
        result[key] = [row[0] for row in cur.fetchall()]
    return result


def import_reference(cur, ref, dry_run=True, actor='lgd_reference_import'):
    """Append masters and immutable reference bindings in the caller transaction."""
    validate_reference(ref)
    body = {key: value for key, value in ref.items() if not key.startswith('_')}
    if (ref.get('_reference_sha256') != REFERENCE_SHA256
            or hashlib.sha256(_json(body).encode()).hexdigest() != REFERENCE_CONTENT_SHA256):
        raise GeographyError('Import requires the unchanged, approved public reference snapshot.')
    if not dry_run:
        cur.execute("select pg_advisory_xact_lock(hashtext('india-lgd-reference-import'))")
        cur.execute('lock table master.state,master.district,master.city in share row exclusive mode')
    before = read_inventory(cur)
    bindings = read_bindings(cur)
    if not dry_run and bindings is None:
        raise GeographyError('Apply migration 25 as the database owner before importing geography.')
    plan = build_plan(ref, before, bindings)
    result = {'dry_run': bool(dry_run), 'snapshot_id': ref['metadata']['snapshot_id'],
              'summary': plan['summary'], 'existing_master_rows_unchanged': True}
    if dry_run:
        return result
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 128:
        raise GeographyError('The reference import audit actor is invalid.')
    metadata = ref['metadata']; snapshot = metadata['snapshot_id']
    reference_hash = ref.get('_reference_sha256', hashlib.sha256(_json(ref).encode()).hexdigest())
    cur.execute("select set_config('app.username',%s,true)", (actor,))
    if not plan['summary']['snapshot_exists']:
        cur.execute('insert into aux.lgd_reference_snapshot(snapshot_id,source_date,reference_sha256,source_uri,mirror_uri,license_uri,expected_counts,reference_json,imported_by) '
                    'values(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)',
                    (snapshot, metadata['source_date'], reference_hash, metadata['source_uri'], metadata['mirror_uri'], metadata['license_uri'],
                     _json(metadata['expected_counts']), _json({key: value for key, value in ref.items() if not key.startswith('_')}), actor))
    state_ids, district_ids = {}, {}
    for row in plan['state']:
        identifier = row['master_id']
        if row['create']:
            cur.execute('insert into master.state(name) values(%s) returning state_id', (row['name'],))
            identifier = cur.fetchone()[0]
        state_ids[row['lgd_state_code']] = identifier
        if row['bind']:
            cur.execute('insert into aux.lgd_state_binding(lgd_state_code,state_id,source_name,created_snapshot_id) values(%s,%s,%s,%s)',
                        (row['lgd_state_code'], identifier, row['name'], snapshot))
    for row in plan['district']:
        identifier = row['master_id']
        if row['create']:
            cur.execute('insert into master.district(state_id,name) values(%s,%s) returning district_id',
                        (state_ids[row['lgd_state_code']], row['name']))
            identifier = cur.fetchone()[0]
        district_ids[row['lgd_district_code']] = identifier
        if row['bind']:
            cur.execute('insert into aux.lgd_district_binding(lgd_district_code,lgd_state_code,district_id,source_name,created_snapshot_id) values(%s,%s,%s,%s,%s)',
                        (row['lgd_district_code'], row['lgd_state_code'], identifier, row['name'], snapshot))
    for kind in ('city', 'supplement'):
        for row in plan[kind]:
            identifier = row['master_id']
            if row['create']:
                cur.execute('insert into master.city(name,state_id,district_id,is_active) values(%s,%s,%s,true) returning city_id',
                            (row['label'], state_ids[row['lgd_state_code']], district_ids.get(row['lgd_district_code'])))
                identifier = cur.fetchone()[0]
            if not row['bind']:
                continue
            if kind == 'city':
                cur.execute('insert into aux.lgd_city_binding(lgd_ulb_code,district_key,lgd_district_code,lgd_state_code,city_id,source_name,master_label,local_body_type_code,created_snapshot_id) '
                            'values(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (row['lgd_ulb_code'], row['lgd_district_code'] or 0, row['lgd_district_code'], row['lgd_state_code'], identifier,
                             row['name'], row['label'], row['local_body_type_code'], snapshot))
            else:
                cur.execute('insert into aux.geo_place_supplement_binding(source_key,lgd_state_code,lgd_district_code,city_id,source_name,master_label,source_uri,created_snapshot_id) '
                            'values(%s,%s,%s,%s,%s,%s,%s,%s)',
                            (row['source_key'], row['lgd_state_code'], row['lgd_district_code'], identifier, row['name'], row['label'], row['source_uri'], snapshot))
    after = read_inventory(cur)
    for table, field in (('state', 'state_id'), ('district', 'district_id'), ('city', 'city_id')):
        existing = {row[field]: row for row in after[table]}
        if any(existing.get(row[field]) != row for row in before[table]):
            raise GeographyError('An existing geography row changed. Roll back this import transaction.')
        expected = sum(row['create'] for row in plan[table]) if table != 'city' else plan['summary']['new_cities']
        if len(after[table]) != len(before[table]) + expected:
            raise GeographyError('Unexpected geography row counts. Roll back this import transaction.')
    repeated = build_plan(ref, after, read_bindings(cur))
    if repeated['summary']['new_bindings'] or any(repeated['summary'][name] for name in ('new_states', 'new_districts', 'new_cities')):
        raise GeographyError('The appended reference is not idempotent. Roll back this import transaction.')
    result['after_counts'] = {table: len(after[table]) for table in after}
    result['repeat_would_insert'] = 0
    return result
