"""Observation lineage and bounded repairs independent of the recent window."""
from datetime import datetime, timezone
from calculations import positive_number

OFFICIAL = {'sse', 'szse'}
SOURCE_URLS = {'sse': 'https://www.sse.com.cn/assortment/fund/etf/list/scale/',
               'szse': 'https://www.szse.cn/market/fund/volume/etf/index.html'}


def migrate_cache(cache):
    cache.setdefault('shares', {})
    meta = cache.setdefault('share_meta', {})
    for day, values in cache.get('shares', {}).items():
        for code in values:
            meta.setdefault(day, {}).setdefault(code, {'source': 'legacy_unknown', 'observed_at': None})
    cache['schema_version'] = 2
    return cache


def merge_observation(cache, day, code, value, source, observed_at=None):
    value = positive_number(value, allow_zero=True)
    if value is None:
        return False
    old = cache.setdefault('share_meta', {}).get(day, {}).get(code, {})
    if old.get('source') in OFFICIAL and source not in OFFICIAL:
        return False
    old_value = cache.setdefault('shares', {}).get(day, {}).get(code)
    # Do not churn timestamps when an identical official observation is fetched again.
    if old_value == value and old.get('source') == source:
        return False
    stamp = observed_at or datetime.now(timezone.utc).isoformat()
    cache['shares'].setdefault(day, {})[code] = value
    cache['share_meta'].setdefault(day, {})[code] = {
        'source': source, 'source_url': SOURCE_URLS.get(source), 'observed_at': stamp,
        'replaces_source': old.get('source'), 'previous_value': old_value,
    }
    return True


def repair_dates(cache, normal_window, limit=8):
    """Old fallback/unknown observations stay queued until verified, with fair retries."""
    normal = set(normal_window)
    attempts = cache.setdefault('repair_attempts', {})
    candidates = []
    for day, values in cache.get('share_meta', {}).items():
        if day in normal:
            continue
        sources = {v.get('source') for v in values.values()}
        if sources - OFFICIAL:
            priority = 0 if sources & {'fallback', 'eastmoney_f84'} else 1
            candidates.append((priority, attempts.get(day, ''), day))
    selected = [d for _, _, d in sorted(candidates)[:max(0, limit)]]
    stamp = datetime.now(timezone.utc).isoformat()
    for day in selected:
        attempts[day] = stamp
    return sorted(normal | set(selected))


def lineage_summary(cache):
    counts = {}
    pending = set()
    for day, values in cache.get('share_meta', {}).items():
        for value in values.values():
            source = value.get('source', 'legacy_unknown')
            counts[source] = counts.get(source, 0) + 1
            if source not in OFFICIAL:
                pending.add(day)
    return {'counts': counts, 'pending_dates': len(pending)}
