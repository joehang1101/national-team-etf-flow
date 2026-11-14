from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json, time, os, sys, shutil, requests, re
from contextlib import contextmanager
import argparse
from runtime import NAV_CACHE, OUT_DIR, REPORT, SHARES_CACHE, KLINE_CACHE, network_timeout, read_json, write_json, snapshot_fingerprint, last_published_state, now_bj
from universe import CAT_INDEX, ENTRIES, TARGETS
from fetch import CAL_DAYS, FRESH_WAIT, backfill_new_codes, expected_latest_cut, incremental_dates, load_kline_cache, load_nav, load_shares_cached, load_trade_calendar, market_kline, save_kline_cache, sync_day_of
from calculations import calculate_daily, positive_number
from compute import build_payload
from render import render_html
from provenance import migrate_cache, repair_dates, lineage_summary
from attribution import attribute, anchor_intervals, holding_bounds
from exports import export_data

def main(offline=False, fresh_retries=0, repair_limit=8):
    print("拉取数据中 ...", flush=True)
    cache = {}
    if os.path.exists(SHARES_CACHE):
        try:
            with open(SHARES_CACHE, encoding="utf-8-sig") as f:
                cache = json.load(f)
        except Exception as e:
            print("缓存损坏, 将全量重建:", e, flush=True)
            cache = {}
    migrate_cache(cache)
    window = incremental_dates(cache, CAL_DAYS)
    if not offline:
        window = repair_dates(cache, window, repair_limit)
    print("本次拉取日期窗口: %s ~ %s (共%d个工作日)" %
          (window[0] if window else "-", window[-1] if window else "-", len(window)), flush=True)
    trade_days = set(cache.get("trade_days", {}).get("days", [])) if offline else load_trade_calendar(cache)
    if trade_days:
        window = [d for d in window if d in trade_days or d > max(trade_days)]
    if offline:
        shares = cache.get("shares", {})
        valid = sorted(d for d, m in shares.items() if m and (not trade_days or d in trade_days))
        deep_start = cache.get("deep_start")
    else:
        shares, valid, deep_start = load_shares_cached(window, cache, trade_days)
    # 新增纳入标的(如 2026.10救市口径)无历史缓存: 一次性回补其历史份额, 保证近一月/近一年与 2025-12-31 基线可用
    if not offline and backfill_new_codes(cache, trade_days):
        shares = cache.get("shares", {})
        valid = sorted(d for d, m in shares.items() if m)
    # 数据新鲜度自检: 若展示日落后于最近已结束交易日(通常因数据源晚发布/单边失败), 等待后重试抓取
    exp = expected_latest_cut(trade_days)
    for _attempt in range(0 if offline else fresh_retries + 1):
        cur = sync_day_of(shares, valid)
        stale = cur is not None and int(cur) < exp
        print("数据新鲜度: 最新同步日=%s 期望=%s %s" %
              (cur, exp, "正常" if not stale else "滞后, 稍后重试"), flush=True)
        if not stale or not window or _attempt >= fresh_retries:
            break
        time.sleep(FRESH_WAIT)
        shares, valid, deep_start = load_shares_cached(window, cache, trade_days)
    # Undated real-time f84 snapshots cannot prove a historical observation date.
    # Only exchange-dated shares enter the daily cache and calculation.
    if not offline:
        write_json(SHARES_CACHE, cache)
    if not valid:
        raise RuntimeError("未取到任何交易日份额数据，保留上次报表")
    nav = read_json(NAV_CACHE) if offline else load_nav()
    if not nav:
        raise RuntimeError("没有净值缓存；请先联网运行 python refresh.py")
    daily = calculate_daily(TARGETS, shares, nav, valid, trade_days,
                            share_meta=cache.get("share_meta"), events=read_json(os.path.join(OUT_DIR,"config","events.json")).get("events", []), memberships=ENTRIES)
    cut = sync_day_of(shares, valid)
    dates = sorted({r['d'] for r in daily if r['d'] <= cut})
    kcache = load_kline_cache()
    cat_kline = {cat: market_kline(dates, kcache.setdefault("cat", {}).setdefault(cat, {}), sec, tx, offline)
                 for cat, (sec,tx) in CAT_INDEX.items()}
    kline = market_kline(dates, kcache.setdefault("kline", {}), "1.000985", "sh000985", offline)
    if not offline:
        save_kline_cache(kcache, dates)
    payload = build_payload(daily, shares, nav, valid, trade_days, exp, deep_start, cat_kline, kline, offline)
    payload["lineage"] = lineage_summary(cache)
    anchors = read_json(os.path.join(OUT_DIR, 'config', 'anchors.json')).get('anchors', [])
    payload['attribution'] = attribute(payload['daily_records'], ENTRIES, anchors)
    payload['anchor_intervals'] = anchor_intervals(anchors)
    payload['holding_bounds'] = holding_bounds(payload['daily_records'], anchors)
    payload['anchors'] = anchors
    payload['control_reviews'] = read_json(os.path.join(OUT_DIR,'config','control_reviews.json')).get('reviews', [])
    payload['history_verification'] = read_json(os.path.join(OUT_DIR,'data','history_verification.json'))
    payload['quality']['official_latest'] = sum(r['source_verified'] and r['f'] is not None for r in daily if r['d'] == payload['data_asof'])
    payload['fingerprint']=snapshot_fingerprint([SHARES_CACHE,NAV_CACHE,KLINE_CACHE]+[
        os.path.join(OUT_DIR,name) for name in ['config/universe.json','config/events.json','config/anchors.json',
                                              'config/control_reviews.json','calculations.py','attribution.py','compute.py']])
    previous=last_published_state()
    payload['updated']=(previous.get('updated') if previous and previous.get('fingerprint')==payload['fingerprint'] else None) or now_bj().strftime('%Y-%m-%d %H:%M')
    return payload

def cli():
    parser = argparse.ArgumentParser(description="采集ETF份额和净值，生成可离线使用的静态报表")
    parser.add_argument("--offline", action="store_true", help="仅使用份额、净值、K线缓存重建，不联网")
    parser.add_argument("--fresh-retries", type=int, default=0, choices=range(3), help="数据滞后额外重试次数（每次等待150秒），默认0")
    parser.add_argument("--require-fresh", action="store_true", help="最新数据滞后或覆盖不足时以非零退出，保留旧报表")
    parser.add_argument("--repair-days", type=int, default=8, help="每次额外修复旧缓存的日期数，0暂停历史修复")
    parser.add_argument('--require-official', action='store_true', help='最新日所有样本须有相邻两日官方份额来源，否则保留旧报表')
    args = parser.parse_args()
    lock = os.path.join(OUT_DIR, ".refresh.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print("已有刷新任务或遗留锁；确认没有运行中的刷新进程后删除 .refresh.lock", file=sys.stderr)
        return 1
    try:
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        with network_timeout():
            payload = main(offline=args.offline, fresh_retries=args.fresh_retries, repair_limit=args.repair_days)
        quality = payload["quality"]
        if args.require_fresh and (quality["is_stale"] or quality["latest_complete"] < quality["target_count"]):
            raise RuntimeError("数据日未达期望或最新交易日覆盖不足，保留原报表。")
        if args.require_official and quality.get('official_latest', 0) < quality['target_count']:
            raise RuntimeError('最新日官方份额来源覆盖不足，保留原报表。')
        render_html(payload)
        export_data(payload, os.path.join(OUT_DIR, 'data'))
        # Every entry point produces the same homepage, without a separate manual copy.
        for name in ("index.html", "report_latest.html"):
            target = os.path.join(OUT_DIR, name)
            shutil.copyfile(REPORT, target + ".tmp")
            os.replace(target + ".tmp", target)
        write_json(os.path.join(OUT_DIR, "_etf_data.json"), payload)
        print("数据更新完成，最新数据日:", payload["data_asof"], "覆盖: %d/%d" % (quality["latest_complete"], quality["target_count"]), flush=True)
        if quality["is_stale"]:
            print("注意：当前数据滞后于期望交易日", quality["expected_date"], flush=True)
        return 0
    except Exception as exc:
        print("刷新失败:", str(exc), file=sys.stderr)
        return 1
    finally:
        os.remove(lock)

if __name__ == "__main__":
    sys.exit(cli())
