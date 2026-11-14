param([switch]$Push, [switch]$Offline)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$refreshArgs = @('-X', 'utf8', 'refresh.py')
if ($Offline) { $refreshArgs += '--offline' }
& python @refreshArgs
if ($LASTEXITCODE -ne 0) { throw 'Refresh failed. See the error above.' }
if (-not $Push) {
    Write-Host 'Local report updated. Run .\start.ps1 to preview.'
    exit 0
}
# Uses the configured origin and normal Git credential manager.
& git add -- index.html '国家队ETF每日净买入卖出报表.html' _etf_shares_cache.json _nav_cache.json _kline_cache.json data
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
& git diff --cached --quiet
if ($LASTEXITCODE -eq 0) { Write-Host 'No generated data changes.'; exit 0 }
if ($LASTEXITCODE -ne 1) { throw 'git diff failed' }
$allowed = @('index.html', '国家队ETF每日净买入卖出报表.html', '_etf_shares_cache.json', '_nav_cache.json', '_kline_cache.json')
$staged = & git -c core.quotepath=false diff --cached --name-only
if ($staged | Where-Object { $_ -notin $allowed -and $_ -notlike 'data/*' }) { throw 'Unrelated staged files exist. Review them before publishing.' }
& git commit -m ('chore: refresh ETF report ' + (Get-Date -Format 'yyyy-MM-dd HH:mm'))
if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
& git push origin HEAD
if ($LASTEXITCODE -ne 0) { throw 'git push failed' }
