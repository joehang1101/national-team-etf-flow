"""Exploratory common-flow residuals, never a calibrated identity probability."""
from statistics import median, mean
from math import sqrt
from datetime import datetime


def known_anchors(anchors, code, day):
    return sorted([a for a in anchors if a.get('verified') and a.get('source_url')
                   and a.get('shares', 0) > 0
                   and a['code'] == code and a['report_date'] <= day
                   and a['published_at'][:10].replace('-', '') <= day], key=lambda a: a['report_date'])


def anchor_intervals(anchors):
    """Retrospective endpoint change only; no interpolation into invented daily trades."""
    groups = {}
    for a in anchors:
        if a.get('verified') and a.get('source_url'):
            groups.setdefault((a['code'], a['holder']), []).append(a)
    rows = []
    for (code, holder), values in groups.items():
        values.sort(key=lambda a: a['report_date'])
        for left, right in zip(values, values[1:]):
            same_basis = left.get('share_basis') == right.get('share_basis') and bool(left.get('share_basis'))
            rows.append({'code': code, 'holder': holder, 'start': left['report_date'], 'end': right['report_date'],
                         'available_at': max(left['published_at'], right['published_at']),
                         'share_change': right['shares'] - left['shares'] if same_basis else None,
                         'status': 'endpoint_change_only' if same_basis else 'incomparable_share_basis',
                         'daily_path_identifiable': False, 'source_url': right['source_url']})
    return rows


def holding_bounds(daily, anchors):
    """Conditional capacity bounds, not daily trade reconstruction.

    Assumes reported units remain comparable (no unrecorded split). A decrease
    can occur through secondary-market transfers, not just ETF redemptions.
    """
    output = []
    codes = {a['code'] for a in anchors}
    for row in daily:
        if row['c'] not in codes or row.get('share_source') not in {'sse','szse'}:
            continue
        usable = known_anchors(anchors, row['c'], row['d'])
        by_holder = {}
        for a in usable:
            by_holder[a['holder']] = a
        latest = list(by_holder.values())
        if not latest or len({a['report_date'] for a in latest}) != 1 or len({a['share_basis'] for a in latest}) != 1:
            continue
        base = sum(a['shares'] for a in latest)
        output.append({'date':row['d'],'code':row['c'],'anchor_date':latest[0]['report_date'],
                       'anchor_holdings':base,'holding_min':0,'holding_max':row['s'],
                       'change_min':-base,'change_max':row['s']-base,
                       'minimum_holding_reduction':max(0,base-row['s']),
                       'available_at':max(a['published_at'] for a in latest),
                       'assumption':'same share units; no unrecorded splits',
                       'not_etf_redemptions':True})
    return output


def attribute(daily, entries, anchors, min_training=20, lookback=60):
    """OLS on prior observations only; controls require explicit evidence and review.

    Training rows use current-vintage data; output is not a point-in-time backtest.
    Statistical extremeness and evidence grade are separate, neither is P(identity).
    """
    by_day = {}
    for row in daily:
        by_day.setdefault(row['d'], {})[row['c']] = row
    dates = sorted(by_day)
    result, history = [], {}
    for day in dates:
        active = [e for e in entries if (not e.get('known_from') or e['known_from'].replace('-', '') <= day)
                  and (not e.get('effective_from') or e['effective_from'].replace('-', '') <= day)
                  and (not e.get('effective_to') or day <= e['effective_to'].replace('-', ''))]
        controls = [e['code'] for e in active if e.get('role') == 'control_candidate'
                    and e.get('control_status') == 'reviewed' and e.get('control_source_url')
                    and (e.get('control_evidence_available_at') or e.get('known_from'))
                    and (e.get('control_evidence_available_at') or e['known_from'])[:10].replace('-','') <= day]
        clean_controls = [e['code'] for e in active if e['code'] in controls and e.get('control_identity_clean') is True]
        rows = by_day[day]
        def rate(code):
            r = rows.get(code, {})
            if r.get('event_published_at') and r['event_published_at'][:10].replace('-', '') > day:
                return None
            base = (r.get('previous_share') or 0) * (r.get('previous_nav') or 0)
            return r['f'] / base if r.get('backtest_eligible') and base > 0 and r.get('f') is not None else None
        control_rates = {code:value for code in controls if (value := rate(code)) is not None}
        clean_controls = [code for code in clean_controls if code in control_rates]
        xvalues = list(control_rates.values())
        x = median(xvalues) if len(xvalues) >= 3 else None
        for e in active:
            if e.get('role') == 'control_candidate':
                continue
            code = e['code']; y = rate(code)
            row = {'date': day, 'code': code, 'label': '证据不足', 'evidence_grade': '不足',
                   'identity_probability': None, 'residual_rate': None, 'extremeness_percentile': None,
                   'control_count': len(xvalues), 'training_count': 0, 'reason': '无足够已审查对照',
                   'pit_eligible': False, 'control_codes': '|'.join(controls),
                   'identity_clean_control_count': len(clean_controls), 'identity_evidence': '未识别交易主体'}
            prior = history.setdefault(code, [])[-lookback:]
            row['training_count'] = len(prior)
            if y is None:
                row['reason'] = '份额来源未核验、数据缺失或折算待核验'
            elif x is not None and len(prior) >= min_training:
                pairs=[(sample[0],sample[1]) for sample in prior]
                xs, ys = zip(*pairs); mx, my = mean(xs), mean(ys)
                variance = sum((v-mx)**2 for v in xs)
                if variance > 1e-16:
                    beta = sum((a-mx)*(b-my) for a,b in pairs) / variance
                    alpha = my - beta*mx
                    residuals = [b-alpha-beta*a for a,b in pairs]
                    residual = y-alpha-beta*x
                    percentile = sum(abs(v) <= abs(residual) for v in residuals) / len(residuals)
                    fit = 1 - sum(v*v for v in residuals) / max(sum((v-my)**2 for v in ys), 1e-16)
                    unusual = percentile >= .95 and abs(residual) > 1e-9
                    row.update(residual_rate=residual, extremeness_percentile=percentile,
                               beta=beta, fit_r2=fit, residual_sigma=sqrt(mean(v*v for v in residuals)),
                               label=('增量申购异常' if residual > 0 else '增量赎回异常') if unusual else '未发现显著增量偏离',
                               evidence_grade='低', reason='共同申赎调整后的统计判断，不能识别交易主体')
                    # Omit each current proxy from BOTH historical factors and today's factor, then refit.
                    residual_range=[]
                    for omitted in control_rates:
                        loo_pairs=[]
                        for _,old_y,old_rates in prior:
                            values=[v for c,v in old_rates.items() if c!=omitted]
                            if len(values)>=2:loo_pairs.append((median(values),old_y))
                        values=[v for c,v in control_rates.items() if c!=omitted]
                        if len(loo_pairs)>=min_training and len(values)>=2:
                            lx,ly=zip(*loo_pairs); lm,ym=mean(lx),mean(ly)
                            lv=sum((v-lm)**2 for v in lx)
                            if lv>1e-16:
                                lb=sum((a-lm)*(b-ym) for a,b in loo_pairs)/lv
                                residual_range.append(y-(ym-lb*lm)-lb*median(values))
                    row['residual_low']=min(residual_range) if residual_range else None
                    row['residual_high']=max(residual_range) if residual_range else None
                    row['sensitivity_refits']=len(residual_range)
                    row['sign_stable'] = len(residual_range)==len(control_rates) and (all(v>0 for v in residual_range) or all(v<0 for v in residual_range))
                    if fit < .1:
                        row.update(label='共同因子解释力不足',reason='R²不足0.1，无法稳健扣除共同申赎')
                    elif unusual and not row['sign_stable']:
                        row.update(label='对照敏感，暂不归因',reason='逐只剔除对照会改变残差方向')
                    usable = known_anchors(anchors, code, day)
                    anchor = usable[-1] if usable else None
                    recent = anchor and (datetime.strptime(day,'%Y%m%d')-datetime.strptime(anchor['report_date'],'%Y%m%d')).days <= 366
                    row['anchor_source'] = anchor['source_url'] if anchor else None
                    if unusual and fit >= .1 and recent and len(clean_controls)>=3 and row['sign_stable']:
                        row.update(label='疑似国家队增量申购' if residual > 0 else '疑似国家队增量赎回',
                                   evidence_grade='中', reason='已审查对照、历史锚点及异常残差共同支持；未经身份核验')
                    elif len(clean_controls)<3:
                        row['identity_evidence']='对照身份未排除污染，机构归因证据不足'
                else:
                    row['reason'] = '对照波动不足，无法识别共同因子'
            elif x is not None:
                row['reason'] = '训练样本不足'
            if x is not None and y is not None:
                history[code].append((x, y, control_rates.copy()))
            result.append(row)
    return result
