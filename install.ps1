$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Error "dDuo Solo Founder installer requires Node.js 18 or newer. Install Node.js and retry."
}

& node (Join-Path $Root "bin/install.mjs") @args
exit $LASTEXITCODE

