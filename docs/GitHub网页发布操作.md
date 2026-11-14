# GitHub 网页发布与每日维护

本项目使用 GitHub Actions + GitHub Pages，无需另购服务器。

- 仓库保存代码和公开历史数据。
- Actions 是云端定时运行 Python 的执行环境，负责更新报表。
- Pages 托管生成的 HTML，用户通过固定网址访问，不需要安装 Python。

访问者打开网页只会看到最近一次成功发布的数据，不会当场执行采集。

## 当前交付状态

本地采集和报表已验收，部署步骤已加入 `.github/workflows/daily-refresh.yml`。本次环境中的GitHub连接器写操作被审批策略拒绝，本机CLI令牌失效，因此这些改动尚未上传，云端部署未完成。下面是完成首次上线所需的操作。

## 第一次上线

### 1. 重新登录本机 GitHub

在本机打开 PowerShell：

```powershell
cd E:\AI\national-team-etf-flow
gh auth login --hostname github.com --web --git-protocol https
gh auth setup-git
```

按终端提示在浏览器登录 `joehang1101`，确认这是拥有仓库写入权限的账号。GitHub若要求授权工作流权限，应允许上传仓库内 `.github/workflows/` 的变更。不要把个人Token写入代码或发到聊天。

### 2. 启用 Actions 和 Pages

浏览器打开仓库：

https://github.com/joehang1101/national-team-etf-flow

1. 点顶部 **Actions**。如出现“理解工作流并启用”的按钮，点击启用。
2. 点 **Settings → Pages**。
3. 在 **Build and deployment → Source** 中选择 **GitHub Actions**。本项目已经带工作流，不需要再选择Jekyll模板。
4. 点 **Settings → Actions → General**，确认允许此仓库运行官方Actions；工作流本身声明所需的 `contents: write`、`pages: write` 和 `id-token: write` 权限。若组织策略或分支保护限制bot写入，须按对应提示调整。

这里只发布生成的首页，不会将整个仓库文件作为网站目录上传。

### 3. 上传这次准备好的项目改动

回到 PowerShell：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\publish.ps1
```

脚本会验证登录及远程仓库、跑测试、检查远程是否有新提交，再提交指定项目文件并推送main。它不会强制覆盖远程，也不会提交日志或本地临时文件。

若提示Git身份未设置，先填写你自己的提交身份后重跑：

```powershell
git config user.name "你的GitHub用户名"
git config user.email "你的GitHub提交邮箱"
```

若提示远程已推进，先检查 `git fetch origin` 后的差异并合并，再发布，不要使用 `--force`。

### 4. 查看第一次部署

1. 打开仓库 **Actions**。
2. 点击 **每日自动刷新国家队ETF报表** 最近的一次任务。推送main会自动触发，也可以点该工作流 → **Run workflow → main → Run workflow**。
3. 等待安装依赖、测试、采集、提交、配置Pages、上传网页、发布网页完成，采集可能需要几分钟。
4. 任务应为绿色，最后的“发布网页”步骤应给出网站地址。
5. 打开：

https://joehang1101.github.io/national-team-etf-flow/

6. 检查网页数据日期和覆盖数，确认与Actions日志一致。首次发布可能需要再等待片刻。

## 之后怎样更新

通常无需开电脑。云端每天按北京时间07:45至09:30每15分钟、16:00及23:00运行。GitHub定时触发可能延迟，交易所数据也可能晚发布；周末及节假日没有新交易数据通常正常。

立即更新：进入 **Actions → 每日自动刷新国家队ETF报表 → Run workflow**。刷新网页只重新加载已有网页，不能代替这个操作。

以后修改代码：本机测试后正常 `git add`、`git commit`、`git push`，推送main将触发更新与部署。`publish.ps1`为本次首次发布准备，若已有暂存变更需先自行检查处理。

## 为什么不用“Deploy from a branch”

默认 `GITHUB_TOKEN` 创建的提交不会再次触发Pages分支构建。为避免“仓库数据更新、网站未更新”，本项目在同一工作流里显式执行 `configure-pages` → `upload-pages-artifact` → `deploy-pages`。因此Pages的Source必须选择 **GitHub Actions**。

官方说明：https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages

## 常见故障

| 现象 | 处理 |
|---|---|
| 网站404 | 检查Pages Source是否为GitHub Actions，以及最后部署步骤是否成功 |
| configure-pages报找不到站点 | 先完成Settings → Pages的Source选择，再手动运行工作流 |
| 推送工作流报权限不足 | 登录正确账号，检查授权是否允许修改workflow以及仓库写权限 |
| bot提交时报403 | 检查Actions写入权限、组织策略和main分支保护规则 |
| 测试/采集失败 | 在失败步骤查看日志；部署不会继续，网站保留上次成功版本 |
| 网站数据日期没变 | 对照最近任务日志；可能没有新交易数据、源尚未发布或沿用缓存，检查覆盖率提示 |
| 没有定时任务 | 检查Actions是否启用；GitHub可能因公开仓库长期无活动而停用定时工作流，必要时重新启用 |

“部署成功”与“数据最新”是两个检查：先看任务是否绿色，再看网页数据日和覆盖数。
