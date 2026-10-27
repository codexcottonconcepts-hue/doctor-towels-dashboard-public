"""Customer profiles used by explicit, permission-checked sales-entry actions.

The caller owns authorization, its SQL transaction and audit attribution. No
function commits, and ordinary invoice edits never update a shared profile.
"""
from collections.abc import Mapping
import re

import dashboard_policy as dp


PROFILE_FIELDS = ('customer_address', 'customer_location', 'customer_gstin', 'customer_contact_number')
_GSTIN = re.compile(r'[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\Z')
_CONTACT = re.compile(r'\+?[0-9 ()-]+\Z')


class CustomerError(ValueError):
    """A safe, actionable customer validation or access message."""
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _text(value, label, maximum, *, multiline=False):
    result = str(value if value is not None else '').strip()
    result = result.replace('\r\n', '\n').replace('\r', '\n')
    if any(ord(ch) < 32 and ch not in ('\n', '\t') for ch in result):
        raise CustomerError(f'{label} contains an unsupported control character.')
    if not multiline:
        result = ' '.join(result.split())
    if len(result) > maximum:
        raise CustomerError(f'{label} must be at most {maximum} characters.')
    return result or None


def parse_fields(form):
    """Normalize customer details; unreported optional values remain blank."""
    address = _text(form.get('customer_address'), 'Customer address', 2000, multiline=True)
    location = _text(form.get('customer_location'), 'Customer location', 200)
    gstin = _text(form.get('customer_gstin'), 'GSTIN', 15)
    contact = _text(form.get('customer_contact_number'), 'Contact number', 40)
    if gstin:
        gstin = gstin.upper()
        if not _GSTIN.fullmatch(gstin):
            raise CustomerError('Enter a valid 15-character GSTIN, or leave it blank for an unregistered customer.')
    if contact and (not _CONTACT.fullmatch(contact) or not re.search(r'[0-9]', contact)):
        raise CustomerError('Contact number: use digits, spaces, parentheses or hyphens, with an optional leading +.')
    return {'address': address, 'location': location, 'gstin': gstin, 'contact_number': contact}


def _details(details):
    return parse_fields({'customer_' + key: details.get(key)
                         for key in ('address', 'location', 'gstin', 'contact_number')})


def _ident(value, label, *, required=False):
    try:
        if value is None or str(value).strip() == '':
            if required:
                raise ValueError
            return None
        result = int(str(value))
        if not 0 < result <= 2147483647:
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise CustomerError('Choose a valid ' + label + '.') from None


def validate_geography(cur, geo, *, existing_city_id=None):
    """Resolve ancestry, retaining only the profile's own historical city.

    New customers and changed cities require an active city. An explicit
    address edit may retain the customer's existing inactive city, just as a
    historical invoice can retain its saved dimensions.
    """
    state = _ident(geo.get('state_id'), 'customer state', required=True)
    district = _ident(geo.get('district_id'), 'customer district')
    city = _ident(geo.get('city_id'), 'customer city')
    cur.execute('select state_id from master.state where state_id=%s', (state,))
    if not cur.fetchone():
        raise CustomerError('The selected customer state is unavailable. Reload the form.')
    if district:
        cur.execute('select state_id from master.district where district_id=%s', (district,))
        record = cur.fetchone()
        if not record or record[0] != state:
            raise CustomerError('The customer district must belong to the selected state.')
    if city:
        cur.execute('select state_id,district_id from master.city where city_id=%s and (is_active or city_id=%s)',
                    (city, existing_city_id))
        record = cur.fetchone()
        if not record or record[0] != state or (district and record[1] != district):
            raise CustomerError('The customer city must belong to the selected state and district.')
        district = record[1]
    return {'state_id': state, 'district_id': district, 'city_id': city}


def _scope(channels):
    if channels is None:
        return '', []
    names = {dp.canonical_channel(channel) for channel in channels}
    if not names:
        return ' and false', []
    tokens = sorted({token for token, name in dp._CHANNEL_ALIASES.items() if name in names})
    if not tokens:
        return ' and false', []
    return (" and (exists (select 1 from txn.sales_entry e join master.channel ch "
            "on ch.channel_id=e.channel_id where e.customer_id=cu.customer_id "
            "and not e.is_deleted and regexp_replace(lower(ch.code),'[^a-z0-9]','','g')=any(%s)) "
            "or not exists (select 1 from txn.sales_entry history where history.customer_id=cu.customer_id))",
            [tokens])


def _profile(cur, customer_id, channels, include_inactive, *, lock=False):
    customer_id = _ident(customer_id, 'customer', required=True)
    scope, args = _scope(channels)
    active = '' if include_inactive else ' and cu.is_active'
    cur.execute('select cu.customer_id,cu.name,cu.address,cu.location,cu.gstin,cu.contact_number,'
                'coalesce(cu.state_id,ci.state_id) as state_id,'
                'coalesce(cu.district_id,ci.district_id) as district_id,cu.city_id,'
                'st.name as state,di.name as district,ci.name as city,'
                'cu.details_updated_at as revision '
                'from master.customer cu left join master.city ci on ci.city_id=cu.city_id '
                'left join master.state st on st.state_id=coalesce(cu.state_id,ci.state_id) '
                'left join master.district di on di.district_id=coalesce(cu.district_id,ci.district_id) '
                'where cu.customer_id=%s' + active + scope + (' for update of cu' if lock else ''),
                [customer_id, *args])
    row = cur.fetchone()
    if not row:
        raise CustomerError('This customer is unavailable or outside your assigned channels.', 404)
    result = dict(row) if isinstance(row, Mapping) else dict(zip([column[0] for column in cur.description], row))
    revision = result.get('revision')
    result['revision'] = revision.isoformat() if hasattr(revision, 'isoformat') else str(revision or '')
    return result


def get_profile(cur, customer_id, channels=None, include_inactive=False):
    """Read one authorized profile, rather than expose all profiles to the page."""
    return _profile(cur, customer_id, channels, include_inactive)


def create_customer(cur, name, details, geo, sub_channel_id, username):
    """Insert a distinct customer; a name conflict never edits another profile."""
    name = _text(name, 'Customer name', 200)
    if not name:
        raise CustomerError('Enter the new customer name.')
    details = _details(details)
    geo = validate_geography(cur, geo)
    sub_channel_id = _ident(sub_channel_id, 'customer subchannel')
    if sub_channel_id:
        cur.execute('select sub_channel_id from master.sub_channel where sub_channel_id=%s and is_active', (sub_channel_id,))
        if not cur.fetchone():
            raise CustomerError('The selected customer subchannel is unavailable. Reload the form.')
    cur.execute("select set_config('app.username',%s,true)", (str(username),))
    cur.execute('insert into master.customer(name,address,location,gstin,contact_number,state_id,district_id,city_id,sub_channel_id,details_updated_by) '
                'values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (lower(name)) do nothing returning customer_id',
                (name, details['address'], details['location'], details['gstin'], details['contact_number'],
                 geo['state_id'], geo['district_id'], geo['city_id'], sub_channel_id, str(username)))
    created = cur.fetchone()
    if not created:
        raise CustomerError('This customer name already exists. Select the existing customer; its saved details were not changed.', 409)
    return created[0]


def update_customer(cur, customer_id, details, geo, revision, username, channels=None):
    """Explicitly update a profile, with channel and stale-form protection."""
    contact_supplied = 'contact_number' in details
    details = _details(details)
    current = _profile(cur, customer_id, channels, False, lock=True)
    if not revision or str(revision) != current['revision']:
        raise CustomerError('Another user changed this customer. Reload its details before saving again.', 409)
    if not contact_supplied:
        # An already-open older browser does not have the new phone control.
        # Omission must not erase a saved number; explicit blank still clears it.
        details['contact_number'] = current.get('contact_number')
    geo = validate_geography(cur, geo, existing_city_id=current['city_id'])
    cur.execute("select set_config('app.username',%s,true)", (str(username),))
    cur.execute('update master.customer set address=%s,location=%s,gstin=%s,contact_number=%s,state_id=%s,district_id=%s,city_id=%s,'
                'details_updated_at=clock_timestamp(),details_updated_by=%s where customer_id=%s',
                (details['address'], details['location'], details['gstin'], details['contact_number'], geo['state_id'],
                 geo['district_id'], geo['city_id'], str(username), current['customer_id']))
    return _profile(cur, current['customer_id'], channels, False)
