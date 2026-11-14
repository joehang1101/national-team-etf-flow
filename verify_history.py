"""Reverify legacy observations from dated exchange records with resumable checkpoints."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import akshare as ak
from calculations import positive_number
from fetch import date_batches, retry
from provenance import migrate_cache, merge_observation, lineage_summary, OFFICIAL
from runtime import OUT_DIR, SHARES_CACHE, read_json, write_json, network_timeout
from universe import TARGETS


def pending_by_market(cache):
    market={code:mk for code,_,mk in TARGETS}
    pending={'sh':set(),'sz':set()}
    for day, values in cache['shares'].items():
        for code in values:
            if market.get(code) and cache['share_meta'].get(day,{}).get(code,{}).get('source') not in OFFICIAL:
                pending[market[code]].add(day)
    return {mk:sorted(days) for mk,days in pending.items()}


def fetch_exchange(market, days):
    if market=='sh':
        df=retry(lambda:ak.fund_etf_scale_sse(date=days[0]),tries=2,gap=1)
        date_col='统计日期'
    else:
        df=retry(lambda:ak.fund_scale_daily_szse(start_date=days[0],end_date=days[-1],symbol='ETF'),tries=2,gap=1)
        date_col='日期'
    if df is None or df.empty:
        return []
    allowed=set(days); codes={c for c,_,mk in TARGETS if mk==market}
    rows=[]
    for _,r in df.iterrows():
        day=str(r.get(date_col))[:10].replace('-',''); code=str(r.get('基金代码')).zfill(6)
        value=positive_number(r.get('基金份额'),allow_zero=True)
        if day in allowed and code in codes and value is not None:
            rows.append((day,code,value))
    return rows


def run(workers=4, limit=0):
    cache=migrate_cache(read_json(SHARES_CACHE)); pending=pending_by_market(cache)
    if limit:
        pending={mk:days[:limit] for mk,days in pending.items()}
    jobs=[('sh',[d]) for d in pending['sh']]+[('sz',batch) for batch in date_batches(pending['sz'],max_days=90)]
    # Mix markets so neither is starved behind years of single-day tasks.
    jobs.sort(key=lambda x:(x[1][0],x[0]))
    before=lineage_summary(cache); done=changed=failures=0
    audit=cache.setdefault('verification_audit',{})
    print('开始历史核验',len(jobs),'批',before,flush=True)
    with network_timeout(20), ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(fetch_exchange,mk,days):(mk,days) for mk,days in jobs}
        for fu in as_completed(futures):
            mk,days=futures[fu]; error=None
            try:
                rows=fu.result()
                for day,code,value in rows:
                    changed+=merge_observation(cache,day,code,value,'sse' if mk=='sh' else 'szse')
            except Exception as exc:
                failures+=1; rows=[]; error=f'{type(exc).__name__}: {str(exc)[:160]}'
            stamp=datetime.now(timezone.utc).isoformat()
            for day in days:
                audit.setdefault(day,{})[mk]={'attempted_at':stamp,'returned_rows':sum(r[0]==day for r in rows),'error':error}
            done+=1
            if done%25==0 or done==len(jobs):
                write_json(SHARES_CACHE,cache)
                print('进度',done,'/',len(jobs),'更新记录',changed,'失败批',failures,lineage_summary(cache),flush=True)
    write_json(SHARES_CACHE,cache)
    report={'started_with':before,'finished_with':lineage_summary(cache),'jobs':len(jobs),'failures':failures,'changed_records':changed}
    write_json(os.path.join(OUT_DIR,'data','history_verification.json'),report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workers',type=int,choices=range(1,7),default=4)
    p.add_argument('--limit',type=int,default=0,help='每市最多核验日期数，0为全部待核验日期')
    args=p.parse_args(); lock=Path(OUT_DIR)/'.refresh.lock'
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:
        raise SystemExit('已有刷新或核验任务运行中')
    try:
        with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
        print(json.dumps(run(args.workers,args.limit),ensure_ascii=False),flush=True)
    finally:
        lock.unlink(missing_ok=True)
