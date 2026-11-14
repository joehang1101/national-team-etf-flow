"""Pure calculations shared by online refresh and cached rebuilds."""
from math import isfinite


def latest_common_day(targets, shares, dates, allow_single=True):
    markets = {c: m for c, _, m in targets}
    sh = {d for d in dates if any(markets.get(c) == 'sh' for c in shares.get(d, {}))}
    sz = {d for d in dates if any(markets.get(c) == 'sz' for c in shares.get(d, {}))}
    common = sh & sz
    eligible = common or ((sh | sz) if allow_single else set())
    return max(eligible) if eligible else None


def positive_number(value, allow_zero=False):
    try:
        value = float(value)
        return value if isfinite(value) and (value >= 0 if allow_zero else value > 0) else None
    except (TypeError, ValueError):
        return None


def calculate_daily(targets, shares, nav, dates, trade_days=None, share_meta=None, events=None, memberships=None):
    """Never attribute a multi-session change or an old NAV to a single day.

    Missing observations remain None. A possible split is flagged for review,
    rather than silently labelled a genuine zero subscription.
    """
    expected = sorted(trade_days or dates)
    previous = {d: expected[i - 1] for i, d in enumerate(expected) if i}
    daily = []
    event_map = {(e['date'].replace('-', ''), e['code']): e for e in (events or [])}
    if memberships is not None:
        from universe import members_on
        active_by_day = {d: {e['code'] for e in members_on(d, entries=memberships)} for d in dates}
    for code, name, market in targets:
        for day in dates:
            if memberships is not None and code not in active_by_day[day]:
                continue
            value = positive_number(shares.get(day, {}).get(code), allow_zero=True)
            if value is None:
                continue
            prev_day = previous.get(day)
            prev = positive_number(shares.get(prev_day, {}).get(code), allow_zero=True)
            unit_nav = positive_number(nav.get(code, {}).get(day))
            prev_nav = positive_number(nav.get(code, {}).get(prev_day))
            flow, delta, status = None, None, "missing_previous_share"
            source = (share_meta or {}).get(day, {}).get(code, {}).get('source', 'legacy_unknown')
            prev_source = (share_meta or {}).get(prev_day, {}).get(code, {}).get('source', 'legacy_unknown')
            event = event_map.get((day, code))
            if prev is not None:
                delta = value - prev
                status = "missing_nav" if unit_nav is None else "ok"
                if unit_nav is not None:
                    flow = delta * unit_nav
                    if event:
                        factor = positive_number(event.get('factor'))
                        if not (factor and event.get('source_url') and event.get('published_at') and event.get('verified')):
                            flow, delta, status = None, None, 'unverified_event'
                        else:
                            delta = value - prev * factor
                            flow, status = delta * unit_nav, 'confirmed_split'
                    elif prev and prev_nav and value:
                        rs, rn = value / prev, unit_nav / prev_nav
                        tolerance = .002 if abs(rs - 1) > .30 else .00001
                        if abs(rs - 1) > .005 and abs(rn - 1) > .005 and (rs - 1) * (rn - 1) < 0 and abs(rs * rn - 1) < tolerance:
                            flow, delta, status = None, None, "possible_split"
            if source in {'fallback', 'eastmoney_f84'} or prev_source in {'fallback', 'eastmoney_f84'}:
                flow, delta, status = None, None, 'unverified_share'
            source_verified = source in {'sse', 'szse'} and prev_source in {'sse', 'szse'}
            daily.append({"d": day, "c": code, "n": name, "m": market,
                          "s": value, "w": unit_nav, "f": flow,
                          "delta": delta, "status": status, 'previous_date': prev_day,
                          'previous_share': prev, 'previous_nav': prev_nav,
                          'share_source': source, 'previous_share_source': prev_source,
                          'source_verified': source_verified,
                          'backtest_eligible': source_verified and flow is not None,
                          'pit_eligible': False,
                          'observed_at': (share_meta or {}).get(day, {}).get(code, {}).get('observed_at'),
                          'event_source': event.get('source_url') if event else None,
                          'event_published_at': event.get('published_at') if event else None})
    return daily
