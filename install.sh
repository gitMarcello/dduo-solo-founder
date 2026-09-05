#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v node >/dev/null 2>&1; then
  echo "dDuo Solo Founder installer requires Node.js 18 or newer." >&2
  echo "Install Node.js, then run this command again." >&2
  exit 2
fi

exec node "$ROOT/bin/install.mjs" "$@"

