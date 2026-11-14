from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json, time, os, sys, shutil, requests, re
from contextlib import contextmanager
from runtime import OUT_DIR, REPORT, now_bj
from universe import BASE23, BASE_TAG, CAT_INDEX, NEW_TAG, NEW_TAG_CODES

def escape_html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def render_html(p):
    def fnum(v, sign=False):
        if v is None:
            return "—"
        s = f"{v:+,.2f}" if sign else f"{v:,.2f}"
        return s
    def cls(v):
        return "" if v is None else ("pos" if v >= 0 else "neg")
    def ftag(code):
        if code in NEW_TAG_CODES:
            return f'<span class="tag">{NEW_TAG}</span>'
        if code in BASE23:
            return f'<span class="tag tagb">{BASE_TAG}</span>'
        return ''
    dates = p["dates"]
    dt = {}
    for k, v in p["date_total"].items():
        kk = str(k)
        if len(kk) == 8:
            kk = f"{kk[:4]}-{kk[4:6]}-{kk[6:]}"
        dt[kk] = v
    # 近21交易日用于柱状图
    chart = dates[-21:]
    chart_total = [dt.get(d) for d in chart]
    maxabs = max([abs(x) for x in chart_total if x is not None] + [1])
    # 近一年滚动累计 (曲线), 起点对齐到深市有数据的日期
    deep_start = p.get("deep_start")
    if deep_start and len(str(deep_start)) == 8:
        ds = str(deep_start)
        deep_start = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"
    bidx_sz = dates.index(deep_start) if deep_start in dates else 0
    bidx = bidx_sz  # 用尽深市可用历史, 曲线取最长
    y_dates = dates[bidx:]
    _mv_src = p.get("mv_total", {})
    _mvm = {}
    for _k, _v in _mv_src.items():
        _kk = str(_k)
        if len(_kk) == 8:
            _kk = f"{_kk[:4]}-{_kk[4:6]}-{_kk[6:]}"
        _mvm[_kk] = _v
    y_mv = [_mvm.get(d, 0) for d in y_dates]
    # 近一周 / 近一月 / 近半年 / 近一年 / 今年以来
    week_days = dates[-5:]
    month_days = dates[-21:]
    halfyear_days = dates[-120:]
    oneyear_days = dates[-242:]
    cur_year = str(now_bj().year)
    ytd_days = [d for d in dates if str(d)[:4] == cur_year]
    def covered_sum(days):
        values = [dt[d] for d in days if dt.get(d) is not None]
        return sum(values) if values else None
    week_tot = covered_sum(week_days)
    month_tot = covered_sum(month_days)
    halfyear_tot = covered_sum(halfyear_days)
    oneyear_tot = covered_sum(oneyear_days)
    ytd_tot = covered_sum(ytd_days)
    today_tot = dt.get(dates[-1])
    def flow_label(value):
        return "数据缺失" if value is None else ("净申购" if value >= 0 else "净赎回")

    # --- 各类指数K线联动数据 (下拉切换) ---
    _dates_fmt = dates
    _cat_dt = {}   # 转键: YYYYMMDD -> YYYY-MM-DD, 供每类每日净买入取值
    for _ck, _cv in (p.get("cat_total") or {}).items():
        _ckk = str(_ck)
        if len(_ckk) == 8:
            _ckk = f"{_ckk[:4]}-{_ckk[4:6]}-{_ckk[6:]}"
        _cat_dt[_ckk] = _cv
    _tot_dt = {}   # 中证全指: 全市场每日净买入合计(键转YYYY-MM-DD)
    for _tk, _tv in (p.get("date_total") or {}).items():
        _tkk = str(_tk)
        if len(_tkk) == 8:
            _tkk = f"{_tkk[:4]}-{_tkk[4:6]}-{_tkk[6:]}"
        _tot_dt[_tkk] = _tv
    # 每类: 该类当日ETF净买入合计(k键=YYYY-MM-DD, 含缺失天填0); 中证全指在首项
    def _cat_series(cat):
        return [(_cat_dt.get(d, {}) or {}).get(cat) for d in _dates_fmt]
    i_kl_names = ["中证全指"] + list(CAT_INDEX.keys())
    _zzkl = p.get("kline") or [None] * len(dates)
    i_kl_series = [_zzkl] + [p.get("cat_kline", {}).get(cat) or [None] * len(dates) for cat in i_kl_names[1:]]
    _tot_series = [_tot_dt.get(d) for d in _dates_fmt]
    i_qd_series = [_tot_series] + [_cat_series(cat) for cat in i_kl_names[1:]]
    INAMES = json.dumps(i_kl_names, ensure_ascii=False)
    ISERIES = json.dumps(i_kl_series, ensure_ascii=False)
    ISERIES_Q = json.dumps(i_qd_series, ensure_ascii=False)
    D_ARR = json.dumps(dates, ensure_ascii=False)
    KLN_JS = ("<script>\n"
      "var IDX_NAMES=" + INAMES + ";\n"
      "var IDX_K=" + ISERIES + ";\n"
      "var IDX_Q=" + ISERIES_Q + ";\n"
      "var DATA_D=" + D_ARR + ";\n"
      "(function(){\n"
      "function go(){\n"
      " var box=document.getElementById('klnBox');\n"
      " var sel=document.getElementById('klnCat');\n"
      " if(!window.echarts){var t=document.getElementById('klnTip');if(t)t.textContent='图表库未加载(需联网)';return;}\n"
      " var chart=echarts.init(box),cur=120,ci=0;\n"
      " function opt(n,ci2){\n"
      "  var K=IDX_K[ci2],Q=IDX_Q[ci2];\n"
      "  var s=(n>0)?Math.max(0,DATA_D.length-n):0,tail=(s>0)?{startValue:s,endValue:DATA_D.length-1} : null;\n"
      "  var ds=DATA_D.slice(s),ks=K.slice(s),qs=Q.slice(s);\n"
      "  return {axisPointer:{link:[{xAxisIndex:[0,1]}],lineStyle:{color:'#98a2b8'}},\n"
      "   grid:[{left:50,right:56,top:16,height:300,gridIndex:0},{left:50,right:56,bottom:28,height:120,gridIndex:1}],\n"
      "   xAxis:[{type:'category',data:ds,gridIndex:0,boundaryGap:true,axisLine:{lineStyle:{color:'#c9cfdd'}},axisLabel:{color:'#7a8194',fontSize:10},axisTick:{show:false}},\n"
      "          {type:'category',data:ds,gridIndex:1,axisLine:{lineStyle:{color:'#c9cfdd'}},axisLabel:{show:false},axisTick:{show:false}}],\n"
      "   yAxis:[{scale:true,gridIndex:0,position:'right',splitLine:{lineStyle:{color:'#eef1f6'}},axisLabel:{color:'#7a8194',fontSize:10}},\n"
      "          {scale:true,gridIndex:1,position:'right',splitLine:{show:false},axisLabel:{color:'#7a8194',fontSize:10,formatter:function(v){return v.toFixed(0);}}}],\n"
      "   dataZoom:[{type:'inside',xAxisIndex:[0,1],zoomOnMouseWheel:true,moveOnMouseMove:true,moveOnMouseWheel:true,throttle:50},\n"
      "             {type:'slider',xAxisIndex:[0,1],height:18,bottom:0,startValue:0,endValue:ds.length-1,brushSelect:false,textStyle:{fontSize:9,color:'#7a8194'},borderColor:'#dfe4ef',fillerColor:'rgba(99,126,188,0.12)',handleStyle:{color:'#b7c2da'},dataBackground:{lineStyle:{color:'#c9d2e4'},areaStyle:{color:'rgba(99,126,188,0.06)'}}}],\n"
      "   series:[\n"
      "    {name:'指数K线',type:'candlestick',data:ks.map(function(x){return x?[x.o,x.c,x.l,x.h]:[null,null,null,null];}),\n"
      "     itemStyle:{color:'#d43d2a',color0:'#1f9d7b',borderColor:'#d43d2a',borderColor0:'#1f9d7b'}},\n"
      "    {name:'该类ETF净买卖',type:'bar',xAxisIndex:1,yAxisIndex:1,data:qs,\n"
      "     itemStyle:{color:function(p){return p.value>=0?'#d43d2a':'#1f9d7b';}}}\n"
      "   ],\n"
      "   tooltip:{trigger:'axis',axisPointer:{type:'cross'},position:[8,338],confine:true,backgroundColor:'#2a2f3a',borderWidth:0,textStyle:{color:'#fff',fontSize:12},extraCssText:'white-space:nowrap;max-width:880px;padding:6px 10px;box-shadow:0 2px 8px rgba(0,0,0,.15);',\n"
      "    formatter:function(ps){var i=ps[0].dataIndex,g=i+s,d=DATA_D[g],k=K[g]||{},q=Q[g];\n"
      "     var s1='';\n"
      "     if(k.o!=null){s1=' &nbsp;开'+k.o+' 高'+k.h+' 低'+k.l+' 收'+k.c;}\n"
      "     var col=q>=0?'#f0705c':'#46c7a6';\n"
      "     var lab=(ci2===0)?'当日净买卖':'该类ETF净买卖';\n"
      "     return '<b>'+d+'</b>'+s1+' &nbsp;'+lab+' <b style=\"color:'+col+'\">'+(q==null?'—':(q>=0?'+':'')+q.toFixed(2)+' 亿')+'</b>';}}\n"
      "  };\n"
      " }\n"
      " chart.setOption(opt(cur,ci));\n"
      " function upd(){chart.setOption(opt(cur,ci),true);}\n"
      " sel.addEventListener('change',function(){ci=sel.selectedIndex;chart.setOption(opt(cur,ci));});\n"
      " var btns=document.querySelectorAll('.rng');\n"
      " Array.prototype.forEach.call(btns,function(b){b.addEventListener('click',function(){\n"
      "   var _n2=parseInt(b.getAttribute('data-n'),10);cur=(_n2===0)?0:(isNaN(_n2)?120:_n2);\n"
      "   Array.prototype.forEach.call(btns,function(x){x.classList.toggle('sel',x===b);});\n"
      "   chart.setOption(opt(cur,ci));\n"
      " });});\n"
      "window.addEventListener('resize',function(){chart.resize();});\n"
      "}\n"
      "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',go);}else{go();}\n"
      "})();\n"
      "</script>")

    _cat_opts = "".join('<option%s>%s</option>' % (' selected' if i == 0 else '', _n) for i, _n in enumerate(i_kl_names))

    # 内联 ECharts，避免境外 CDN 在本机加载失败导致图表空白
    _EC_PATH = os.path.join(OUT_DIR, "assets", "echarts.min.js")
    if os.path.exists(_EC_PATH) and os.path.getsize(_EC_PATH) > 100000:
        with open(_EC_PATH, encoding="utf-8") as f:
            _ec = f.read().replace("</script>", "<\\/script>")
        ECHARTS_HTML = "<script>" + _ec + "</script>"
    else:
        raise RuntimeError("缺少 assets/echarts.min.js，无法生成可离线使用的图表")

    etfs = sorted(p["etfs"], key=lambda e: e["month"])

    # 同类(指数)品种归集
    agg = {}
    for e in etfs:
        agg.setdefault(e.get("cat", "其他"), []).append(e)
    cat_rows_html = ""
    cat_share_tot = cat_today_tot = cat_week_tot = cat_month_tot = cat_mv_tot = 0.0
    cat_s25_tot = cat_mv25_tot = 0.0
    for cat in sorted(agg, key=lambda c: sum(x["month"] for x in agg[c]), reverse=True):
        es = agg[cat]
        td = sum(x["today"] for x in es if x["today"] is not None); wk = sum(x["week"] for x in es)
        mo = sum(x["month"] for x in es); sh = sum(x["last_share"] for x in es)
        mv = sum(x["last_share"] * x["last_nav"] for x in es)
        s25 = sum(x.get("s2025", 0) for x in es)
        mv25 = sum(x.get("s2025", 0) * x.get("n2025", 0) for x in es)
        cat_today_tot += td; cat_week_tot += wk; cat_month_tot += mo; cat_share_tot += sh; cat_mv_tot += mv
        cat_s25_tot += s25; cat_mv25_tot += mv25
        w = min(abs(mo) / 200 * 100, 100)
        cat_rows_html += f"""<tr>
          <td><b>{escape_html(cat)}</b><span class="code">{len(es)}只</span></td>
          <td class="r {cls(td)}">{fnum(td, True)}</td>
          <td class="r {cls(wk)}">{fnum(wk, True)}</td>
          <td class="r {cls(mo)}">{fnum(mo, True)}</td>
          <td class="r">{s25:,.1f}</td>
          <td class="r">{mv25:,.1f}</td>
          <td class="r">{sh:,.1f}</td>
          <td class="r">{mv:,.1f}</td>
          <td class="tbar"><div class="tbf"><i class="{cls(mo)}" style="width:{w:.0f}%"></i></div></td></tr>"""
    cat_tot_row = f"""<tr class="ctot"><td><b>全部（{len(agg)}类{len(etfs)}只）</b></td>
      <td class="r {cls(cat_today_tot)}">{fnum(cat_today_tot, True)}</td>
      <td class="r {cls(cat_week_tot)}">{fnum(cat_week_tot, True)}</td>
      <td class="r {cls(cat_month_tot)}">{fnum(cat_month_tot, True)}</td>
      <td class="r">{cat_s25_tot:,.1f}</td>
      <td class="r">{cat_mv25_tot:,.1f}</td>
      <td class="r">{cat_share_tot:,.1f}</td>
      <td class="r">{cat_mv_tot:,.1f}</td><td></td></tr>"""

    # 每日明细矩阵 (最近 HOT 个交易日)
    HOT = 10
    hot_dates = dates[-HOT:]
    hot_html = ""
    mcol_headers = "".join(f"<th>{d[5:]}</th>" for d in hot_dates)
    # 单元格: 上行=净买卖金额(亿元,单位'亿'), 下行=倍量(单位'倍'); v=净买卖(亿), m=倍量
    def _dm_cell(v, m=None):
        if v is None:
            return '<td class="r" style="color:var(--mut)">—</td>'
        if abs(v) < 0.001:
            top, c = "0", ""
        else:
            top, c = fnum(v, True) + "亿", cls(v)
        if m is None:
            return f'<td class="r {c}">{top}</td>'
        return f'<td class="r {c}">{top}<div class="mult {cls(m)}">{m:+.2f}倍</div></td>'
    # 同一指数的 ETF 放在一起展示: 先按类别近{HOT}日净买入合计排序, 类内再按单只合计排序, 类前插入分组行
    _hot_sum = lambda e: sum(e["series"].get(d) or 0 for d in hot_dates)
    _groups = {}
    for e in etfs:
        _groups.setdefault(e.get("cat", "其他"), []).append(e)
    for _cat in sorted(_groups, key=lambda c: sum(_hot_sum(e) for e in _groups[c]), reverse=True):
        hot_html += ('<tr class="ghead"><td colspan="%d">%s · %d只</td></tr>'
                     % (len(hot_dates) + 1, escape_html(_cat), len(_groups[_cat])))
        for e in sorted(_groups[_cat], key=_hot_sum, reverse=True):
            ser = e["series"]
            bz = e.get("s2025", 0) * 0.001 if e.get("s2025", 0) > 0 else None
            dsh = e.get("dsh", {})
            cells = ""
            for d in hot_dates:
                v = ser.get(d)
                m = (dsh.get(d) / bz) if (bz and dsh.get(d) is not None) else None
                cells += _dm_cell(v, m)
            hot_html += f"<tr><td><span class='mkt'>{e['m']}</span>{escape_html(e['n'])}{ftag(e['c'])}</td>{cells}</tr>"

    # 同类指数归集 · 每日净买卖明细矩阵 (最近 HOT 个交易日)
    cat_daily = {}
    for e in etfs:
        cat = e.get("cat", "其他")
        cd = cat_daily.setdefault(cat, {})
        for d in hot_dates:
            v = e["series"].get(d)
            if v is not None:
                cd[d] = cd.get(d, 0) + v
    cat_order = sorted(cat_daily, key=lambda c: sum(cat_daily[c].get(d, 0) for d in hot_dates), reverse=True)
    cat_mv = {}
    for e in etfs:
        g = e.get("cat", "其他")
        cat_mv[g] = cat_mv.get(g, 0) + e.get("s2025", 0) * e.get("n2025", 0)
    cat_daily_html = ""
    for cat in cat_order:
        cbz = cat_mv.get(cat, 0) * 0.001 if cat_mv.get(cat, 0) > 0 else None
        cells = ""
        for d in hot_dates:
            v = cat_daily[cat].get(d)
            m = (v / cbz) if (cbz and v is not None) else None
            cells += _dm_cell(v, m)
        cat_daily_html += f'<tr><td><b>{escape_html(cat)}</b><span class="code">{len(agg.get(cat, []))}只</span></td>{cells}</tr>'
    _all_mv = sum(e.get("s2025", 0) * e.get("n2025", 0) for e in etfs)
    _all_bz = _all_mv * 0.001 if _all_mv > 0 else None
    tot_cells = ""
    for d in hot_dates:
        v = dt.get(d, 0)
        m = (v / _all_bz) if _all_bz and v is not None else None
        tot_cells += _dm_cell(v, m)
    cat_daily_tot = f'<tr class="ctot"><td><b>全部（{len(cat_daily)}类{len(etfs)}只）</b></td>{tot_cells}</tr>'

    # 表格行: 同一指数的 ETF 放在一起, 类别按近一月合计降序, 类内按近一月降序, 类前插入分组行
    rows = ""
    _sum_groups = {}
    for e in etfs:
        _sum_groups.setdefault(e.get("cat", "其他"), []).append(e)
    _sum_rows = ""
    for _cat in sorted(_sum_groups, key=lambda c: sum(x["month"] for x in _sum_groups[c]), reverse=True):
        _sum_rows += ('<tr class="ghead"><td colspan="6">%s · %d只</td></tr>'
                      % (escape_html(_cat), len(_sum_groups[_cat])))
        for e in sorted(_sum_groups[_cat], key=lambda x: x["month"], reverse=True):
            _sum_rows += f"""<tr>
          <td><span class="mkt">{e['m']}</span>{escape_html(e['n'])}{ftag(e['c'])}<span class="code">{e['c']}</span></td>
          <td class="r">{e['last_share']:,.1f}</td>
          <td class="r {cls(e['today'])}">{fnum(e['today'], True)}</td>
          <td class="r {cls(e['week'])}">{fnum(e['week'], True)}</td>
          <td class="r {cls(e['month'])}">{fnum(e['month'], True)}</td>
          <td class="tbar"><div class="tbf"><i class="{cls(e['month'])}" style="width:{min(abs(e['month'])/50*100,100):.0f}%"></i></div></td>
        </tr>"""
    rows = _sum_rows

    # 柱状图
    bars = ""
    for i, d in enumerate(chart):
        v = chart_total[i]
        if v is None:
            bars += f'<div class="col" title="{d} 数据缺失"><div class="bwrap"></div><div class="dt">{d[5:]}</div></div>'
            continue
        h = abs(v) / maxabs * 100
        bars += f"""<div class="col" title="{d} {fnum(v,True)}亿">
          <div class="bwrap"><div class="bar {cls(v)}" style="height:{max(h,1.5):.1f}%"></div></div>
          <div class="dt">{d[5:]}</div></div>"""

    # 观察池资产规模折线(SVG)
    W, H = 860, 120; pad = 12
    mn, mx = min(y_mv), max(y_mv)
    if mn == mx: mn -= 1; mx += 1
    npts = len(y_mv)
    pts = []
    for i, v in enumerate(y_mv):
        x = pad + i * (W - 2 * pad) / max(1, npts - 1)
        y = H - pad - (v - mn) / (mx - mn) * (H - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    last_pts = pts[-1]; last_v = y_mv[-1]
    ypt_json = json.dumps([{"d": y_dates[i], "v": round(y_mv[i], 1)} for i in range(len(y_dates))], ensure_ascii=False)
    JS_CODE = ("<script>\n"
      "const YPT=" + ypt_json + ";\n"
      "const C0=" + str(pad) + ",C1=" + str(W - pad) + ",CH=" + str(H) +
      ",CMN=" + f"{mn:.0f}" + ",CMX=" + f"{mx:.0f}" + ",WW=" + str(W) + ";\n"
      "const CVD=document.getElementById('cumSvg'),TIP=document.getElementById('cumTip'),G=document.getElementById('cumGuide');\n"
      "const N=YPT.length;\n"
      "function xAt(i){return C0+i*(C1-C0)/(N-1);}\n"
      "function FMT(v){return v.toFixed(1)+'亿';}\n"
      "CVD.addEventListener('mousemove',function(e){"
      "var r=CVD.getBoundingClientRect();"
      "var sx=(e.clientX-r.left)/r.width*WW;"
      "var i=Math.round((sx-C0)/(C1-C0)*(N-1));"
      "if(i<0)i=0;if(i>N-1)i=N-1;"
      "var p=YPT[i],gx=xAt(i);"
      "G.setAttribute('x1',gx);G.setAttribute('x2',gx);"
      "TIP.style.color='var(--accent)';"
      "TIP.textContent='日期 '+p.d+'  资产规模 '+FMT(p.v);"
      "});"
      "CVD.addEventListener('mouseleave',function(){G.setAttribute('x1',-10);G.setAttribute('x2',-10);});"
      "\n</script>")

    def fmt_dt(d):
        return d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:8]}"

    quality = p.get("quality", {})
    complete = quality.get("latest_complete", len(etfs))
    status_text = "数据滞后" if quality.get("is_stale") else "达到本次运行期望数据日"
    if quality.get("offline"):
        status_text = "离线缓存重建 · " + status_text
    missing = "、".join(quality.get("missing_latest", [])) or "无"
    quality_html = f'<div class="quality"><b>{escape_html(status_text)}</b> · 当日可计算 {complete}/{len(etfs)} 只 · 本次运行期望 {escape_html(quality.get("expected_date", p["data_asof"]))}<br>缺失 / 待核验：{escape_html(missing)}。汇总为已覆盖样本合计，历史覆盖随数据可得性变化；“—”不计入合计，不能当作净申赎为零。K线覆盖 {quality.get("kline_coverage", 0)}/{len(dates)} 日。<br>交易日历：{"已核验覆盖本次期望日" if quality.get("calendar_verified") else "覆盖不足，暂按工作日回退，节假日可能误判"}。刷新页面只重载现有报表，采集须运行脚本或自动任务。</div>'
    quality_html += f'<div class="quality">相邻两日份额均有官方来源记录且可计算：{quality.get("official_latest", 0)}/{len(etfs)}。旧缓存无来源记录的值保留为“来源待核验”，不等于已验证官方值；待修复历史日期 {p.get("lineage", {}).get("pending_dates", 0)} 个。<br>研究导出使用当前修订版本，尚非可直接进行时点回测的数据集。<a href="data/manifest.json">数据清单</a> · <a href="data/daily_etf.csv">ETF日度CSV</a> · <a href="data/daily_categories.csv">类别CSV</a> · <a href="data/daily_indices.csv">指数CSV</a></div>'
    signals = [r for r in p.get('attribution', []) if r['date'] == p['data_asof']]
    signal_rows = ''.join('<tr><td>'+escape_html(r['code'])+'</td><td>'+escape_html(r['label'])+'</td><td>'+escape_html(r['evidence_grade'])+'</td><td>'+escape_html(r['reason'])+'</td></tr>' for r in signals)
    anchors = p.get('anchors', [])
    anchor_rows = ''.join('<tr><td>'+escape_html(a['code'])+'</td><td>'+escape_html(a['holder'])+'</td><td>'+escape_html(a['report_date'])+'</td><td>'+escape_html(a['published_at'])+'</td><td class="r">'+f"{a['shares']/1e8:,.2f}"+'</td><td><a href="'+escape_html(a['source_url'])+'">披露原文</a></td></tr>' for a in anchors if a.get('verified'))
    research_html = '<div class="card"><h2>疑似国家队行为 · 研究模式</h2><p>“净申购/净赎回”是观测量；以下归因是研究假设。置信度表示证据等级，并非识别交易主体的概率。A500仅作候选对照，未经审查不进入归因模型。</p><div style="overflow-x:auto"><table><thead><tr><th>代码</th><th>判断</th><th>证据等级</th><th>原因</th></tr></thead><tbody>'+signal_rows+'</tbody></table></div><details><summary>持有人披露锚点（历史报告期与实际公告日分开记录）</summary><div style="overflow-x:auto"><table><thead><tr><th>代码</th><th>持有人</th><th>报告期</th><th>公告日</th><th>持有亿份</th><th>证据</th></tr></thead><tbody>'+anchor_rows+'</tbody></table></div><p>锚点只约束披露时点，不能唯一恢复中间每日买卖；前十大未出现不代表持仓为零。<a href="data/anchor_intervals.csv">披露区间净变化CSV</a> · <a href="data/holding_bounds.csv">条件持仓容量区间CSV</a> · <a href="data/attribution_daily.csv">归因研究CSV</a></p></details></div>'

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>国家队ETF每日净买入卖出报表</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1a1d24;--mut:#7a8194;--line:#e6e8ee;
  --pos:#d43d2a;--posbg:#fdecea;--neg:#1f9d7b;--negbg:#e6f6f0;--accent:#2f54eb;}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  background:var(--bg);color:var(--ink);line-height:1.55;}}
.wrap{{max-width:980px;margin:0 auto;padding:20px 16px 60px;}}
.top{{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:10px;margin-bottom:18px;}}
h1{{font-size:22px;margin:0;font-weight:700}}
.sub{{color:var(--mut);font-size:12px;margin-top:6px;display:flex;gap:14px;flex-wrap:wrap}}
.sub b{{font-weight:600;color:var(--accent)}}
.kpis{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-auto-rows:1fr;gap:12px;margin-bottom:22px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;justify-content:center}}
.kpi .lb{{font-size:12px;color:var(--mut)}}
.kpi .vl{{font-size:22px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}}
.kpi .tg{{font-size:12px;margin-top:2px}}
.pos{{color:var(--pos)}} .neg{{color:var(--neg)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:22px}}
.card h2{{font-size:15px;margin:0 0 14px;font-weight:600}}
.bars{{display:flex;align-items:flex-end;gap:4px;height:190px;}}
.col{{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;height:100%;}}
.bwrap{{flex:1;display:flex;align-items:flex-end;width:100%}}
.bar{{width:100%;border-radius:3px;min-height:2px}}
.bar.pos{{background:linear-gradient(180deg,#e8836f,var(--pos))}}
.bar.neg{{background:linear-gradient(180deg,#54c2a6,var(--neg))}}
.dt{{font-size:10px;color:var(--mut);transform:rotate(-40deg);transform-origin:top right}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{font-size:12px;color:var(--mut);font-weight:500;text-align:right;padding:6px 8px;border-bottom:2px solid var(--line);white-space:nowrap}}
th:first-child{{text-align:left}}
td{{padding:7px 8px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}}
td.r{{text-align:right;white-space:nowrap}}
tr.ctot td{{border-top:2px solid var(--ink);font-weight:600;background:#fafbfc}}
tr.ghead td{{background:#eef2fa;color:#2b3a67;font-weight:700;font-size:11px;padding:5px 8px;border-top:1px solid var(--line)}}
td.zero{{color:var(--mut);font-size:11px;opacity:.65}}
.mult{{display:block;font-size:10px;opacity:.72;margin-top:1px;font-family:var(--font-mono,"SFMono-Regular",Consolas,monospace)}}
table.matrix td{{font:11px var(--font-mono,"SFMono-Regular",Consolas,monospace)}}
table.matrix th{{font-size:10px}}
.mkt{{display:inline-block;font-size:10px;color:var(--mut);border:1px solid var(--line);border-radius:4px;padding:0 4px;margin-right:6px}}
.code{{color:var(--mut);font-size:11px;margin-left:6px}}
.tag{{display:inline-block;font-size:10px;line-height:15px;color:#b45309;background:#fff7ed;
  border:1px solid #fed7aa;border-radius:4px;padding:0 5px;margin-left:6px;vertical-align:1px;font-weight:500;white-space:nowrap}}
.tagb{{color:#1d4ed8;background:#eff6ff;border-color:#bfdbfe}}
.tbar{{width:90px}}.tbf{{background:#f0f1f4;border-radius:3px;height:10px;overflow:hidden}}
.tbf i{{display:block;height:100%;border-radius:3px}}
.tbf i.pos{{background:var(--pos)}}.tbf i.neg{{background:var(--neg)}}

.note{{color:var(--mut);font-size:12px;margin-top:18px;line-height:1.7}}
.quality{{background:#fff8e6;border:1px solid #f0d994;border-radius:10px;padding:12px 14px;margin:0 0 18px;font-size:12px;color:#755715;line-height:1.8}}
@media(max-width:600px){{.kpis{{grid-template-columns:1fr}}.wrap{{padding:14px 10px 36px}}.card{{padding:12px}}h1{{font-size:19px}}}}
.svgwrap{{margin-top:16px}}
.chartbox{{position:relative}}
.cumtip{{display:block;text-align:center;margin:2px auto 4px;min-height:18px;
  border:none;box-shadow:none;background:transparent;color:var(--mut);
  font:12px "SFMono-Regular",Consolas,monospace;pointer-events:none;white-space:nowrap}}
svg text{{fill:var(--mut);font-size:10px}}
.foot{{color:var(--mut);font-size:11px;text-align:center;margin-top:30px}}
.klnctl{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px}}
.klnctl .kp{{font-size:12px;color:var(--mut)}}
.rng{{border:1px solid var(--line);background:#fff;border-radius:8px;padding:5px 12px;font-size:12px;cursor:pointer;color:var(--ink)}}
.rng:hover{{border-color:var(--accent)}}
.rng.sel{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.kpt{{font-size:12px;color:var(--mut);margin-left:auto;font-family:SFMono-Regular,Consolas,monospace}}
.ktip{{font-size:12px;color:var(--mut);text-align:center;margin-top:8px;min-height:16px;font-family:SFMono-Regular,Consolas,monospace}}
</style>
  {ECHARTS_HTML}
  </head><body><div class="wrap">
  <div class="top">
    <div>
      <h1>国家队ETF · 每日净买入 / 净卖出</h1>
      <div class="sub"><span>数据日 <b>{escape_html(p['data_asof'])}</b></span>
        <span>更新时间 <b>{escape_html(p['updated'])}</b></span>
        <span>标的口径：汇金系相关宽基ETF篮子 共 <b>{len(etfs)}</b> 只 · 全市场净申赎</span>
        <button onclick="location.reload()" title="重新加载已生成的报表；采集更新请运行本地刷新脚本或 GitHub Actions"
           style="cursor:pointer;margin-left:6px;padding:4px 12px;border:1px solid var(--line,#ccc);border-radius:6px;background:var(--card,#fff);color:var(--txt,#111);font-size:12px">重新加载报表</button></div>
      <div style="color:var(--mut);font-size:12px;margin-top:6px">本表为汇金系相关宽基ETF篮子（{len(etfs)}只，含23只原始观察池与扩展观察池品种）的全市场每日净申赎估算，篮子 ≠ 汇金实际持仓，无法据此准确检测汇金买卖。</div>
    </div>
  </div>
  {quality_html}
  <div class="kpis">
    <div class="kpi"><div class="lb">最新交易日 {fmt_dt(dates[-1])} 合计</div>
      <div class="vl {cls(today_tot)}">{fnum(today_tot, True)}</div>
      <div class="tg {cls(today_tot)}">{flow_label(today_tot)} 亿元</div></div>
    <div class="kpi"><div class="lb">近一周累计 (5个交易日)</div>
      <div class="vl {cls(week_tot)}">{fnum(week_tot, True)}</div>
      <div class="tg {cls(week_tot)}">{flow_label(week_tot)} 亿元</div></div>
    <div class="kpi"><div class="lb">近一月累计 (21个交易日)</div>
      <div class="vl {cls(month_tot)}">{fnum(month_tot, True)}</div>
      <div class="tg {cls(month_tot)}">{flow_label(month_tot)} 亿元</div></div>
    <div class="kpi"><div class="lb">近半年累计 (约120个交易日)</div>
      <div class="vl {cls(halfyear_tot)}">{fnum(halfyear_tot, True)}</div>
      <div class="tg {cls(halfyear_tot)}">{flow_label(halfyear_tot)} 亿元</div></div>
    <div class="kpi"><div class="lb">近一年累计 (约242个交易日)</div>
      <div class="vl {cls(oneyear_tot)}">{fnum(oneyear_tot, True)}</div>
      <div class="tg {cls(oneyear_tot)}">{flow_label(oneyear_tot)} 亿元</div></div>
    <div class="kpi"><div class="lb">今年以来累计 ({cur_year}年{len(ytd_days)}个交易日)</div>
      <div class="vl {cls(ytd_tot)}">{fnum(ytd_tot, True)}</div>
      <div class="tg {cls(ytd_tot)}">{flow_label(ytd_tot)} 亿元</div></div>
  </div>
  <div class="card"><h2>全部品种 · 每日净买卖合计（近21个交易日，亿元）</h2>
    <div class="bars">{bars}</div>
    <div class="svgwrap"><div class="chartbox">
      <h2 style="font-size:14px">观察池资产规模 · 最长 {len(y_dates)}个交易日（亿元）</h2>
      <div id="cumTip" class="cumtip"></div>
      <svg id="cumSvg" viewBox="0 0 {W} {H}" width="100%" height="{H}" preserveAspectRatio="none">
        <line id="cumGuide" x1="-10" y1="{pad}" x2="-10" y2="{H-pad}" stroke="#c0c6d4" stroke-width="1" stroke-dasharray="3 3"/>
        <polyline points="{poly}" fill="none" stroke="#2f54eb" stroke-width="1.5"/>
        <circle cx="{last_pts.split(',')[0]}" cy="{last_pts.split(',')[1]}" r="3.5" fill="#2f54eb"/>
        <text x="{pad}" y="{pad-4}">{mn:.0f}</text>
        <text x="{W-pad}" y="{pad-4}" text-anchor="end">{mx:.0f}亿</text>
        <text x="{W-pad}" y="{H-6}" text-anchor="end">{last_v:,.0f}亿</text>
      </svg>
    </div></div>
    {JS_CODE}
  </div>
  <div class="card"><h2>指数K线 × 每日净买入联动（上K线 下当日净买卖）</h2>
    <div class="klnctl"><span class="kp">指数</span>
      <select id="klnCat" style="font-size:12px;padding:2px 6px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg)">{_cat_opts}</select>
      <span class="kp" style="margin-left:10px">显示范围</span>
      <button class="rng" data-n="60">60日</button>
      <button class="rng sel" data-n="120">120日</button>
      <button class="rng" data-n="250">250日</button>
      <button class="rng" data-n="0">全部</button>
      <span class="kpt" id="klnDate"></span></div>
    <div id="klnBox" style="height:520px;width:100%"></div>
    <div id="klnTip" class="ktip">默认显示中证全指K线与观察池ETF每日净申赎联动；下拉可切换至{len(CAT_INDEX)}类细分指数（沪深300、创业板等）K线 × 该类ETF净买入合计。上方为指数日K线，下方为当日净买入金额（亿元）；红=净买入、绿=净卖出。</div>
  </div>
  <div class="card"><h2>分品种汇总 · 近一周 / 近一月净买入卖出（亿元，按近一月降序）</h2>
    <div style="overflow-x:auto"><table>
      <thead><tr><th>品种</th><th>最新份额(亿)</th><th>今日</th><th>近一周</th><th>近一月</th><th style="width:90px">近一月强度</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
    <div style="color:var(--mut);font-size:11px;margin-top:8px">标注「{BASE_TAG}」的 {len(BASE23)} 只为仓库原始观察池，持有人归属尚未逐项附公告核验；标注「{NEW_TAG}」的 {len(NEW_TAG_CODES)} 只为 仓库扩展观察池，此标签不证明特定机构买入。</div>
  </div>
  <div class="card"><h2>同类指数品种归集汇总（亿元）</h2>
    <div style="overflow-x:auto"><table>
      <thead><tr><th>指数类别</th><th>今日</th><th>近一周</th><th>近一月</th><th>2025-12-31份额(亿)</th><th>2025-12-31市值(亿)</th><th>最新份额(亿)</th><th>ETF总市值(亿)</th><th style="width:90px">近一月强度</th></tr></thead>
      <tbody>{cat_rows_html}{cat_tot_row}</tbody>
    </table></div>
    <div style="color:var(--mut);font-size:11px;margin-top:8px">将跟踪同一指数的多只 ETF 归集加总，观察该指数方向上的整体净买卖。</div>
  </div>
  <div class="card"><h2>每日净买卖明细 · 金额 / 倍量份额（最近{HOT}个交易日，亿元 · 可横向滚动）</h2>
    <div style="overflow-x:auto"><table class="matrix">
      <thead><tr><th>品种</th>{mcol_headers}</tr></thead>
      <tbody>{hot_html}</tbody>
    </table></div>
    <div style="color:var(--mut);font-size:11px;margin-top:8px">每格上行=净买卖金额(亿元)，下行=倍量份额（＝当日净申购(赎回)份额 ÷（该 ETF 自身 2025-12-31 份额 × 0.1%），单位倍）。
      「—」表示缺少相邻交易日份额、当日净值或疑似折算待核验；「0」表示该日基金份额较上一交易日无变化（申购与赎回相抵），属正常现象，非数据缺失。正值=净买入(红)，负值=净卖出(绿)。</div>
  </div>
  <div class="card"><h2>同类指数品种 · 每日净买卖明细（金额 / 倍量金额 · 最近{HOT}个交易日，亿元 · 可横向滚动）</h2>
    <div style="overflow-x:auto"><table class="matrix">
      <thead><tr><th>指数类别</th>{mcol_headers}</tr></thead>
      <tbody>{cat_daily_html}{cat_daily_tot}</tbody>
    </table></div>
    <div style="color:var(--mut);font-size:11px;margin-top:8px">将跟踪同一指数的多只 ETF 归集加总，观察该指数方向每日整体净买卖。
      每格上行=净买卖金额(亿元)，下行=倍量金额（＝当日净买入金额 ÷（该类 2025-12-31 市值 × 0.1%），单位倍）。
      「0」表示该类当日份额较前日无净变化。正值=净买入(红)，负值=净卖出(绿)。</div>
  </div>
  {research_html}
  <div class="note"><b>口径说明：</b>净买入 / 净卖出 =（当日基金份额 − 前一日基金份额）× 当日单位净值，正值=净买入(净申购)，负值=净卖出(净赎回)。
  份额源：上交所 〈AKShare fund_etf_scale_sse(逐日)〉、深交所 〈fund_scale_daily_szse(区间批量)〉；净值源：天天基金公开净值JSON序列。
  份额为日终口径，当日净买卖反映当日申购赎回结果。缺失值不等于零；汇总为已覆盖样本合计，历史覆盖随基金成立日期和数据源变化。资产规模缺日沿用前值，仅供观察。MSCI中国A50的K线使用560050价格代理。数据不代表未来涨跌预测，不构成投资建议。</div>
  <div class="foot">ETF Flow · Python 采集与计算 · 静态报表</div>
  {KLN_JS}
</div></body></html>"""
    from bs4 import BeautifulSoup
    from web_layout import write_pages
    document = BeautifulSoup(html, 'html.parser')
    chart_cards = document.select('div.wrap > div.card')
    if len(chart_cards) < 6:
        raise RuntimeError('报表图表区块不完整')
    sections = dict(zip(['history','kline','etfs','categories','matrix','category_matrix'],
                        [str(card) for card in chart_cards[:6]]))
    write_pages(p, sections, bars, document.find('style').get_text(), ECHARTS_HTML, KLN_JS, REPORT)
