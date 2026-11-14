# 宽基 ETF 净申赎观察报表

跟踪 **34 只 ETF、12 类指数**，用日终基金份额和单位净值估算全市场净申赎。Python 生成静态 HTML，浏览器直接展示，无需数据库、账号或付费 API Key。

**观察池不等于汇金真实持仓。这里无法识别交易参与者，不能据此断言“国家队今天买了多少”。** 原仓库的23只基础名单和11只扩展名单保留为观察分组，持仓归属尚未逐项附公告核验。

## 在 Windows 本机运行

首次安装依赖（Python 3.11及以上；本次本机使用3.14验证）：

```powershell
cd E:\AI\national-team-etf-flow
python -m pip install -r requirements.txt
```

双击 `启动报表.bat`，或者运行：

```powershell
.\start.ps1
```

打开 `http://127.0.0.1:8765/`。默认立即展示现有报表，终端按 Ctrl+C 停止服务；端口被占用可运行 `.\start.ps1 -Port 8766`。PowerShell若限制脚本执行，可使用上面的批处理入口。

联网更新全部数据：

```powershell
python -X utf8 refresh.py
```

更新后预览：`.\start.ps1 -Refresh`。仅用本地缓存重新计算：

```powershell
python -X utf8 refresh.py --offline
```

全量历史核验可运行 `python -X utf8 verify_history.py --workers 4`，支持检查点恢复。已完成的官方记录不会重复请求。

离线需要 `_etf_shares_cache.json`、`_nav_cache.json` 和 `_kline_cache.json`；仓库保留公共数据缓存以支持开箱演示。图表库已放在 `assets/` 并内联进HTML，打开报表不用再访问 CDN。

其他选项：

- `--require-fresh`：日期滞后或最新日覆盖不足则返回非零状态，保留已发布报表。
- `--fresh-retries 1`：滞后时额外等待150秒后重试，默认0，避免本机无提示长等。
- `python -X utf8 -m unittest discover -s tests -v`：验证计算、交易日、离线链路和失败保留旧报表。

页面中的“重新加载报表”只刷新浏览器，不会启动 Python。要取得新数据须运行脚本或等待 GitHub Actions。

## 页面分层

- `index.html`：市场概览，先看候选池状态摘要，再看全观察池金额和研究摘要。
- `signals.html`：状态与七类特征、标准化示例、逐只候选z值、历史状态与可展开方法。
- `flows.html`：指数走势、资产规模、ETF与类别明细，矩阵按需展开。
- `research.html`：统计偏离、筛选、拟合与对照剔除敏感度、披露出处。
- `quality.html`：历史核验、下载、数据可用性和方法边界。

## 功能

- 最新交易日、5日、21日、120日、242日、今年以来净申赎合计。
- 近21交易日每日合计柱状图。
- 观察池资产规模历史曲线。
- 中证全指及12类指数 K 线与净申赎联动，支持切换类别、时间范围、悬停、缩放。
- 单只ETF及同类指数汇总，近10交易日矩阵和基于2025-12-31的倍量指标。
- 显示数据日期、本次运行期望日期、最新日可计算覆盖数、缺失代码及K线覆盖。

## 文件与运行链路

| 文件 | 用途 |
|---|---|
| `refresh.py` | 命令行入口与流程调度 |
| `web_layout.py` / `feature_layout.py` / `assets/app.css` | 五页布局与统一视觉样式 |
| `features.py` | 规模标准化、此前60日z、广度、结构、行情背景与描述性状态 |
| `verify_history.py` | 全量历史来源核验与检查点 |
| `fetch.py` / `compute.py` / `render.py` | 采集、汇总、网页渲染 |
| `provenance.py` / `attribution.py` / `exports.py` | 来源修复、归因研究、CSV导出 |
| `config/` / `data/` | 版本化观察池、事件、披露锚点 / 研究数据集 |
| `calculations.py` | 纯计算：相邻交易日差分、缺失处理、疑似折算标记 |
| `_etf_shares_cache.json` | 每日每只ETF的总份额、交易日历与历史回补状态 |
| `_nav_cache.json` | 每只ETF每日单位净值；采集失败保留已有历史 |
| `_kline_cache.json` | 指数K线历史，主源/备用源失效时补齐图表 |
| `_etf_data.json` | 本机生成的结构化计算结果，含质量信息；不提交 |
| `index.html` | 本地与 GitHub Pages 统一首页 |
| `国家队ETF每日净买入卖出报表.html` | 同内容的中文文件名报表 |
| `report_latest.html` | 同内容的ASCII文件名副本；不提交 |
| `serve.py` / `start.ps1` | 本机静态预览服务/启动入口 |
| `update_and_push.ps1` | 默认仅刷新；显式加 `-Push` 才提交生成文件并推送当前origin |

详细原理见 [docs/实现原理.md](docs/实现原理.md)，当前来源追踪、模块结构、CSV与归因研究规则以 [数据质量与归因](docs/数据质量与归因.md) 为准。

## GitHub 自动更新和发布

`.github/workflows/daily-refresh.yml` 每天在北京时间07:45、08:00、08:15、08:30、08:45、09:00、09:15、09:30、16:00、23:00触发，也可在 Actions 手动 Run workflow。GitHub定时任务可能延迟，不保证准点；节假日通常没有新交易数据。

工作流安装统一依赖 → 运行测试 → 按需采集并计算 → 提交五个网页、中文首页副本、研究数据与缓存 → 上传五页和data目录 → 部署Pages。推送main或手动取消refresh_data时直接发布已生成文件；定时任务及默认手动运行先采集再发布。无内容变化则不提交；任务设串行并发组和25分钟超时。使用仓库自带 `GITHUB_TOKEN`，无需在源码或远程URL中填写个人Token。

首次启用需：

1. 将本地代码更改提交到 GitHub 仓库。
2. 在仓库 Actions 中启用工作流（fork的定时任务可能默认停用）。
3. Settings → Actions → General → Workflow permissions 确保允许读写仓库；分支保护规则也可能阻止bot直接推送。
4. Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**。
5. 手动运行一次刷新，检查运行日志、生成提交和 Pages 部署结果。

完整的首次发布、日常维护和故障排查操作见 [docs/GitHub网页发布操作.md](docs/GitHub网页发布操作.md)。

Pages 地址为 `https://joehang1101.github.io/national-team-etf-flow/`。每次上线仍需核对Actions部署成功与网页数据日期。

## 数据口径

日净申赎估算（亿元）＝（当日总份额－上一交易日总份额）× 当日单位净值 ÷ 100000000。

`config/events.json`支持公告确认的份额折算；`data/`输出日度CSV和校验清单。归因模块输出证据等级，不输出身份概率。

特征使用26只候选池，8只A500对照候选不计入该池；原金额图表仍为34只全观察池。日度资金率=净申赎÷前一交易日ETF总资产，非国家队持仓。z使用此前60个完整交易日的样本均值/标准差，累计窗口与其基准不共享日期，缺失、成员变化、零波动基准留空。状态为描述性规则，不提供仓位百分比或买卖价。完整定义见 [特征与状态](docs/特征与状态.md)。

缺少相邻交易日份额或当日净值时显示“—”，不拿旧净值计算，也不把跨多日变化归给一天。疑似份额折算同样留空待核验。合计仅包含可计算样本，历史覆盖可能变化；未做固定样本可比性调整。资产规模＝各ETF总份额×净值的加总，缺日沿用可用前值，不是汇金持仓价值。

份额由上交所、深交所经AKShare获取；净值读取天天基金公开数据文件的JSON序列；K线使用东财、腾讯及本地历史缓存。公共接口可能延迟、受限或变更；缓存不代表本次已重新验证全部历史。MSCI中国A50图表使用560050价格代理，并非该指数原始点位。

数据观察不代表未来涨跌预测，不构成投资建议。
