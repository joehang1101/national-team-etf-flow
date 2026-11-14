from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json, time, os, sys, shutil, requests, re
from contextlib import contextmanager
import akshare as ak
from runtime import BEIJING, KLINE_CACHE, NAV_CACHE, now_bj, read_json, write_json
from universe import TARGETS
from calculations import positive_number, latest_common_day
from provenance import migrate_cache, merge_observation

FRESH_WAIT=150
CAL_DAYS=1850
_EM_DOWN=False
_TX_DOWN=False
SH_BACKFILL_FROM="20251231"
SH_BACKFILL_WORKERS=8
COVER_MIN=60

def gen_cal_days(start_d, end_d):
    """start_d..end_d 之间的工作日(周一~周五), 返回 YYYYMMDD 序列"""
    out, cur = [], start_d
    while cur <= end_d:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out

def incremental_dates(cache, cal_days):
    """增量拉取窗口: 有缓存则只拉最近若干天(含补拉最近12天以便捕获晚发布), 无缓存则全量构建;
    缓存起始晚于5年窗口时, 从5年起点补齐缺失历史(仅需一次, 补足后缓存即含完整5年)."""
    hist = (cache or {}).get("shares", {})
    today = now_bj()
    if not hist:
        return natural_dates(cal_days)
    try:
        lastc = datetime.strptime(max(hist), "%Y%m%d").replace(tzinfo=BEIJING)
        firstc = datetime.strptime(min(hist), "%Y%m%d").replace(tzinfo=BEIJING)
    except Exception:
        lastc = today - timedelta(days=1); firstc = today
    five_years = today - timedelta(days=cal_days)   # 5年窗口起点(约5年前同日期)
    start = lastc + timedelta(days=1)
    if firstc > five_years:
        start = five_years   # 历史不足, 从5年起点补齐
    refresh = today - timedelta(days=12)
    if start > refresh:
        start = refresh
    return gen_cal_days(start, today)

def load_trade_calendar(cache):
    """A股交易日历(YYYYMMDD集合). 优先复用缓存(须覆盖到今日之后), 否则拉取新浪交易日历并写回缓存.
    失败时返回已缓存的部分或 None, 上层退化为仅按工作日过滤(不会中断报表生成)."""
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    td = cache.get("trade_days") or {}
    days = sorted(td.get("days") or [])
    if days and days[-1] >= today:
        return set(days)
    try:
        df = ak.tool_trade_date_hist_sina()
        days = sorted({str(x)[:10].replace("-", "") for x in df["trade_date"]})
        if days:
            cache["trade_days"] = {"days": days, "end": days[-1]}
            print("交易日历已更新: %s ~ %s (共%d个交易日)" % (days[0], days[-1], len(days)), flush=True)
            return set(days)
    except Exception as e:
        print("交易日历拉取失败(退化为工作日过滤):", str(e)[:80], flush=True)
    return set(days) if days else None

def expected_latest_cut(trade_days=None):
    """最近一个已结束交易日(YYYYMMDD), 按北京时间; 周末与法定节假日自动回落到上一交易日.
    日期超出日历覆盖范围(如跨年日历尚未更新)时退化为仅按工作日判断, 避免误判."""
    current = now_bj()
    d = current.date() if current.hour >= 16 else (current - timedelta(days=1)).date()
    cal_last = max(trade_days) if trade_days else ""
    for _ in range(400):
        ds = d.strftime("%Y%m%d")
        if d.weekday() < 5 and (ds > cal_last or ds in trade_days):
            return int(ds)
        d -= timedelta(days=1)
    return int(d.strftime("%Y%m%d"))

def sync_day_of(shares, valid):
    return latest_common_day(TARGETS, shares, valid)

def load_shares_cached(window_dates, cache, trade_days=None):
    """拉取窗口数据并合并进持久化缓存, 返回 (合并后shares, 所有有数据日期, deep_start).
    trade_days 非空时: 丢弃非交易日(法定节假日/周末)份额, 防止节假日被快照兜底误填成"幽灵交易日"污染X轴."""
    migrate_cache(cache)
    hist = cache.setdefault("shares", {})
    fetched, valid_fetch, ds_start = load_shares(window_dates)
    for d, m in fetched.items():
        if m and (not trade_days or d in trade_days):
            for code, value in m.items():
                market = next((mk for c, _, mk in TARGETS if c == code), None)
                if market:
                    merge_observation(cache, d, code, value, "sse" if market == "sh" else "szse")
    if trade_days:
        cal_last = max(trade_days)
        for d in [x for x in list(hist) if x not in trade_days and x <= cal_last]:
            del hist[d]
    # Keep retired universe members in historical storage; filter only newly fetched records.
    if ds_start:
        cached_ds = cache.get("deep_start")
        if not cached_ds or str(ds_start) < str(cached_ds):
            cache["deep_start"] = ds_start
    cached_days = sorted(hist.keys())
    valid = sorted(d for d in cached_days if hist[d])
    return hist, valid, cache.get("deep_start")

def retry(fn, tries=3, gap=2):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:90]}"
            time.sleep(gap)
    raise RuntimeError(last)

def natural_dates(n):
    today = now_bj()
    return gen_cal_days(today - timedelta(days=n), today)

def _em_kline(secid, beg, end):
    """东财单次日K请求. 注意: smplmt 即单次返回条数上限, 设小了长区间会被截断(旧的 smplmt=1000 只能拿999根)."""
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           "?fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56"
           "&ut=7eea3edcaed734bea9cbfc24409ed989&klt=101&fqt=0&secid=%s"
           "&beg=%s&end=%s&smplmt=10000&lmt=200000" % (secid, beg, end))
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

    def _get():
        j = requests.get(url, headers=headers, timeout=15).json()
        if not isinstance(j, dict):
            raise ValueError("bad resp type")
        ks = (j or {}).get("data", {}).get("klines")
        if not ks:
            raise ValueError("empty klines")
        return ks

    return retry(_get, tries=2, gap=1)

def fetch_kline(dates, secid="1.000985"):
    """直连东财拉取指数(secid, 默认中证全指000985)日K, 按 dates 对齐返回 [{d,o,h,l,c}|None].
    按年分片请求后合并, 避免单次请求条数上限导致早段被截断; 部分年份失败时其余年份仍然可用."""
    if not dates:
        return []
    global _EM_DOWN
    if _EM_DOWN:
        raise ValueError("东财K线不可达(本轮已跳过)")
    m = {}
    err = None
    fails = 0
    for y in range(int(dates[0][:4]), int(dates[-1][:4]) + 1):
        try:
            for line in _em_kline(secid, "%d0101" % y, "%d1231" % y):
                f = line.split(",")
                k = f[0].replace("-", "")
                if k.isdigit() and len(k) == 8:
                    # 顺序: 日期,开,收,高,低,...
                    m[k] = [round(float(f[1]), 3), round(float(f[3]), 3),
                            round(float(f[4]), 3), round(float(f[2]), 3)]
            fails = 0
        except Exception as e:
            err = e
            fails += 1
            print("  东财K线分片失败 %s %d年: %s" % (secid, y, str(e)[:50]), flush=True)
            # 开头连续两年都失败(多为网络不可达), 立即放弃东财, 交给备用源, 避免逐片空转重试
            if fails >= 2 and not m:
                _EM_DOWN = True
                raise ValueError("东财K线不可达: %s" % (err,))
    if not m:
        if any(word in str(err) for word in ("ProxyError", "ConnectionError", "Timeout")):
            _EM_DOWN = True
        raise ValueError("empty klines: %s" % (err,))
    return [({"d": d, "o": m[k][0], "h": m[k][1], "l": m[k][2], "c": m[k][3]}
             if (k := d.replace("-", "")) in m else None) for d in dates]

def fetch_kline_tx(dates, tx_code="sh000985"):
    """备选源(腾讯) 指数(sh000985)日K, 与 fetch_kline 同返回格式. 行: 日期,开,收,高,低,量
    同样按年分片, 避免 count 上限截断早段."""
    if not dates:
        return []
    global _TX_DOWN
    if _TX_DOWN:
        raise ValueError("腾讯K线不可达(本轮已跳过)")

    def _d8(x):
        s = str(x)
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    m = {}
    err = None
    fails = 0
    for y in range(int(dates[0][:4]), int(dates[-1][:4]) + 1):
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               "?param=%s,day,%d-01-01,%d-12-31,800,qfq" % (tx_code, y, y))

        def _get():
            j = requests.get(url, headers=headers, timeout=15).json()
            if not isinstance(j, dict):
                raise ValueError("bad resp type")
            d = j.get("data")
            rows = None
            if isinstance(d, dict):
                for sym in (tx_code, "qfqday", "day"):
                    v = d.get(sym)
                    if isinstance(v, dict):
                        rows = v.get("qfqday") or v.get("day") or v.get("data")
                    elif isinstance(v, list) and v:
                        rows = v
                    if rows:
                        break
            elif isinstance(d, list) and d:
                rows = d
            if not rows:
                raise ValueError("empty tx klines")
            return rows

        try:
            rows = retry(_get, tries=2, gap=1)
        except Exception as e:
            err = e
            fails += 1
            print("  腾讯K线分片失败 %s %d年: %s" % (tx_code, y, str(e)[:50]), flush=True)
            # 开头连续两年失败且无任何数据(多为限流/不可达), 后续类别直接走缓存, 避免逐类空转重试
            if fails >= 2 and not m:
                _TX_DOWN = True
                raise ValueError("腾讯K线不可达: %s" % (err,))
            continue
        fails = 0
        for f in rows:
            try:
                if len(f) >= 5 and "-" in str(f[0]):
                    k = str(f[0]).replace("-", "")
                    m[k] = [round(float(f[1]), 3), round(float(f[3]), 3),
                            round(float(f[4]), 3), round(float(f[2]), 3)]
            except Exception:
                pass
    if not m:
        if any(word in str(err) for word in ("ProxyError", "ConnectionError", "Timeout")):
            _TX_DOWN = True
        raise ValueError("empty tx klines: %s" % (err,))
    return [({"d": d, "o": m[k][0], "h": m[k][1], "l": m[k][2], "c": m[k][3]}
             if (k := d.replace("-", "")) in m else None) for d in dates]

def load_kline_cache():
    try:
        j = read_json(KLINE_CACHE)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}

def merge_kline(dates, arr, cmap):
    """用缓存 cmap(键=YYYYMMDD -> [开,高,低,收]) 补齐 arr 中的 None, 同时把本次有效数据回写 cmap.
    保证取数偶发失败时图表不出现整段空白."""
    out, hit = [], 0
    for d, it in zip(dates, arr or []):
        k = str(d).replace("-", "")
        if it:
            cmap[k] = [it["o"], it["h"], it["l"], it["c"]]
            out.append(it)
        elif k in cmap:
            v = cmap[k]
            out.append({"d": d, "o": v[0], "h": v[1], "l": v[2], "c": v[3]})
            hit += 1
        else:
            out.append(None)
    return out, hit

def market_kline(dates, cmap, secid, tx_code, offline=False):
    """Refresh the recent tail; retained history need not be re-fetched every day."""
    arr = [None] * len(dates)
    if not offline:
        cutoff = (now_bj() - timedelta(days=12)).strftime("%Y%m%d") if cmap else ""
        recent = [d for d in dates if d.replace("-", "") >= cutoff]
        if recent:
            try:
                fetched = fetch_kline(recent, secid)
            except Exception as e:
                print("  东财K线失败，尝试腾讯:", str(e)[:80], flush=True)
                fetched = [None] * len(recent)
            if any(v is None for v in fetched):
                try:
                    backup = fetch_kline_tx(recent, tx_code)
                    fetched = [a or b for a, b in zip(fetched, backup)]
                except Exception as e:
                    print("  腾讯K线失败，保留缓存:", str(e)[:80], flush=True)
            by_day = dict(zip(recent, fetched))
            arr = [by_day.get(d) for d in dates]
    result, hits = merge_kline(dates, arr, cmap)
    print("  K线 %s: %d/%d 日，缓存补齐 %d 日" % (secid, sum(bool(x) for x in result), len(dates), hits), flush=True)
    return result

def save_kline_cache(cache, keep_dates):
    """只保留当前窗口内的 K 线, 防止缓存文件逐年膨胀."""
    if not isinstance(cache, dict):
        return
    keep = set(str(d).replace("-", "") for d in (keep_dates or []))
    if keep:
        for sec in ("kline",):
            if isinstance(cache.get(sec), dict):
                cache[sec] = {k: v for k, v in cache[sec].items() if k in keep}
        for name, sub in list((cache.get("cat") or {}).items()):
            cache["cat"][name] = {k: v for k, v in sub.items() if k in keep}
    try:
        tmp = KLINE_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, KLINE_CACHE)
    except Exception as e:
        print("K线缓存写入失败:", str(e)[:80], flush=True)

def load_shares(dates):
    """沪: fund_etf_scale_sse 逐日; 深: fund_scale_daily_szse 区间批量"""
    sh_codes = [c for c, _, mk in TARGETS if mk == "sh"]
    sz_codes = [c for c, _, mk in TARGETS if mk == "sz"]
    daily_shares = {ds: {} for ds in dates}
    valid = []
    for ds in dates:
        try:
            df = retry(lambda ds=ds: ak.fund_etf_scale_sse(date=ds))
            if df is None or len(df) == 0:
                continue
            for _, row in df.iterrows():
                try:
                    code = str(row.get("基金代码")).zfill(6)
                    value = positive_number(row.get("基金份额"), allow_zero=True)
                    row_day = str(row.get("统计日期"))[:10].replace("-", "")
                    if code in sh_codes and value is not None and row_day == ds:
                        daily_shares[ds][code] = value
                except Exception:
                    pass
            valid.append(ds)
        except Exception as e:
            print(f"  沪 fail {ds}: {str(e)[:70]}", flush=True)
    valid.sort()
    deep_start = None
    if sz_codes:
        sz_days = sorted(dates)
        CH = 45
        for batch in date_batches(sz_days):
            s, e = batch[0], batch[-1]
            try:
                sdf = retry(lambda s=s, e=e: ak.fund_scale_daily_szse(start_date=s, end_date=e, symbol="ETF"))
            except Exception as ex:
                print(f"  深批 fail {s}~{e}: {str(ex)[:50]}", flush=True); continue
            if sdf is None or len(sdf) == 0:
                continue
            for _, row in sdf.iterrows():
                dd = str(row.get("日期"))[:10].replace("-", "")
                try:
                    code = str(row.get("基金代码")).zfill(6); v = positive_number(row.get("基金份额"), allow_zero=True)
                except Exception:
                    continue
                if code in sz_codes and v is not None and s <= dd <= e:
                    daily_shares.setdefault(dd, {})[code] = v
        sz_have = sorted(d for d, m in daily_shares.items() if any(c in m for c in sz_codes))
        if sz_have:
            valid = sorted(set(valid) | set(sz_have))
            deep_start = sz_have[0]
    return daily_shares, valid, deep_start


def date_batches(dates, max_days=120, max_count=45):
    batch = []
    for day in sorted(set(dates)):
        if batch and (len(batch) >= max_count or
                      (datetime.strptime(day, '%Y%m%d') - datetime.strptime(batch[0], '%Y%m%d')).days > max_days):
            yield batch
            batch = []
        batch.append(day)
    if batch:
        yield batch

def parse_nav_text(text):
    """Extract the JSON series without executing the provider's JavaScript."""
    match = re.search(r"\bData_netWorthTrend\s*=\s*", text)
    if not match:
        raise ValueError("净值响应缺少 Data_netWorthTrend")
    rows, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
    if not isinstance(rows, list):
        raise ValueError("净值序列格式变化")
    values = {}
    for row in rows:
        value = positive_number(row.get("y"))
        if value is not None:
            day = datetime.fromtimestamp(float(row["x"]) / 1000, BEIJING).strftime("%Y%m%d")
            values[day] = value
    return values

def load_nav():
    nav = read_json(NAV_CACHE)
    def one(code):
        try:
            def fetch():
                response = requests.get("https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
                                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                response.raise_for_status()
                return parse_nav_text(response.text)
            m = retry(fetch)
            if not m:
                raise ValueError("净值响应为空")
            return code, m
        except Exception as e:
            print(f"  净值 fail {code}: {str(e)[:70]}，保留历史净值缓存", flush=True)
            return code, {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for code, values in pool.map(one, [c for c, _, _ in TARGETS]):
            nav.setdefault(code, {}).update(values)
            print("  净值 %s: %d 日，最新 %s" % (code, len(nav[code]), max(nav[code], default="无")), flush=True)
    write_json(NAV_CACHE, nav)
    return nav

def backfill_new_codes(cache, trade_days=None):
    """新纳入标的的历史份额一次性回补(仅写入新增标的自身份额, 不膨胀缓存).
    深市: fund_scale_daily_szse 45天区间批量, 回补全部缓存区间; 沪市: fund_etf_scale_sse 按日并发, 回补 SH_BACKFILL_FROM 之后.
    完成后写入 cache['hist_done'], 后续运行不再重复回补."""
    hist = cache.setdefault("shares", {})
    done = cache.setdefault("hist_done", {})
    cover = {}
    for m in hist.values():
        for c in m:
            cover[c] = cover.get(c, 0) + 1
    gap = [c for c, _, _ in TARGETS if not done.get(c) and cover.get(c, 0) < COVER_MIN]
    if not gap:
        return 0
    days = [d for d in sorted(hist) if (not trade_days or d in trade_days)]
    if not days:
        return 0
    mkt = {c: mk for c, _, mk in TARGETS}
    sh_gap = [c for c in gap if mkt.get(c) == "sh"]
    sz_gap = [c for c in gap if mkt.get(c) == "sz"]
    filled = 0

    def _take(df, keep):
        n = 0
        if df is None or len(df) == 0:
            return 0
        dcol = "日期" if "日期" in df.columns else "统计日期"
        for _, row in df.iterrows():
            try:
                code = str(row.get("基金代码")).zfill(6)
                v = float(row.get("基金份额"))
            except Exception:
                continue
            dd = str(row.get(dcol))[:10].replace("-", "")
            if code in keep and len(dd) == 8 and dd.isdigit() and (not trade_days or dd in trade_days):
                merge_observation(cache, dd, code, v, "sse" if code in sh_gap else "szse")
                n += 1
        return n

    if sh_gap:
        sh_days = [d for d in days if d >= SH_BACKFILL_FROM]
        keep = set(sh_gap)
        print("沪市新增标的历史回补: %s 自 %s 起共%d日" %
              (",".join(sh_gap), sh_days[0] if sh_days else "-", len(sh_days)), flush=True)

        def _one(ds):
            try:
                return _take(ak.fund_etf_scale_sse(date=ds), keep)
            except Exception:
                return 0

        with ThreadPoolExecutor(max_workers=SH_BACKFILL_WORKERS) as ex:
            futs = [ex.submit(_one, ds) for ds in sh_days]
            for i, fu in enumerate(futs, 1):
                filled += fu.result()
                if i % 50 == 0 or i == len(futs):
                    print("  沪市回补进度 %d/%d" % (i, len(futs)), flush=True)
        for c in sh_gap:
            n = sum(1 for m in hist.values() if c in m)
            print("  沪市回补完成 %s: 累计%d个交易日" % (c, n), flush=True)
            if n >= 5:
                done[c] = "1"

    if sz_gap:
        keep = set(sz_gap)
        CH = 45
        print("深市新增标的历史回补: %s 共%d个交易日区间" % (",".join(sz_gap), len(days)), flush=True)
        for i in range(0, len(days), CH):
            s = days[i]; e = days[min(i + CH - 1, len(days) - 1)]
            try:
                sdf = retry(lambda s=s, e=e: ak.fund_scale_daily_szse(
                    start_date=s, end_date=e, symbol="ETF"), tries=2, gap=3)
            except Exception as ex:
                print("  深市回补 fail %s~%s: %s" % (s, e, str(ex)[:50]), flush=True)
                continue
            filled += _take(sdf, keep)
        for c in sz_gap:
            n = sum(1 for m in hist.values() if c in m)
            print("  深市回补完成 %s: 累计%d个交易日" % (c, n), flush=True)
            if n >= 5:
                done[c] = "1"

    cache["hist_done"] = done
    return filled

