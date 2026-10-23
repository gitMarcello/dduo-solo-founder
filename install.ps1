$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Error "dDuo Solo Founder installer requires Node.js 22.23.4+, 24.22.1+ or 26.12.1+. Install Node.js and retry."
}

& node (Join-Path $Root "bin/install.mjs") @args
exit $LASTEXITCODE

