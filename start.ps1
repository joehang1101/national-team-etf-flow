param([int]$Port = 8765, [switch]$Refresh, [switch]$Offline, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Refresh -and $Offline) { throw 'Choose -Refresh or -Offline.' }
if ($Refresh -or $Offline) {
    $refreshArgs = @('-X', 'utf8', 'refresh.py')
    if ($Offline) { $refreshArgs += '--offline' }
    & python @refreshArgs
    if ($LASTEXITCODE -ne 0) { throw 'Refresh failed; existing report is still available.' }
}
$serveArgs = @('-X', 'utf8', 'serve.py', '--port', "$Port")
if ($NoBrowser) { $serveArgs += '--no-browser' }
& python @serveArgs
exit $LASTEXITCODE
