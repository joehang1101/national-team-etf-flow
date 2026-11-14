"""Deterministic CSV exports with explicit missingness, lineage and manifests."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from universe import CONFIG, members_on


def write_csv(path, rows, fields):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    content = stream.getvalue().encode('utf-8-sig')
    target = Path(path); tmp = target.with_suffix(target.suffix+'.tmp')
    tmp.write_bytes(content); os.replace(tmp, target)
    return {'rows': len(rows), 'sha256': hashlib.sha256(content).hexdigest()}


def export_data(payload, directory):
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    raw = {(r['d'], r['c']): r for r in payload['daily_records']}
    fields = ['date','code','name','category','role','shares','nav','net_flow_cny','share_delta',
              'share_source','previous_share_source','observed_at','status','source_verified',
              'backtest_eligible','pit_eligible','event_source','event_published_at','universe_version']
    rows = []
    entries = {e['code']: e for e in CONFIG['entries']}
    etfs = {e['c']: e for e in payload['etfs']}
    historical_codes = {code for _, code in raw}
    for code in historical_codes:
        if code not in etfs and code in entries:
            e=entries[code]
            etfs[code]={'c':code,'n':e['name'],'cat':e['category']}
    for date in payload['dates']:
        day = date.replace('-','')
        active = {e['code'] for e in members_on(day)}
        for etf in etfs.values():
            r = raw.get((day, etf['c']), {})
            rows.append(dict(r, date=date, code=etf['c'],name=etf['n'], category=etf['cat'],
                             role=entries.get(etf['c'],{}).get('role'), shares=r.get('s'), nav=r.get('w'),
                             net_flow_cny=r.get('f'), share_delta=r.get('delta'),
                             status=r.get('status','missing_share' if etf['c'] in active else 'outside_universe'), universe_version=CONFIG['version']))
    manifest = {'schema_version':1,'data_asof':payload['data_asof'],'universe_version':CONFIG['version'],
                'vintage':'latest_revised','point_in_time_backtest_ready':False,
                'missing_values':'empty CSV fields; never interpreted as zero','files':{}}
    repo = Path(__file__).resolve().parent
    manifest['input_sha256'] = {name: hashlib.sha256((repo/name).read_bytes()).hexdigest()
        for name in ['config/universe.json','config/events.json','config/anchors.json',
                     '_etf_shares_cache.json','_nav_cache.json','_kline_cache.json',
                     'calculations.py','compute.py','attribution.py'] if (repo/name).exists()}
    manifest['files']['daily_etf.csv'] = write_csv(root/'daily_etf.csv',rows,fields)
    catrows, indexrows = [], []
    for i,date in enumerate(payload['dates']):
        day=date.replace('-','')
        active = {e['code'] for e in members_on(day)}
        for category in sorted({e['cat'] for e in etfs.values()}):
            codes = [e['c'] for e in etfs.values() if e['cat']==category and e['c'] in active]
            values = [raw.get((day,c),{}) for c in codes]
            flows = [r['f'] for r in values if r.get('f') is not None]
            catrows.append({'date':date,'category':category,'net_flow_cny':sum(flows) if flows else None,
                            'covered':len(flows),'source_verified':sum(r.get('source_verified',False) for r in values),
                            'expected':len(codes)})
        for index,series in [('中证全指',payload['kline'])]+list(payload['cat_kline'].items()):
            k = series[i] if i < len(series) else None
            indexrows.append({'date':date,'index':index,'close':k.get('c') if k else None,
                              'is_proxy':index=='MSCI中国A50','source':'market_cache_or_live', 'pit_eligible':False})
    manifest['files']['daily_categories.csv']=write_csv(root/'daily_categories.csv',catrows,['date','category','net_flow_cny','covered','source_verified','expected'])
    manifest['files']['daily_indices.csv']=write_csv(root/'daily_indices.csv',indexrows,['date','index','close','is_proxy','source','pit_eligible'])
    signals=payload.get('attribution',[])
    manifest['files']['attribution_daily.csv']=write_csv(root/'attribution_daily.csv',signals,
        ['date','code','label','evidence_grade','reason','residual_rate','extremeness_percentile','control_count','training_count','beta','fit_r2','anchor_source','identity_probability','pit_eligible'])
    intervals=payload.get('anchor_intervals',[])
    manifest['files']['anchor_intervals.csv']=write_csv(root/'anchor_intervals.csv',intervals,
        ['code','holder','start','end','available_at','share_change','status','daily_path_identifiable','source_url'])
    bounds=payload.get('holding_bounds',[])
    manifest['files']['holding_bounds.csv']=write_csv(root/'holding_bounds.csv',bounds,
        ['date','code','anchor_date','anchor_holdings','holding_min','holding_max','change_min','change_max',
         'minimum_holding_reduction','available_at','assumption','not_etf_redemptions'])
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return manifest
