# Run this script yourself after gh auth login and gh auth setup-git.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& gh auth status
if ($LASTEXITCODE -ne 0) { throw 'Run gh auth login --hostname github.com --web --git-protocol https, then gh auth setup-git.' }
$origin = & git remote get-url origin
if ($LASTEXITCODE -ne 0 -or $origin -ne 'https://github.com/joehang1101/national-team-etf-flow.git') { throw 'Unexpected origin; review the repository before publishing.' }
$branch = & git branch --show-current
if ($branch -ne 'main') { throw 'Expected main branch. Review your branch before publishing.' }
& python -X utf8 -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& git fetch origin main
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed; check network and login.' }
& git merge-base --is-ancestor origin/main HEAD
if ($LASTEXITCODE -ne 0) { throw 'Remote main has advanced. Review and merge remote changes before publishing.' }
# Stage only the project files prepared for this release.
$releaseFiles = @('.github/workflows/daily-refresh.yml', '.gitignore', 'README.md',
    'refresh.py', 'fetch.py', 'compute.py', 'render.py', 'runtime.py', 'universe.py',
    'provenance.py', 'attribution.py', 'exports.py', 'web_layout.py', 'features.py', 'feature_layout.py', 'verify_history.py', 'config', 'data',
    'calculations.py', 'requirements.txt', 'serve.py', 'start.ps1',
    'update_and_push.ps1', 'publish.ps1', 'assets', 'docs', 'tests', 'index.html', 'signals.html', 'flows.html', 'research.html', 'quality.html',
    '_etf_shares_cache.json', '_nav_cache.json', '_kline_cache.json',
    '启动报表.bat', '发布步骤.txt', '国家队ETF每日净买入卖出报表.html')
$alreadyStaged = & git -c core.quotepath=false diff --cached --name-only
if ($alreadyStaged) { throw 'Staged files already exist. Review or unstage them before running this script.' }
& git add -- @releaseFiles
if ($LASTEXITCODE -ne 0) { throw 'Cannot stage files.' }
& git diff --cached --quiet
$diffCode = $LASTEXITCODE
if ($diffCode -eq 1) {
    & git commit -m 'feat: validate ETF pipeline and deploy report to GitHub Pages'
    if ($LASTEXITCODE -ne 0) { throw 'Commit failed. Set your Git user.name and user.email if needed.' }
} elseif ($diffCode -ne 0) { throw 'Cannot inspect staged changes.' }
& git push origin main
if ($LASTEXITCODE -ne 0) { throw 'Push failed. Check repository permissions and workflow scope.' }
Write-Host 'Uploaded. Check the workflow at https://github.com/joehang1101/national-team-etf-flow/actions'
