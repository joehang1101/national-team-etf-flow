from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json, time, os, sys, shutil, requests, re
from contextlib import contextmanager
from runtime import last_published_state, now_bj
from universe import CAT_MAP, TARGETS, members_on
from calculations import latest_common_day

def build_payload(daily, shares, nav, valid, trade_days, exp, deep_start, cat_kline, kline, offline=False):
    # 建立 日期->每条, 日期->合计
    by_date = {}
    items_by = {}
    for it in daily:
        items_by.setdefault(it["d"], {})[it["c"]] = it
    dates_sorted = sorted({it["d"] for it in daily})
    date_total = {}
    cat_total = {}   # 类别 → {日期: 该类别下所有ETF当日净买入合计}
    for ds in dates_sorted:
        values = [x["f"] for x in items_by[ds].values() if x["f"] is not None]
        s = sum(values) if values else None
        date_total[ds] = s
        per_cat = {}
        for c, it in items_by[ds].items():
            if it["f"] is None:
                continue
            cat = CAT_MAP.get(c, "其他")
            per_cat[cat] = per_cat.get(cat, 0) + it["f"]
        cat_total[ds] = per_cat

    cut = latest_common_day(TARGETS, shares, dates_sorted)
    if cut is None:
        raise RuntimeError("没有可展示的交易日")
    dates_sorted = [d for d in dates_sorted if d <= cut]
    today = cut
    week_days = dates_sorted[-5:]
    month_days = dates_sorted[-21:]

    def cum(days):
        dc = {x["c"]: 0.0 for x in daily}
        for ds in days:
            for c, it in items_by.get(ds, {}).items():
                if it["f"] is not None:
                    dc[c] += it["f"]
        return dc
    w_sum = cum(week_days); m_sum = cum(month_days)

    lpub = last_published_state()
    stamp = now_bj().strftime("%Y-%m-%d %H:%M")
    if lpub and lpub.get("day") == today:
        stamp = lpub.get("updated") or stamp
    # 各类指数K线: 逐类尝试东财secid, 失败退腾讯; 仍缺的用历史K线缓存补齐(保证不出现整段空白)
    payload = {
        "updated": stamp,
        "data_asof": today,
        "deep_start": deep_start,
        "dates": [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates_sorted],
        "date_total": {k: round(v / 1e8, 3) if v is not None else None for k, v in date_total.items()},
        "cat_total": {str(k): {cc: round(vv / 1e8, 3) for cc, vv in v.items()} for k, v in cat_total.items()},
        "cat_kline": cat_kline,
        "etfs": [],
    }
    payload["kline"] = kline
    _ok = sum(bool(x) for x in kline)
    dsh_all, s5, n5 = {}, {}, {}
    for code, _, _ in TARGETS:
        # 2025-12-31 份额/净值 基准备锚，用于"每日倍量"：单只按份额、同类/全表按金额
        _dd = {}
        for _ds in dates_sorted:
            _it = items_by.get(_ds, {}).get(code)
            if _it and _it["delta"] is not None:
                _k = f"{_ds[:4]}-{_ds[4:6]}-{_ds[6:]}"
                _dd[_k] = round(_it["delta"] / 1e8, 4)
        dsh_all[code] = _dd
        s5[code] = (shares.get("20251231", {}).get(code) or 0) / 1e8
        _nn = None
        for _ds in sorted(nav.get(code, {})):
            if str(_ds) <= "20251231":
                _nn = nav[code][_ds]
        n5[code] = _nn or 0

    for code, name, mkt in TARGETS:
        if code not in {e['code'] for e in members_on(today)}:
            continue
        payload["etfs"].append({
            "c": code, "n": name, "m": "沪" if mkt == "sh" else "深",
            "cat": CAT_MAP.get(code, "其他"),
            "today": (lambda v: v / 1e8 if v is not None else None)(items_by.get(today, {}).get(code, {}).get("f")),
            "week": round(w_sum.get(code, 0) / 1e8, 3),
            "month": round(m_sum.get(code, 0) / 1e8, 3),
            "last_share": (lambda c=code: (items_by.get(today, {}).get(c, {}).get("s") or 0) / 1e8)(),
            "last_nav": (items_by.get(today, {}).get(code, {}).get("w") or 0),
            "s2025": s5.get(code, 0),
            "n2025": n5.get(code, 0),
            "dsh": dsh_all.get(code, {}),
            "series": {f"{ds[:4]}-{ds[4:6]}-{ds[6:]}": (lambda v: round(v / 1e8, 3) if v is not None else None)(items_by.get(ds, {}).get(code, {}).get("f"))
                       for ds in dates_sorted},
        })
    # 每日总ETF总市值(全市场口径, 亿元): 每只 份额×净值, 缺日用前一交易日前值填充
    per_day = {}
    for it in daily:
        per_day.setdefault(it["c"], {})[it["d"]] = (it["s"], it["w"])
    last_seen = {}
    mv_total = {}
    for ds in dates_sorted:
        tot = 0.0
        active_codes = {e['code'] for e in members_on(ds)}
        for c, _, _ in TARGETS:
            if c not in active_codes:
                continue
            pd = per_day.get(c, {})
            if ds in pd:
                s, w = pd[ds]
                old = last_seen.get(c, (None, None))
                last_seen[c] = (s, w if w is not None else old[1])
            s, w = last_seen.get(c, (None, None))
            if s is not None and w is not None:
                tot += s * w
        mv_total[ds] = round(tot / 1e8, 1)
    payload["mv_total"] = mv_total
    coverage = {ds: sum(it["f"] is not None for it in items_by[ds].values()) for ds in dates_sorted}
    payload["quality"] = {
        "expected_date": str(exp), "is_stale": today < str(exp),
        "offline": offline, "target_count": len(payload['etfs']),
        "latest_complete": coverage.get(today, 0), "daily_coverage": coverage,
        "missing_latest": [c for c, _, _ in TARGETS if items_by.get(today, {}).get(c, {}).get("f") is None],
        "kline_coverage": _ok,
        "calendar_verified": bool(trade_days and max(trade_days) >= str(exp)),
        "issues_latest": {c: items_by.get(today, {}).get(c, {}).get("status", "missing_share")
                          for c, _, _ in TARGETS if items_by.get(today, {}).get(c, {}).get("status") != "ok"},
    }
    if not any(coverage.values()):
        raise RuntimeError("没有可计算的净申赎数据，保留上次报表")
    payload["daily_records"] = [it for it in daily if it["d"] <= cut]
    payload['feature_trade_days'] = sorted(set(dates_sorted) | {d for d in trade_days or [] if dates_sorted[0] <= d <= cut})
    return payload

