"""Four focused static screens built from the same verified data snapshot."""
from collections import Counter
from html import escape
from pathlib import Path
import os


NAV=[('index.html','市场概览','01'),('flows.html','资金流明细','02'),('research.html','归因研究','03'),('quality.html','数据与方法','04')]


def number(value, signed=False, digits=2):
    return '—' if value is None else (f'{value:+,.{digits}f}' if signed else f'{value:,.{digits}f}')


def tone(value):
    return '' if value is None else ('pos' if value>=0 else 'neg')


def pill(text,kind=''):
    return f'<span class="pill {kind}">{escape(str(text))}</span>'


def frame(page,title,description,p,body,css,head_extra='',foot_extra=''):
    nav=''.join(f'<a href="{url}"'+(' aria-current="page"' if url==page else '')+f'><span class="nav-num">{n}</span>{label}</a>' for url,label,n in NAV)
    day=p['data_asof']; day=f'{day[:4]}-{day[4:6]}-{day[6:]}'
    script=(Path(__file__).parent/'assets/app.js').read_text(encoding='utf-8')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="dataset-fingerprint" content="{p.get('fingerprint','')}"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · ETF Flow</title><style>{css}</style>{head_extra}</head>
<body><div class="frame"><aside class="sidebar"><a class="brand" href="index.html">ETF Flow<small>宽基资金观察</small></a><div class="nav-label">研究工作台</div><nav class="nav" aria-label="主导航">{nav}</nav><div class="side-foot"><a href="quality.html#downloads">下载研究数据</a>日终份额 · 公开净值<br>不代表未来涨跌预测</div></aside>
<main class="content"><header class="page-head"><div><div class="eyebrow">国家队 ETF 观察 / {title}</div><h1>{title}</h1><p class="page-description">{description}</p></div><div class="head-meta">数据日期<b>{day}</b><button type="button" onclick="location.reload()" title="仅重载已生成网页，不启动数据采集">重新加载</button></div></header>
<div class="scope"><strong>观察口径</strong> · ETF 全市场净申赎不等于国家队买卖。统计异常与交易主体识别分别呈现。</div>{body}
<footer class="page-foot">数据日 <b>{p['data_asof']}</b> · 报表更新时间 <b>{escape(p['updated'])}</b>（北京时间） · 日终估算，非盘中资金流。<br>缺失值不等于零；来源修订会影响历史结果。<a href="quality.html">查看覆盖、证据和方法限制</a>。</footer></main></div><script>{script}</script>{foot_extra}</body></html>'''


def kpis(p):
    dates=[d.replace('-','') for d in p['dates']]
    data=p['date_total']
    items=[]
    for label,days in [('最新交易日',dates[-1:]),('近一周 · 5日',dates[-5:]),('近一月 · 21日',dates[-21:])]:
        values=[data[d] for d in days if data.get(d) is not None]
        value=sum(values) if values else None
        items.append(f'<div class="kpi"><div class="lb">{label}</div><div class="vl {tone(value)}">{number(value,True)}</div><div class="tg muted">亿元 · 已覆盖样本合计</div></div>')
    return '<div class="kpis">'+''.join(items)+'</div>'


def audit_metrics(p):
    counts=p.get('lineage',{}).get('counts',{})
    total=sum(counts.values()); official=counts.get('sse',0)+counts.get('szse',0)
    return total,official,100*official/total if total else 0


def review_table(p):
    rows=[]
    for r in p.get('control_reviews',[]):
        used=r['decision']=='market_proxy_only'
        source=f'<a href="{escape(r["url"],quote=True)}" target="_blank" rel="noopener">中报第{r["page"]}页</a>' if r.get('url') else '缺少本次核验的完整证据'
        rows.append(f'<tr><td><div class="etf-name">{escape(r["name"])}</div><div class="etf-code">{r["code"]}</div></td><td>{pill("情绪代理" if used else "不纳入", "blue" if used else "warn")}</td><td>{number(r.get("institutional_pct"))}%</td><td>{number(r.get("retail_pct"))}%</td><td>{source}</td><td class="detail-row">{escape(r["rationale"])}</td></tr>')
    return '<div class="table-scroll"><table><thead><tr><th>ETF</th><th>审查结论</th><th>机构占比</th><th>个人占比</th><th>原始证据</th><th>使用边界</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'


def write_pages(p, cards, bars, legacy_css, echarts, kline_js, report):
    css=legacy_css+'\n'+(Path(__file__).parent/'assets/app.css').read_text(encoding='utf-8')
    latest=[r for r in p.get('attribution',[]) if r['date']==p['data_asof']]
    alerts=[r for r in latest if r['label'] in ('增量申购异常','增量赎回异常')]
    modeled=[r for r in latest if r.get('residual_rate') is not None]
    q=p['quality']; total,official,percent=audit_metrics(p)
    status_counts=Counter(r['status'] for r in p.get('daily_records',[]))
    valid_flows=sum(r.get('f') is not None for r in p.get('daily_records',[]))
    daily=p['date_total'].get(p['data_asof'])
    direction='净申购' if daily is not None and daily>=0 else '净赎回'
    categories=p.get('cat_total',{}).get(p['data_asof'],{})
    leaders=''.join(f'<div class="category-line"><span>{escape(cat)}</span><b class="{tone(v)}">{number(v,True)} 亿</b></div>' for cat,v in sorted(categories.items(),key=lambda x:abs(x[1]),reverse=True)[:6])
    overview=f'''<section class="card hero"><div class="hero-row"><div><div class="eyebrow">观察池当日合计 · {direction}</div><div class="hero-value">{number(daily,True)}<span>亿元</span></div><p>近一日的申赎方向已知；交易主体仍需披露与多重证据核验。</p><div class="pill-row">{pill(str(q['latest_complete'])+'/'+str(q['target_count'])+' 只可计算')}{pill('数据滞后' if q['is_stale'] else '达到期望数据日')}</div></div><div class="hero-side"><b>{q.get('official_latest',0)}/{q['target_count']}</b>相邻两日官方份额覆盖<br><a href="quality.html" style="color:white">查看数据质量 →</a></div></div></section>'''+kpis(p)
    overview+=f'''<div class="grid-two"><section class="card"><div class="section-head"><h2>每日申赎方向</h2><a href="flows.html">查看走势与明细 →</a></div><p class="small muted">近21个交易日 · 亿元</p><div class="bars">{bars}</div></section><section class="card"><div class="section-head"><h2>指数方向</h2><a href="flows.html#categories">全部类别 →</a></div>{leaders}</section></div>
<div class="grid-equal"><section class="card"><div class="section-head"><h2>归因研究摘要</h2><a href="research.html">打开研究页 →</a></div><div class="big-stat">{len(alerts)} <span class="small muted">只出现增量偏离</span></div><p>{len(modeled)}/{len(latest)} 只具备因子估计结果。当前对照仅用于共同情绪调整，机构身份归因仍受限制。</p><div class="insight"><strong>先看异常，再看它是否稳健</strong><p>研究页列出历史异常分位、拟合解释力和剔除单只对照后的残差区间。</p></div></section><section class="card"><div class="section-head"><h2>历史核验进度</h2><a href="quality.html">查看记录 →</a></div><div class="big-stat">{percent:.1f}%</div><p>已有份额记录的官方来源覆盖</p><div class="progress"><span style="width:{percent:.2f}%"></span></div><p>{official:,}/{total:,} 条已有记录已核验；来源待核验日期 {p.get('lineage',{}).get('pending_dates',0):,} 个。数据可得性缺口另行标记。</p></section></div>'''
    flows=kpis(p)+'<nav class="subnav" aria-label="资金流章节"><a href="#trend">指数联动</a><a href="#history">规模历史</a><a href="#categories">指数汇总</a><a href="#etfs">ETF明细</a></nav>'
    flows+='<section id="trend">'+cards['kline'].replace('<select id="klnCat"','<select aria-label="选择跟踪指数" id="klnCat"')+'</section><section id="history">'+cards['history']+'</section>'
    flows+='<section id="categories">'+cards['categories']+'</section><section id="etfs">'+cards['etfs']+'</section>'
    flows+='<details class="card"><summary>展开最近10日 · 单只ETF矩阵</summary>'+cards['matrix']+'</details><details class="card"><summary>展开最近10日 · 指数归集矩阵</summary>'+cards['category_matrix']+'</details>'
    names={e['c']:e['n'] for e in p['etfs']}
    signal_rows=[]
    for r in sorted(latest,key=lambda r:(r['label'] not in ('增量申购异常','增量赎回异常'),-(abs(r.get('residual_rate') or 0)))):
        code=r['code']; residual=r.get('residual_rate'); unusual=r in alerts
        details=f'''<details class="detail-row"><summary>判断依据</summary><p>{escape(r['reason'])}</p><p>训练 {r['training_count']} 个样本 · 对照 {r['control_count']} 只<br>剔除单只对照后的残差：{number(None if r.get('residual_low') is None else r['residual_low']*1e4)} 至 {number(None if r.get('residual_high') is None else r['residual_high']*1e4)} bp<br>方向稳健：{'是' if r.get('sign_stable') else '否或未估计'}</p><p>{escape(r.get('identity_evidence','机构归因未确认'))}</p></details>'''
        signal_rows.append(f'<tr data-search="{escape(code+" "+names.get(code,""),quote=True)}" data-signal="{"alert" if unusual else "other"}"><td><div class="etf-name">{escape(names.get(code,code))}</div><div class="etf-code">{code}</div></td><td>{pill(r["label"],"warn" if unusual else "")}</td><td class="r {tone(residual)}">{number(None if residual is None else residual*1e4,True)}</td><td class="r">{number(None if r.get("extremeness_percentile") is None else r["extremeness_percentile"]*100, digits=0)}%</td><td class="r">{number(r.get("fit_r2"))}</td><td>{details}</td></tr>')
    reviewed=sum(r['decision']=='market_proxy_only' for r in p.get('control_reviews',[]))
    research=f'''<div class="grid-equal"><section class="card"><div class="eyebrow">统计判断</div><div class="big-stat">{len(alerts)} 只异常偏离</div><p>以已审查市场代理剔除共同申赎，使用此前样本估计当日残差。</p>{pill('统计异常不等于机构交易','blue')}</section><section class="card"><div class="eyebrow">身份归因</div><div class="big-stat">证据仍有边界</div><p>已审查A500并非无国家队的干净对照；不输出身份概率，不将残差金额当作国家队买卖。</p>{pill('机构身份未核验','warn')}</section></div>
<section class="card"><div class="section-head"><h2>当日逐只研究判断</h2><a href="data/attribution_daily.csv">下载全部日度结果</a></div><div class="toolbar"><label for="signal-search">筛选</label><input id="signal-search" type="search" placeholder="ETF代码或名称" data-filter-table="signals"><select aria-label="研究判断类型" data-signal-table="signals"><option value="all">全部判断</option><option value="alert">只看异常偏离</option><option value="other">其他与证据不足</option></select><span class="small muted" data-filter-count="signals"></span></div><div class="table-scroll"><table id="signals"><thead><tr><th>ETF</th><th>统计判断</th><th class="r">残差 bp</th><th class="r">异常分位</th><th class="r">R²</th><th>证据</th></tr></thead><tbody>{''.join(signal_rows)}</tbody></table></div><p>1 bp = 0.01%。分位是历史残差异常程度，不是国家队参与概率。研究阈值未校准为统计显著性或交易成功率，跨ETF比较尚未做多重检验校正。</p></section>
<section class="card"><h2>对照组审查</h2><p>{reviewed}只获准作市场情绪代理，其余因本次证据不足不进入模型。机构与联接份额统计口径不同，不能简单相加。</p>{review_table(p)}</section>'''
    anchor_rows=''.join(f'<tr><td>{a["code"]}</td><td>{escape(a["holder"])}</td><td>{a["report_date"]}</td><td>{a["published_at"]}</td><td class="r">{a["shares"]/1e8:,.2f}</td><td><a href="{escape(a["source_url"],quote=True)}">披露原文</a></td></tr>' for a in p.get('anchors',[]) if a.get('verified'))
    research+='<section class="card"><h2>披露锚点与条件区间</h2><div class="table-scroll"><table><thead><tr><th>ETF</th><th>持有人</th><th>报告期</th><th>公告日</th><th>亿份</th><th>来源</th></tr></thead><tbody>'+anchor_rows+'</tbody></table></div><p>披露端点不能唯一恢复每日交易。条件持仓容量边界还依赖份额单位可比、无未记录折算；持仓减少也不等于ETF净赎回。</p><a class="action-link" href="data/holding_bounds.csv">下载条件容量区间</a></section>'
    bounds=[b for b in p.get('holding_bounds',[]) if b['date']==p['data_asof'] and b['minimum_holding_reduction']>0]
    if bounds:
        bound_rows=''.join(f'<tr><td>{b["code"]}</td><td>{b["anchor_date"]}</td><td class="r">{b["anchor_holdings"]/1e8:,.2f}</td><td class="r">{b["holding_max"]/1e8:,.2f}</td><td class="r">{b["minimum_holding_reduction"]/1e8:,.2f}</td></tr>' for b in bounds)
        research+='<section class="card"><h2>披露锚点的条件约束</h2><p>在份额单位可比、没有未记录折算的条件下，已披露账户的持仓不可能超过当前ETF总份额，因此可得到相对锚点的持仓减少下限。</p><div class="table-scroll"><table><thead><tr><th>ETF</th><th>锚点报告期</th><th class="r">锚点合计亿份</th><th class="r">当前ETF总亿份</th><th class="r">条件减少下限亿份</th></tr></thead><tbody>'+bound_rows+'</tbody></table></div><p>这是从报告期至今的区间约束，不能定位到某一天；减少还可能通过二级市场交易发生。若发现折算或披露口径变更，须重算。</p></section>'
    downloads=[('daily_etf.csv','ETF日度结果','份额、净值、净申赎及来源'),('daily_categories.csv','指数归集','每日金额及覆盖数'),('daily_indices.csv','指数收盘','历史行情与代理标记'),('attribution_daily.csv','归因研究','残差、拟合、证据与敏感度'),('control_reviews.csv','对照审查','审查决定及报告出处'),('anchor_intervals.csv','披露端点','两期持有人份额净变化'),('holding_bounds.csv','条件容量区间','带假设的持仓范围'),('manifest.json','数据清单','文件行数、版本、SHA-256'),('history_verification.json','历史核验报告','批次、失败数与覆盖变化')]
    quality=f'''<div class="kpis"><div class="kpi"><div class="lb">已有份额记录 · 官方来源</div><div class="vl">{percent:.1f}%</div><div class="tg muted">{official:,}/{total:,} 条</div></div><div class="kpi"><div class="lb">来源待核验日期</div><div class="vl">{p.get('lineage',{}).get('pending_dates',0):,}</div><div class="tg muted">仅指已有记录，非所有潜在缺口</div></div><div class="kpi"><div class="lb">最新日可计算</div><div class="vl">{q['latest_complete']}/{q['target_count']}</div><div class="tg muted">两日官方份额 {q.get('official_latest',0)}/{q['target_count']}</div></div></div>
<section class="card"><h2>核验结果与可用性</h2><div class="insight"><strong>来源核验与数据完整性分别检查</strong><p>官方核验只覆盖已有记录及此次取得的有效日期，成立前、未取得的净值、疑似折算等仍保留为空。历史样本数随基金存续和数据可得性变化。当前历史净申赎可计算 {valid_flows:,} 条，疑似折算 {status_counts.get('possible_split',0)} 条留空待核验。</p></div><div class="insight"><strong>目前为修订数据集</strong><p>份额可追溯到官方，但尚无完整首次发布时间与逐次不可覆盖快照；不能直接当作严格时点回测集。</p></div><div class="insight"><strong>缺失与来源</strong><p>最新缺失代码：{escape('、'.join(q.get('missing_latest',[])) or '无')}。K线可用 {q.get('kline_coverage',0)}/{len(p['dates'])} 日。MSCI中国A50图表使用560050价格代理。</p></div></section>
<section class="card" id="downloads"><h2>下载研究数据</h2><div class="downloads">'''+''.join(f'<a href="data/{name}">{label}<small>{desc}</small></a>' for name,label,desc in downloads)+'''</div></section>
<section class="card"><h2>计算与判定方法</h2><details class="method"><summary>净申赎怎样计算？</summary><p>（当日总份额－上一交易日总份额）× 当日单位净值。缺少相邻交易日份额或当日净值时留空。数据来自上交所、深交所与天天基金。</p></details><details class="method"><summary>折算与历史修复怎样处理？</summary><p>确认公告按折算因子计算真实申赎；无公告的疑似折算留空待核验。官方记录可覆盖兜底或未知来源，旧日期修复不受12天窗口限制。</p></details><details class="method"><summary>统计异常怎样变为归因判断？</summary><p>至少3只已审查代理、20个此前训练样本，最多60个样本拟合共同因子。异常需结合拟合解释力、残差分位、剔除对照后的方向稳健性与持有人披露；没有干净对照时不能提高机构归因证据等级。</p></details><details class="method"><summary>更新与数据延迟</summary><p>页面重新加载不触发采集。GitHub Actions按配置时间采集并发布；公共接口可能晚发布或失败。请同时看数据日期、官方来源和覆盖数。</p></details></section>'''
    pages={
        'index.html':frame('index.html','市场概览','先看当日方向，再进入明细与证据。',p,overview,css),
        'flows.html':frame('flows.html','资金流明细','查看指数联动、类别汇总与逐日ETF记录。',p,flows,css,echarts,kline_js),
        'research.html':frame('research.html','归因研究','把统计偏离、对照审查和持有人证据分开判断。',p,research,css),
        'quality.html':frame('quality.html','数据与方法','查看历史核验、可用性、下载和计算边界。',p,quality,css),
    }
    root=Path(report).parent
    for name,html in pages.items():
        dest=root/name; tmp=dest.with_suffix('.html.tmp');tmp.write_text(html,encoding='utf-8',newline='\n');os.replace(tmp,dest)
    dest=Path(report); tmp=Path(str(dest)+'.tmp');tmp.write_text(pages['index.html'],encoding='utf-8',newline='\n');os.replace(tmp,dest)
    print('四页报表已生成:',str(root),flush=True)
