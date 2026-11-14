from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json, time, os, sys, shutil, requests, re
from contextlib import contextmanager

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(OUT_DIR, "国家队ETF每日净买入卖出报表.html")
SHARES_CACHE = os.path.join(OUT_DIR, "_etf_shares_cache.json")
NAV_CACHE = os.path.join(OUT_DIR, "_nav_cache.json")
KLINE_CACHE = os.path.join(OUT_DIR, "_kline_cache.json")
BEIJING = timezone(timedelta(hours=8))

def now_bj():
    return datetime.now(BEIJING)

def read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)

def write_json(path, value):
    # Replace only after serialization and flush succeed; keep the old cache on failure.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    os.replace(tmp, path)

@contextmanager
def network_timeout(seconds=20):
    """Also bound AKShare calls that omit requests' timeout argument."""
    original = requests.sessions.Session.request
    def bounded(session, method, url, **kwargs):
        kwargs.setdefault("timeout", seconds)
        return original(session, method, url, **kwargs)
    requests.sessions.Session.request = bounded
    try:
        yield
    finally:
        requests.sessions.Session.request = original

def last_published_state():
    """读取上一份报表的 (数据日, 更新时间), 用于数据未推进时沿用旧时间戳, 使输出字节不变、工作流跳过提交"""
    cands = [os.path.join(OUT_DIR, "index.html"), REPORT]
    for p in cands:
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                s = f.read()
        except Exception:
            continue
        day = re.search(r"数据日\s*<b>(\d{8})</b>", s)
        upd = re.search(r"更新时间\s*<b>([^<]+)</b>", s)
        if day:
            return {"day": day.group(1), "updated": (upd.group(1) if upd else None)}
    return None

