"""Validated dashboard display and data access. No database or auth dependencies."""
import json

MONTHS = ('Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar')
CHANNELS = ('LFO', 'L-LFO', 'D&R-S', 'D&R-N', 'Saloon', 'Hospital')
ONLINE_WIDGETS = ('online_summary', 'online_platforms', 'online_trend')
CATALOGS = {
    'pages': dict(zip(('home','team','lfo','llfo','drs','drn','saloon','balance','state','store','map','yoy'),
                     ('Dashboard','Sales executive breakup','LFO','L-LFO','D&R-S','D&R-N','Saloon','Target Balance','State Details','Store Details','India Map','Year on Year'))),
    'views': {'overview':'Overview', 'detail':'Detail', 'secondary':'Secondary'},
    'widgets': {
        'orders':'Total orders', 'units':'Total units sold', 'target':'Target', 'achievement':'Achievement',
        'difference':'Difference', 'achievement_pct':'Achievement %', 'channel_sales':'Sales by channel',
        'executive_sales':'Sales by executive / sales team', 'retailer_sales':'Retailer DT / TT targets and achievement',
        'monthly_sales':'Month-on-month targets and achievement', 'monthly_quantity':'Monthly quantity',
        'achievement_gauge':'Target achievement gauge', 'executive_monthly':'Executive monthly report',
        'executive_customers':'Executive customer / branch breakup',
        'top_groups':'Top groups', 'alerts':'Alerts and notifications', 'yoy':'Year-on-year charts and tables',
        'sales_trend':'Sales trend', 'product_sales':'Sales by product brand', 'product_volume':'Volume by product brand',
        'fast_products':'Fast-moving products', 'colours':'High-demand colours', 'orders_week':'Orders by week',
        'order_bands':'Order value bands', 'region_sales':'Sales by region', 'top_states':'Top states',
        'orders_state':'Orders by state', 'orders_category':'Orders by category', 'orders_month':'Orders by month',
        'cities':'City sales', 'balance_monthly':'Monthly target balance chart', 'balance_table':'Monthly target balance table',
        'balance_channels':'Target balance by channel', 'state_split':'State sales split-up', 'city_split':'City sales split-up',
        'store_groups':'Retail groups by channel', 'store_master':'Store master', 'store_categories':'Outlets by category',
        'map':'India billing map', 'state_ranking':'State billing rankings', 'collections':'Committed collections',
        'secondary_groups':'Secondary groups and targets', 'secondary_monthly':'Secondary month-on-month chart',
        'secondary_register':'Secondary branch / retailer table',
        'online_summary':'Online sales summary', 'online_platforms':'Online sales by platform',
        'online_trend':'Online daily, weekly and monthly trends',
    },
    'filters': {'fy':'Financial year','month':'Month','brand':'DT / TT','exec':'Executive','customer':'Customer',
                'sub':'Distribution','state':'State','secondaryMonth':'Secondary month','distributor':'Secondary distributor','branch':'Secondary branch',
                'onlinePlatform':'Online platform','onlinePeriod':'Online date and period'},
    'drilldowns': {'sales':'Invoice / sales breakup','executive':'Executive breakup','state':'State customer breakup','secondary':'Secondary sales breakup'},
}
CATALOGS['pages']['online'] = 'Online sales'
SCOPE_LABELS = {'channel':'Channels','executive':'Executives','customer':'Customers','brand':'DT / TT brands',
                'distribution':'Distributions / retail groups','state':'States','month':'Months (every financial year)'}
_CHANNEL_ALIASES = {'lfo':'LFO','llfo':'L-LFO','drs':'D&R-S','drn':'D&R-N','north':'D&R-N',
                    'saloon':'Saloon','hospital':'Hospital','lfoprimary':'LFO'}


def canonical_channel(value):
    text = str(value)
    token = ''.join(c for c in text.lower() if c.isalnum())
    return _CHANNEL_ALIASES.get(token, text)


def full_policy():
    return {'version':1, **{key:list(values) for key,values in CATALOGS.items()},
            'scopes':{key:None for key in SCOPE_LABELS}}


def deny_policy():
    return {'version':1, **{key:[] for key in CATALOGS}, 'scopes':{key:[] for key in SCOPE_LABELS}}


def normalize_policy(policy):
    """Strict validation. None is deliberately not shorthand for unrestricted."""
    expected = {'version','scopes',*CATALOGS}
    if not isinstance(policy,dict) or set(policy) != expected or type(policy.get('version')) is not int or policy['version'] != 1:
        raise ValueError('Invalid dashboard access policy version or fields.')
    normalized = {'version':1}
    for key,catalog in CATALOGS.items():
        values = policy[key]
        if not isinstance(values,list) or len(values)>len(catalog) or any(not isinstance(v,str) or v not in catalog for v in values):
            raise ValueError('Invalid dashboard '+key+' selection.')
        chosen = set(values)
        normalized[key] = [v for v in catalog if v in chosen]
    scopes = policy['scopes']
    if not isinstance(scopes,dict) or set(scopes) != set(SCOPE_LABELS):
        raise ValueError('Invalid dashboard data access fields.')
    normalized['scopes'] = {}
    for key in SCOPE_LABELS:
        values = scopes[key]
        if values is None:
            normalized['scopes'][key] = None
            continue
        if not isinstance(values,list) or len(values)>5000 or any(not isinstance(v,str) or not v.strip() or len(v)>500 or any(ord(c)<32 for c in v) for v in values):
            raise ValueError('Invalid dashboard '+key+' data selection.')
        if key == 'channel':
            values = [canonical_channel(v) for v in values]
            if any(v not in CHANNELS for v in values):
                raise ValueError('Unknown dashboard channel.')
        if key == 'month' and any(v not in MONTHS for v in values):
            raise ValueError('Dashboard month access uses Apr through Mar in every financial year.')
        normalized['scopes'][key] = sorted(set(values), key=(lambda v:MONTHS.index(v)) if key=='month' else str.casefold)
    return normalized


def _value(user,key,default=None):
    try:
        return user[key]
    except (KeyError,IndexError,TypeError):
        return default


def policy_for(user):
    if user is None or _value(user,'disabled',False):
        return deny_policy()
    if _value(user,'is_admin',False):
        return full_policy()
    try:
        stored = _value(user,'dashboard_access_json')
        if not isinstance(stored,str):
            return deny_policy()
        return normalize_policy(json.loads(stored))
    except (ValueError,TypeError):
        return deny_policy()


def policy_is_subset(child,parent):
    """All includes future values; a finite parent can never grant All."""
    try:
        child,parent = normalize_policy(child),normalize_policy(parent)
    except (ValueError,TypeError):
        return False
    if any(not set(child[key]) <= set(parent[key]) for key in CATALOGS):
        return False
    for key in SCOPE_LABELS:
        c,p = child['scopes'][key],parent['scopes'][key]
        if p is not None and (c is None or not set(c)<=set(p)):
            return False
    return True


def is_unrestricted(user):
    return policy_for(user) == full_policy()


def scope_restricted(user):
    return any(values is not None for values in policy_for(user)['scopes'].values())


def online_allowed(policy):
    """Online aggregates have their own explicit page and section grant."""
    return ('online' in policy.get('pages', []) and
            any(key in policy.get('widgets', []) for key in ONLINE_WIDGETS))


def row_matches(row,policy):
    """Primary ledger positions shared by refresh.py and the dashboard."""
    if len(row)<13:
        return False
    values = {'month':str(row[0]).split('-')[0], 'channel':canonical_channel(row[1]),
              'executive':row[2], 'state':row[3], 'brand':row[5], 'distribution':row[6], 'customer':row[12]}
    return all(allowed is None or values[key] in allowed for key,allowed in policy['scopes'].items())


def options_from_data(data,user=None,saved_policy=None):
    """Options visible to the manager, with permitted saved values preserved."""
    policy = full_policy() if user is None else policy_for(user)
    options = {key:set() for key in SCOPE_LABELS}
    for row in (data or {}).get('rows',[]):
        if not row_matches(row,policy):
            continue
        values = {'channel':canonical_channel(row[1]),'executive':row[2],'state':row[3],
                  'brand':row[5],'distribution':row[6],'customer':row[12]}
        for key,value in values.items():
            if isinstance(value,str) and value.strip():
                options[key].add(value)
    for key,known in (('channel',CHANNELS),('brand',('Doctor Towel','Thirsty Towel')),('month',MONTHS)):
        allowed = policy['scopes'][key]
        options[key].update(v for v in known if allowed is None or v in allowed)
    for source in (policy,saved_policy):
        if source is None:
            continue
        source = normalize_policy(source)
        for key,values in source['scopes'].items():
            if values is not None:
                allowed = policy['scopes'][key]
                options[key].update(v for v in values if allowed is None or v in allowed)
    return {key:sorted(values,key=(lambda v:MONTHS.index(v)) if key=='month' else str.casefold) for key,values in options.items()}
