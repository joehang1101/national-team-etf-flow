"""Small homepage summary and a separate, progressively disclosed feature page."""
from html import escape


def fmt(value, digits=2, signed=False, scale=1):
    if value is None:
        return '—'
    return f'{value * scale:+,.{digits}f}' if signed else f'{value * scale:,.{digits}f}'


def pct(value, signed=False):
    return fmt(value, signed=signed, scale=100) + ('%' if value is not None else '')


def state_summary(p):
    s = p.get('features', {}).get('latest', {})
    if not s:
        return ''
    return f'''<section class="card state-summary" aria-labelledby="state-title"><div class="section-head"><div><div class="eyebrow">当前状态 · {s['expected']}只候选，不含A500对照</div><h2 id="state-title">{escape(s['state'])}</h2></div><a href="signals.html">查看七类特征 →</a></div><p>{escape(s.get('summary_text',''))}</p><div class="state-facts"><div><span>候选池当日净申赎</span><b>{fmt(s.get('net_flow_cny'),signed=True,scale=1e-8)} 亿</b></div><div><span>规模标准化强度</span><b>{pct(s.get('flow_1d_rate'),True)} <small>z {fmt(s.get('flow_1d_z'),signed=True)}</small></b></div><div><span>净流入广度</span><b>{s.get('inflow_count',0)}/{s.get('covered',0)} <small>只</small></b></div><div><span>中证全指当日</span><b>{pct(s.get('return_1d'),True)}</b></div></div><p>{escape(s['state_reason'])}</p><p class="small">覆盖 {s['covered']}/{s['expected']} · 状态规则用于观察仓位节奏，不生成买卖价或收益预测。交易主体未确认。</p></section>'''


def signals_body(p):
    features = p.get('features', {})
    s = features.get('latest', {})
    if not s:
        return '<section class="card"><h2>特征数据尚未生成</h2></section>'
    def card(n, title, value, description, evidence):
        return f'<section class="card feature-card"><div class="eyebrow">{n} / {title}</div><h2>{value}</h2><p>{description}</p><details><summary>展开计算依据</summary>{evidence}</details></section>'
    horizon = ''.join(f'<tr><td>{n}日</td><td class="r">{pct(s.get(f"flow_{n}d_rate"),True)}</td><td class="r">{fmt(s.get(f"flow_{n}d_z"),signed=True)}</td><td class="r">{s.get(f"flow_{n}d_samples",0)}/60</td></tr>' for n in (1,5,10,20))
    strength = card('01','强度',f'z {fmt(s.get("flow_1d_z"),signed=True)}',
        f'当日相对净申赎 {pct(s.get("flow_1d_rate"),True)}；位于此前60个完整交易日的 {pct(s.get("rate_percentile_60"))} 历史位置。',
        f'<p>分母是各ETF前一交易日总份额×前一交易日净值的合计 {fmt(s.get("previous_assets_cny"),scale=1e-8)} 亿元，不是国家队持仓。60日均值 {pct(s.get("rate_mean_60"),True)}，样本标准差 {pct(s.get("rate_sigma_60"))}。</p><div class="table-scroll"><table><thead><tr><th>期限</th><th class="r">累计相对流量</th><th class="r">z</th><th class="r">有效基准</th></tr></thead><tbody>{horizon}</tbody></table></div>')
    breadth = card('02','广度',f'{s["inflow_count"]}/{s["covered"]} 只净流入',
        f'已覆盖样本中占 {pct(s.get("inflow_share"))}。指数类别{s["category_direction"]}。',
        f'<p>候选池覆盖 {s["covered"]}/{s["expected"]}；净流出 {s["outflow_count"]} 只，其余为零流量。完整类别 {s["category_covered"]}/{s["category_expected"]}，其中 {s["category_inflow_count"]} 类净流入、{s["category_outflow_count"]} 类净流出。单个类别按净申赎金额归集。广泛同向可能来自共同情绪，不能单独识别组织行为。</p>')
    structure_rows = ''.join(f'<tr><td>{name}</td><td class="r">{pct(s.get("inflow_"+key+"_share"))}</td><td class="r">{pct(s.get("outflow_"+key+"_share"))}</td></tr>' for key,name in [('large','大盘'),('small','小盘与成长'),('other','其他')])
    structure = card('03','结构',f'大盘占流入 {pct(s.get("inflow_large_share"))}',
        f'小盘与成长占流入 {pct(s.get("inflow_small_share"))}；流出侧单独比较，避免正负相抵后比例失真。',
        f'<p>大盘：沪深300、上证50；小盘与成长：中证1000、创业板、科创50；中证500等归入其他。流入分母为所有正流量之和，流出分母为所有负流量绝对值之和。流入合计 {fmt(s.get("inflow_gross_cny"),scale=1e-8)} 亿，流出绝对额 {fmt(s.get("outflow_gross_cny"),scale=1e-8)} 亿；某侧为零时占比留空。</p><table><thead><tr><th>分组</th><th class="r">占流入</th><th class="r">占流出</th></tr></thead><tbody>{structure_rows}</tbody></table>')
    contrarian = card('04','逆势',f'指数当日 {pct(s.get("return_1d"),True)}',
        f'中证全指3日 {pct(s.get("return_3d"),True)}，距60日收盘高点 {pct(s.get("drawdown_60"),True)}。',
        '<p>当日与3日价格变动同时列出，供比较资金与行情方向。状态规则仅在当日价格方向相反、|z|≥2且至少2/3候选同向时标记托底或控速形态，不证明交易主体或未来收益。</p>')
    streak = s.get('inflow_streak') if (s.get('flow_1d_rate') or 0)>0 else s.get('outflow_streak')
    streak_label = '流入' if (s.get('flow_1d_rate') or 0)>0 else '流出' if (s.get('flow_1d_rate') or 0)<0 else '同向'
    persistence = card('05','持续性',f'连续{streak_label} {fmt(streak,0)} 日',
        f'5日均值 {pct(s.get("mean_5"),True)}，20日均值 {pct(s.get("mean_20"),True)}。',
        f'<p>均值差（5日−20日）{fmt(s.get("mean_5_minus_20"),scale=1e4,signed=True)} bp。正值表示近期相对流量更偏流入，负值更偏流出；不以接近零或负数的20日均值作除数。缺失或成员变化中断连续计数，零流量同时中断流入与流出。</p>')
    background = card('06','背景',f'距250日线 {pct(s.get("distance_ma250"),True)}',
        f'中证全指20日年化波动率 {pct(s.get("volatility_20_annualized"))}。',
        '<p>250日线采用截至当日的连续250个交易日收盘均值。波动率是最近20个对数日收益的样本标准差×√252，需要21个连续收盘。缺行情不前向填充。</p>')
    controls = card('07','对照',f'代理中位流量 {pct(s.get("control_rate"),True)}',
        f'{s["control_count"]}只已审查代理；候选池与代理的原始差 {fmt(s.get("raw_control_gap"),scale=1e4,signed=True)} bp。',
        f'<p>代码：{escape(s.get("control_codes","") or "无")}。原始差是两种规模标准化资金率之差，未做回归调整，不能当作归因残差。归因页另有逐只回归、{s["residual_alert_count"]}只增量偏离和{s["weak_fit_count"]}只解释力不足。代理可能包含国家队，身份归因仍未确认。</p><a href="research.html">核对回归与持有人证据 →</a>')
    rows = []
    for r in features.get('latest_etfs', []):
        rows.append(f'<tr data-search="{escape(r["code"]+" "+r["name"],quote=True)}"><td><div class="etf-name">{escape(r["name"])}</div><div class="etf-code">{r["code"]}</div></td><td class="r">{pct(r.get("flow_1d_rate"),True)}</td><td class="r">{fmt(r.get("flow_1d_z"),signed=True)}</td><td class="r">{fmt(r.get("flow_5d_z"),signed=True)}</td><td class="r">{fmt(r.get("flow_10d_z"),signed=True)}</td><td class="r">{fmt(r.get("flow_20d_z"),signed=True)}</td><td class="r">{fmt(r.get("inflow_streak"),0)}/{fmt(r.get("outflow_streak"),0)}</td></tr>')
    history = ''.join(f'<tr><td>{r["date"]}</td><td>{escape(r["state"])}</td><td class="r">{fmt(r.get("flow_1d_z"),signed=True)}</td><td class="r">{pct(r.get("inflow_share"))}</td><td class="r">{pct(r.get("return_1d"),True)}</td><td class="r">{r["covered"]}/{r["expected"]}</td></tr>' for r in reversed(features.get('pool_history', [])[-60:]))
    return state_summary(p)+f'''<div class="feature-grid">{strength}{breadth}{structure}{contrarian}{persistence}{background}{controls}</div>
<section class="card"><div class="section-head"><h2>逐只候选 · 相对资金强度</h2><a href="data/features_daily.csv">下载完整历史特征</a></div><div class="toolbar"><label for="feature-search">筛选</label><input id="feature-search" type="search" placeholder="ETF代码或名称" data-filter-table="features"><span class="small muted" data-filter-count="features"></span></div><div class="table-scroll"><table id="features"><thead><tr><th>ETF</th><th class="r">当日相对流量</th><th class="r">1日z</th><th class="r">5日z</th><th class="r">10日z</th><th class="r">20日z</th><th class="r">连续入/出</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p>z的正负是相对历史常态的偏离方向，可能与当日净申赎正负不同。覆盖、基准不全或历史标准差为零时显示“—”。</p></section>
<details class="card"><summary>展开最近60日状态记录</summary><div class="table-scroll"><table><thead><tr><th>日期</th><th>状态</th><th class="r">1日z</th><th class="r">流入占比</th><th class="r">指数日涨跌</th><th class="r">覆盖</th></tr></thead><tbody>{history}</tbody></table></div></details>
<section class="card"><h2>怎样理解标准化与状态提示？</h2><details class="method" open><summary>同样净申购50亿，为什么意义不同？</summary><p>先假设ETF资产规模相同：此前60日平均净申购10亿、标准差20亿，今天50亿对应z=(50−10)/20=2；若标准差为40亿，z=1。实际特征先除以前一日ETF总资产，再用此前60个交易日的均值和样本标准差计算，减少规模差异。</p><p>z=2只表示高于均值两个标准差。资金流可能偏态、厚尾且相关，不能直接换成发生概率；页面另列经验历史分位，二者都不是交易成功率。<a href="https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/standard.htm" target="_blank" rel="noopener">标准化公式参考：NIST</a>。</p></details>
<details class="method"><summary>如何保证基准不包含当前信号？</summary><p>1日z使用当日之前60个完整交易日。5/10/20日先累计每日相对流量，再与当前窗口开始之前的60个同期限历史窗口比较；历史窗口彼此重叠，不能当60次独立实验。当前窗口和基准都要求完整、同一组成员，缺一天就不计算，不能跳过缺失拼接。历史不足或成员变化导致z留空。</p></details>
<details class="method"><summary>状态规则和使用边界</summary><p>先检查完整覆盖、60日基准和指数行情；再检查逆势广度组合、|z|≥2的强度异常、连续至少3日同向，最后为常态区间观察。正净流入不一定有正z，广度也不证明有组织交易。</p><p>这是当前修订数据的描述性规则，尚未通过独立样本、增量收益或交易成本检验。没有校准仓位比例、买卖价、成功率或身份概率。市场背景、数据日期与披露证据应同时阅读；不代表未来涨跌预测。</p></details></section>'''
