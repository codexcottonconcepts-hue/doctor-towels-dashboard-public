"""Project dashboard payloads onto a user's allowed data, before serialization.

This is an authorization boundary, not a browser filter. Supplementary totals
are withheld when their grain cannot satisfy every granted scope. The input is
never mutated, so an administrator's cached payload cannot be narrowed (or a
restricted user's widened) by a subsequent request.
"""
from copy import deepcopy
from collections.abc import Mapping
from datetime import date

from dashboard_policy import canonical_channel, online_allowed


MONTHS = ("Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar")
ROW_FIELDS = {"month": 0, "channel": 1, "executive": 2, "state": 3,
              "brand": 5, "distribution": 6, "customer": 12}
TARGET_CHANNELS = {"LFO Primary": "LFO", "L-LFO": "L-LFO", "D&R-S": "D&R-S",
                   "North": "D&R-N", "Saloon": "Saloon"}
SECONDARY_CHANNELS = {"lfoSecondary": "LFO", "llfoSecondary": "L-LFO",
                      "saloonSecondary": "Saloon", "drsSecondary": "D&R-S",
                      "drnSecondary": "D&R-N"}
REGIONS = {"D&R-S": "D&R - S", "D&R-N": "D&R - N", "L-LFO": "L - LFO",
           "LFO": "LFO", "Saloon": "Saloon", "Hospital": "Hospital"}
# Only static presentation metadata survives without a data-specific projection.
# New datasets must be explicitly reviewed here rather than silently bypassing
# restrictions when another feature is added to the shared payload.
STATIC_KEYS = {"source", "fetched", "logo", "wordmark", "map"}


def project_online(source, policy):
    """Online platform aggregates cannot satisfy primary-ledger identity scopes."""
    if not online_allowed(policy):
        return {"available": False, "reason": "Online sales access is not enabled for this account."}
    scopes = _scopes(policy)
    if any(scopes[key] is not None for key in ROW_FIELDS if key != "month"):
        return {"available": False, "reason": "Online sales does not contain the account's allowed data dimensions."}
    if not isinstance(source, Mapping):
        return {"available": False, "reason": "Online sales has not been imported yet."}
    result = deepcopy(source)
    allowed = scopes['month']
    if allowed is None:
        return result
    def permitted(value, monthly=False):
        try:
            if not isinstance(value, str) or len(value) != (7 if monthly else 10):
                return False
            period = date.fromisoformat(value + '-01' if monthly else value)
            return MONTHS[(period.month - 4) % 12] in allowed
        except (ValueError, TypeError):
            return False
    result['daily'] = [deepcopy(row) for row in source.get('daily', [])
                       if isinstance(row, Mapping) and permitted(row.get('date'))]
    result['monthly'] = [deepcopy(row) for row in source.get('monthly', [])
                         if isinstance(row, Mapping) and permitted(row.get('month'), monthly=True)]
    dates = sorted({row['date'] for row in result['daily']})
    result['coverage'] = {'dailyStart': dates[0] if dates else None,
                          'dailyEnd': dates[-1] if dates else None,
                          'latestReportedDate': dates[-1] if dates else None,
                          'dailyDays': len(dates),
                          'monthlyEnteredCells': sum(row.get('source') == 'monthly-entered' for row in result['monthly']),
                          'monthlyDerivedCells': sum(row.get('source') == 'daily-derived' for row in result['monthly'])}
    periods = [row['date'][:7] for row in result['daily']] + [row['month'] for row in result['monthly']]
    result['financialYears'] = sorted({str(int(value[:4]) - (int(value[5:7]) < 4)) for value in periods})
    result['warnings'] = []
    return result


def _scopes(policy):
    value = policy.get("scopes") if isinstance(policy, Mapping) else None
    if not isinstance(value, Mapping):
        return {key: [] for key in ROW_FIELDS}
    # Canonical policy normally guarantees this; direct callers still fail closed.
    return {key: value.get(key) if key in value and
            (value[key] is None or isinstance(value[key], (list, tuple))) else []
            for key in ROW_FIELDS}


def _short_month(value):
    if not isinstance(value, str):
        return None
    short = value.split("-", 1)[0]
    return short if short in MONTHS else None


def _matches(scopes, field, value):
    allowed = scopes[field]
    if allowed is None:
        return True
    if field == "channel":
        normalized = canonical_channel(value)
        return bool(normalized) and normalized in {canonical_channel(x) for x in allowed}
    if field == "month":
        short = _short_month(value)
        return short is not None and short in allowed
    return value in allowed


def _row_allowed(row, scopes):
    return (isinstance(row, (list, tuple)) and len(row) >= 13 and
            all(_matches(scopes, field, row[index]) for field, index in ROW_FIELDS.items()))


def row_allowed(row, policy):
    """Whether one ledger/product/colour row satisfies all allowed dimensions."""
    return _row_allowed(row, _scopes(policy))


def _month_amounts(values, scopes):
    if not isinstance(values, Mapping):
        return {}
    return {month: deepcopy(values[month]) for month in MONTHS
            if month in values and _matches(scopes, "month", month)}


def _channel_targets(source, scopes):
    if any(scopes[key] is not None for key in ("executive", "customer", "brand", "distribution", "state")):
        return {}
    result = {}
    for fy, plans in (source or {}).items():
        if not isinstance(plans, Mapping):
            continue
        targets = {key: _month_amounts(plans[key], scopes) for key, channel in TARGET_CHANNELS.items()
                   if key in plans and _matches(scopes, "channel", channel)}
        targets = {key: months for key, months in targets.items() if months}
        if not targets:
            continue
        # Ignore precomputed rollups: they may contain forbidden channels/months.
        for total, components in (("D&R - S TOTAL", ("D&R-S", "L-LFO")),
                                  ("D&R TOTAL", ("D&R-S", "L-LFO", "North")),
                                  ("ALL TOTAL", tuple(TARGET_CHANNELS))):
            selected = [targets[key] for key in components if key in targets]
            if selected:
                targets[total] = {month: sum(plan.get(month, 0) or 0 for plan in selected)
                                  for month in MONTHS if any(month in plan for plan in selected)}
        result[str(fy)] = targets
    return result


def _city_states(rows):
    """State inference is accepted only when the channel/city is unambiguous."""
    result = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 13 or not row[4] or not row[3]:
            continue
        key = (canonical_channel(row[1]), row[4])
        result.setdefault(key, set()).add(row[3])
    return result


def _exec_targets(source, scopes, city_states):
    if any(scopes[key] is not None for key in ("customer", "brand", "distribution")):
        return {}
    result = {}
    for fy, plans in (source or {}).items():
        kept = []
        for plan in plans or []:
            if not isinstance(plan, Mapping):
                continue
            # This is the dashboard's established interpretation of legacy city
            # assignments; a blank channel is never inferred from all sales.
            channel = canonical_channel(plan.get("channel") or plan.get("region") or "D&R-S")
            if not (_matches(scopes, "channel", channel) and _matches(scopes, "executive", plan.get("person"))):
                continue
            state = plan.get("state")
            if scopes["state"] is not None:
                if not state and plan.get("scopeType", "city") == "city" and plan.get("city"):
                    candidates = city_states.get((channel, plan["city"]), set())
                    state = next(iter(candidates)) if len(candidates) == 1 else None
                if not state or not _matches(scopes, "state", state):
                    continue
            full = plan.get("full")
            if not isinstance(full, Mapping):
                full = dict(zip(MONTHS[:4], plan.get("t") or []))
            months = _month_amounts(full, scopes)
            reported = [month for month in plan.get("reportedMonths", full.keys())
                        if month in months]
            if not months or not reported:
                continue
            item = {"person": plan.get("person", ""), "city": plan.get("city", ""),
                    "channel": channel, "region": REGIONS.get(channel, channel),
                    "scopeType": plan.get("scopeType", "city"), "full": months,
                    "reportedMonths": reported,
                    "t": [months.get(month) for month in MONTHS[:4]],
                    "a": [None] * 4}
            if state:
                item["state"] = state
            kept.append(item)
        if kept:
            result[str(fy)] = kept
    return result


def _retailer_targets(source, scopes):
    if any(scopes[key] is not None for key in ("executive", "customer", "state")):
        return []
    result = []
    for target in source or []:
        if not isinstance(target, Mapping):
            continue
        if not all(_matches(scopes, field, target.get(key)) for field, key in
                   (("channel", "channel"), ("month", "month"),
                    ("brand", "brand"), ("distribution", "retailer"))):
            continue
        result.append({key: deepcopy(target[key]) for key in
                       ("fy", "month", "channel", "retailer", "brand", "amount") if key in target})
    return result


def _empty_secondary():
    return {"available": False, "distributors": [], "stores": [], "sales": [], "targets": [],
            "distributorTargets": [], "channelTargets": [], "secondaryTargets": [],
            "branchTargetPlans": [], "financialYears": [], "warnings": []}


def _secondary(source, channel, scopes):
    # Store names and secondary-sales measures have no reliable mapping to the
    # primary ledger's executive, customer, brand, distribution or state grain.
    if (not _matches(scopes, "channel", channel) or
            any(scopes[key] is not None for key in ("executive", "customer", "brand", "distribution", "state")) or
            not isinstance(source, Mapping)):
        return _empty_secondary()
    result = _empty_secondary()
    for key in ("sales", "targets", "distributorTargets", "channelTargets", "secondaryTargets"):
        result[key] = [deepcopy(row) for row in source.get(key, [])
                       if isinstance(row, Mapping) and _matches(scopes, "month", row.get("month"))]
    # Annual branch plans contain amounts outside a partial month grant. The
    # already-expanded monthly 'targets' above are sufficient for authorized use.
    if scopes["month"] is None:
        result["branchTargetPlans"] = deepcopy(source.get("branchTargetPlans", []))
    store_ids = {row.get("storeId") for key in ("sales", "targets", "branchTargetPlans") for row in result[key]}
    stores = [row for row in source.get("stores", []) if row.get("id") in store_ids]
    distributor_ids = {row.get("distributorId") for row in stores}
    distributor_ids.update(row.get("distributorId") for key in ("distributorTargets", "branchTargetPlans") for row in result[key])
    for row in result["channelTargets"] + result["secondaryTargets"]:
        distributor_ids.update(row.get("missingDistributorIds", []))
    result["stores"] = deepcopy(stores)
    result["distributors"] = [deepcopy(row) for row in source.get("distributors", []) if row.get("id") in distributor_ids]
    years = {_fy_from_month(row.get("month")) for key in ("sales", "targets", "distributorTargets", "channelTargets") for row in result[key]}
    years.update(str(row["fy"]) for row in result["branchTargetPlans"] if row.get("fy") is not None)
    result["financialYears"] = sorted(int(year) for year in years if year is not None)
    result["available"] = bool(source.get("available")) and bool(result["stores"] or result["distributors"])
    for key in ("targetBasis", "storeTargetBasis"):
        if key in source:
            result[key] = source[key]
    return result


def _fy_from_month(value):
    try:
        month, year = value.split("-")
        return str(int(year) - (1 if month in ("Jan", "Feb", "Mar") else 0)) if month in MONTHS else None
    except (AttributeError, TypeError, ValueError):
        return None


def _calendar(result, data, scopes):
    months_by_fy = {}
    for row in result["rows"]:
        months_by_fy.setdefault(str(row[11]), set()).add(row[0])
    planned_months = {}
    for fy, plans in result["targetsByFY"].items():
        planned_months.setdefault(fy, set()).update(month for plan in plans.values() for month in plan)
    for fy, plans in result["execByFY"].items():
        planned_months.setdefault(fy, set()).update(month for plan in plans for month in plan["reportedMonths"])
    for target in result["primaryRetailerTargets"]:
        short = _short_month(target.get('month'))
        if target.get("fy") is not None and short:
            planned_months.setdefault(str(target["fy"]), set()).add(short)
    secondary_months = {}
    for key in SECONDARY_CHANNELS:
        for name in ("sales", "targets", "distributorTargets", "channelTargets"):
            for row in result[key][name]:
                fy = _fy_from_month(row.get("month"))
                if fy:
                    if name == 'sales':
                        secondary_months.setdefault(fy, set()).add(row['month'])
                    else:
                        planned_months.setdefault(fy, set()).add(_short_month(row['month']))
    # Secondary history supplies years without primary invoices, but cannot
    # stretch a billed primary year with unrelated future secondary periods.
    for fy, months in secondary_months.items():
        months_by_fy.setdefault(fy, months)
    # Retain the existing convention: a sales year shows billed months; a
    # plan-only year may show its authorized planned months.
    for fy, months in planned_months.items():
        if fy not in months_by_fy:
            months_by_fy[fy] = {f"{month}-{int(fy) + (1 if month in ('Jan', 'Feb', 'Mar') else 0)}" for month in months}
    years = sorted(months_by_fy, key=int)
    current = str(data.get("fy", ""))
    result["fy"] = current if current in years else (years[-1] if years else current)
    result["fys"] = years
    result["fyLabels"] = {fy: f"{fy}-{str(int(fy)+1)[-2:]}" for fy in years}
    result["monthsByFY"] = {fy: sorted(months, key=lambda value: MONTHS.index(_short_month(value))) for fy, months in months_by_fy.items()}
    result["months"] = result["monthsByFY"].get(result["fy"], [])
    result["monthOrder"] = [month for month in MONTHS if _matches(scopes, "month", month)]
    result["plannedFys"] = [str(fy) for fy in data.get("plannedFys", []) if str(fy) in years]
    result["chanTargets"] = deepcopy(result["targetsByFY"].get(result["fy"], {}))
    result["cityTargets"] = deepcopy(result["execByFY"].get(result["fy"], []))


def project_data(data, policy):
    """Return an independent payload containing only granted data.

    A canonical policy has scopes for all seven dimensions; None means all
    values, an empty list means none. Section/page visibility is intentionally
    separate and cannot add any data to this projection.
    """
    scopes = _scopes(policy)
    if all(value is None for value in scopes.values()):
        result = deepcopy(data)
        if 'onlineSales' in data:
            result['onlineSales'] = project_online(data['onlineSales'], policy)
        return result
    result = {key: deepcopy(data[key]) for key in STATIC_KEYS if key in data}
    for key in ("rows", "productRows", "productCoverageRows", "colourRows"):
        result[key] = [deepcopy(row) for row in data.get(key, []) if _row_allowed(row, scopes)]
    colours = {row[7] for row in result["colourRows"]}
    result["colourList"] = [deepcopy(colour) for colour in data.get("colourList", [])
                            if isinstance(colour, Mapping) and colour.get("name") in colours]
    result["productSalesAvailable"] = bool(data.get("productSalesAvailable"))
    if 'onlineSales' in data:
        result['onlineSales'] = project_online(data['onlineSales'], policy)
    result["targetsByFY"] = _channel_targets(data.get("targetsByFY", {}), scopes)
    result["execByFY"] = _exec_targets(data.get("execByFY", {}), scopes, _city_states(data.get("rows", [])))
    result["primaryRetailerTargets"] = _retailer_targets(data.get("primaryRetailerTargets", []), scopes)
    for key, channel in SECONDARY_CHANNELS.items():
        result[key] = _secondary(data.get(key), channel, scopes)
    # These imported aggregates cannot be reconciled to the granted rows.
    result.update({"acctTargets": {}, "hist": [], "histLabels": [], "lfo2": [],
                   "outletsByTerritory": [], "outletsByCategory": [], "pipeline": [],
                   "mtd": None, "rotn": None, "fromSnapshot": [], "snapshotDate": None,
                   "primaryId": "", "suppId": "", "tabs": None, "splitSource": "PostgreSQL",
                   "pg": {"ledgerRows": len(result["rows"])}})
    _calendar(result, data, scopes)
    return result
