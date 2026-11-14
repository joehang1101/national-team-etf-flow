"""Descriptive, prior-only rolling features. No trade or identity probabilities."""
from math import isfinite, log, sqrt
from statistics import mean, median, stdev
from universe import members_on


VERSION = '1.0'
LARGE = {'沪深300', '上证50'}
SMALL = {'中证1000', '创业板', '科创板50'}


def finite(value):
    return isinstance(value, (int, float)) and isfinite(value)


def flow_rate(row, day):
    base = (row.get('previous_share') or 0) * (row.get('previous_nav') or 0)
    published = (row.get('event_published_at') or '')[:10].replace('-', '')
    if (not row.get('source_verified') or not finite(row.get('f')) or base <= 0
            or (published and published > day)):
        return None
    return row['f'] / base


def window_value(values, signatures, end, size, average=False):
    start = end - size + 1
    if start < 0:
        return None
    part = values[start:end + 1]
    if len(set(signatures[start:end + 1])) != 1 or not all(finite(v) for v in part):
        return None
    return mean(part) if average else sum(part)


def standardize(value, baseline):
    """Exactly 60 prior observations; constant/missing history is not zero signal."""
    n = sum(finite(v) for v in baseline)
    result = {'z': None, 'percentile': None, 'mean': None, 'sigma': None, 'samples': n}
    if len(baseline) != 60 or n != 60 or not finite(value):
        return result
    mu, sigma = mean(baseline), stdev(baseline)
    result.update(mean=mu, sigma=sigma)
    if sigma <= 1e-12:
        return result
    result.update(z=(value - mu) / sigma,
                  percentile=sum(v <= value for v in baseline) / 60)
    return result


def rolling_features(values, signatures, i, window_cache=None):
    result = {}
    for size in (1, 5, 10, 20):
        if window_cache is not None:
            cached = window_cache.setdefault(size, [])
            for end in range(len(cached), i + 1):
                cached.append(window_value(values, signatures, end, size))
            value = cached[i]
        else:
            value = window_value(values, signatures, i, size)
        # The baseline ends BEFORE the current size-day window starts. Its 60
        # trailing windows overlap each other, but share no day with the target.
        ends = range(i - size - 59, i - size + 1)
        history = [(window_cache[size][end] if window_cache is not None else window_value(values, signatures, end, size)) if end >= 0 and
                   signatures[end] == signatures[i] else None for end in ends]
        stats = standardize(value, history)
        prefix = f'flow_{size}d'
        result.update({prefix + '_rate': value, prefix + '_z': stats['z'],
                       prefix + '_samples': stats['samples']})
        if size == 1:
            result.update(rate_mean_60=stats['mean'], rate_sigma_60=stats['sigma'],
                          rate_percentile_60=stats['percentile'])
    result['mean_5'] = window_value(values, signatures, i, 5, average=True)
    result['mean_20'] = window_value(values, signatures, i, 20, average=True)
    result['mean_5_minus_20'] = (result['mean_5'] - result['mean_20']
                               if result['mean_5'] is not None and result['mean_20'] is not None else None)
    for direction, name in ((1, 'inflow_streak'), (-1, 'outflow_streak')):
        count = 0
        if finite(values[i]):
            for j in range(i, -1, -1):
                if signatures[j] != signatures[i] or not finite(values[j]) or direction * values[j] <= 0:
                    break
                count += 1
            result[name] = count
        else:
            result[name] = None
    return result


def price_features(closes, i):
    def segment(size):
        part = closes[max(0, i-size+1):i+1]
        return part if len(part) == size and all(finite(v) and v > 0 for v in part) else None
    result = {}
    for size in (1, 3):
        part = segment(size + 1)
        result[f'return_{size}d'] = part[-1] / part[0] - 1 if part else None
    part = segment(60)
    result['drawdown_60'] = part[-1] / max(part) - 1 if part else None
    part = segment(250)
    result['distance_ma250'] = part[-1] / mean(part) - 1 if part else None
    part = segment(21)
    result['volatility_20_annualized'] = stdev([log(b / a) for a, b in zip(part, part[1:])]) * sqrt(252) if part else None
    return result


def describe_state(row):
    """Transparent conjunctions, descriptive labels, never buy/sell prices."""
    rate, z, ret = row.get('flow_1d_rate'), row.get('flow_1d_z'), row.get('return_1d')
    if not finite(rate) or row['covered'] != row['expected']:
        return '数据不足', '候选池覆盖不全，暂不生成资金状态。'
    if z is None:
        return '历史基准不足', '需要相同成员、连续且完整的此前60个交易日；零波动基准也不生成z值。'
    if ret is None:
        return '行情证据不足', '资金特征可用，缺少相邻指数收盘，暂不作逆势判断。'
    breadth = row['inflow_share']
    if rate > 0 and z >= 2 and ret < 0 and breadth >= 2/3:
        return '下跌中的广泛流入', '正向资金强度z≥2、至少2/3候选净流入，同时中证全指下跌；符合托底形态，交易主体未确认。'
    if rate < 0 and z <= -2 and ret > 0 and breadth <= 1/3 and row['outflow_count'] / row['covered'] >= 2/3:
        return '上涨中的广泛流出', '负向资金强度z≤−2、至少2/3候选净流出，同时中证全指上涨；符合控速形态，交易主体未确认。'
    if abs(z) >= 2:
        return '资金强度偏离常态', '资金强度距此前60日均值至少两个标准差，逆势方向或广度未同时满足。'
    if (row.get('inflow_streak') or 0) >= 3:
        return '连续净流入', '连续至少3个完整交易日净流入；继续结合短长均值与市场背景观察。'
    if (row.get('outflow_streak') or 0) >= 3:
        return '连续净流出', '连续至少3个完整交易日净流出；持续性不等于未来价格方向。'
    return '常态区间观察', '资金强度z处于−2至2之间，未触发连续3日同向状态。'


def describe_context(row):
    if row.get('flow_1d_rate') is None:
        return '覆盖不足时保留缺失，等待数据核验后再观察。'
    parts = [f"{row['inflow_count']}/{row['covered']}只净流入"]
    if row.get('return_1d') is not None:
        parts.append(f"中证全指当日{row['return_1d']:+.2%}")
    if row.get('mean_5_minus_20') is not None:
        comparison = '高于' if row['mean_5_minus_20'] > 0 else '低于' if row['mean_5_minus_20'] < 0 else '等于'
        parts.append(f"5日平均相对流量{comparison}20日")
    parts.append('交易主体及策略有效性尚未确认')
    return '；'.join(parts) + '。'


def extract_features(payload, entries):
    dates = [d.replace('-', '') for d in payload.get('feature_trade_days', payload['dates'])]
    daily = {}
    for r in payload['daily_records']:
        daily.setdefault(r['d'], {})[r['c']] = r
    candidates = [e for e in entries if e.get('role') == 'candidate']
    codes = {e['code']: e for e in candidates}
    all_codes = sorted(codes)
    series = {code: [] for code in ['candidate_pool'] + all_codes}
    signatures = {code: [] for code in series}
    windows = {code: {} for code in series}
    records, pool_rows = [], []
    def closes_for(series):
        # Align by trading date, rather than trusting a shifted list position.
        mapping = {r['d'].replace('-', ''): r.get('c') for r in series if r and r.get('d')}
        return [mapping.get(d) for d in dates]
    prices = {'中证全指': closes_for(payload.get('kline', []))}
    prices.update({cat: closes_for(values) for cat, values in payload.get('cat_kline', {}).items()})
    model_by_day = {}
    for r in payload.get('attribution', []):
        model_by_day.setdefault(r['date'], []).append(r)
    for i, day in enumerate(dates):
        rows = daily.get(day, {})
        active = {e['code']: e for e in members_on(day, entries=candidates)}
        rates = {c: flow_rate(rows.get(c, {}), day) for c in active}
        covered = [c for c in active if rates[c] is not None]
        complete = bool(active) and len(covered) == len(active)
        flow = sum(rows[c]['f'] for c in covered) if complete else None
        base = sum(rows[c]['previous_share'] * rows[c]['previous_nav'] for c in covered) if complete else None
        pool = {'date': day, 'code': 'candidate_pool', 'name': '候选观察池', 'category': '全部候选',
                'expected': len(active), 'covered': len(covered), 'net_flow_cny': flow,
                'previous_assets_cny': base, 'source_verified': complete,
                'pit_eligible': False, 'feature_version': VERSION}
        signature = '|'.join(sorted(active))
        series['candidate_pool'].append(flow / base if base and base > 0 else None)
        signatures['candidate_pool'].append(signature)
        pool.update(rolling_features(series['candidate_pool'], signatures['candidate_pool'], i, windows['candidate_pool']))
        pool['inflow_count'] = sum(rates[c] > 0 for c in covered)
        pool['outflow_count'] = sum(rates[c] < 0 for c in covered)
        pool['inflow_share'] = pool['inflow_count'] / len(covered) if covered else None
        cats = {e['category'] for e in active.values()}
        catflows = {cat: sum(rows[c]['f'] for c in covered if active[c]['category'] == cat)
                    for cat in cats if all(rates[c] is not None for c in active if active[c]['category'] == cat)}
        pool.update(category_expected=len(cats), category_covered=len(catflows),
                    category_inflow_count=sum(v > 0 for v in catflows.values()),
                    category_outflow_count=sum(v < 0 for v in catflows.values()))
        pool['category_direction'] = ('覆盖不足' if not cats or len(catflows) != len(cats) else
            '全部净流入' if all(v > 0 for v in catflows.values()) else
            '全部净流出' if all(v < 0 for v in catflows.values()) else '方向分化')
        for side, direction in [('inflow', 1), ('outflow', -1)]:
            amounts = {c: max(0, direction * rows[c]['f']) for c in covered}
            total = sum(amounts.values())
            pool[side + '_gross_cny'] = total if complete else None
            for group, categories in [('large', LARGE), ('small', SMALL), ('other', cats - LARGE - SMALL)]:
                pool[f'{side}_{group}_share'] = (sum(v for c, v in amounts.items() if active[c]['category'] in categories) / total
                                                        if complete and total > 0 else None)
        pool.update(price_features(prices['中证全指'], i))
        controls = [e for e in members_on(day, entries=entries) if e.get('role') == 'control_candidate'
                    and e.get('control_status') == 'reviewed' and e.get('control_source_url')
                    and e.get('control_evidence_available_at')
                    and e['control_evidence_available_at'][:10].replace('-', '') <= day]
        cr = [v for e in controls if (v := flow_rate(rows.get(e['code'], {}), day)) is not None]
        pool['control_count'] = len(cr)
        pool['control_rate'] = median(cr) if len(cr) >= 3 else None
        pool['control_codes'] = '|'.join(e['code'] for e in controls if flow_rate(rows.get(e['code'], {}), day) is not None)
        pool['raw_control_gap'] = (pool['flow_1d_rate'] - pool['control_rate']
                                   if pool['flow_1d_rate'] is not None and pool['control_rate'] is not None else None)
        modeled = model_by_day.get(day, [])
        pool['residual_alert_count'] = sum(r['label'] in ('增量申购异常', '增量赎回异常') for r in modeled)
        pool['weak_fit_count'] = sum(r['label'] == '共同因子解释力不足' for r in modeled)
        pool['state'], pool['state_reason'] = describe_state(pool)
        pool['summary_text'] = describe_context(pool)
        pool_rows.append(pool)
        records.append(pool)
        for code in all_codes:
            e = codes[code]; r = rows.get(code, {})
            rate = rates.get(code)
            series[code].append(rate)
            signatures[code].append(code if code in active else '')
            if code not in active:
                continue
            record = {'date': day, 'code': code, 'name': e['name'], 'category': e['category'],
                      'expected': 1, 'covered': int(rate is not None), 'net_flow_cny': r.get('f') if rate is not None else None,
                      'previous_assets_cny': (r.get('previous_share') or 0)*(r.get('previous_nav') or 0) or None,
                      'source_verified': bool(r.get('source_verified')), 'pit_eligible': False, 'feature_version': VERSION}
            record.update(rolling_features(series[code], signatures[code], i, windows[code]))
            if e['category'] in prices:
                record.update(price_features(prices[e['category']], i))
            records.append(record)
    return {'version': VERSION, 'latest': pool_rows[-1] if pool_rows else {}, 'pool_history': pool_rows,
            'latest_etfs': [r for r in records if r['date'] == payload['data_asof'] and r['code'] != 'candidate_pool'],
            'records': records}
