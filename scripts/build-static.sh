#!/usr/bin/env bash
# Assemble the static frontend for Vercel.
#
# stroj/web/ stays the single source of truth — it is what the judge itself
# serves in a single-host deployment — so this just lays it out the way Vercel
# expects rather than duplicating it in the repo.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf public
mkdir -p public/static

cp stroj/web/index.html public/index.html
cp stroj/web/style.css stroj/web/app.js public/static/

# Fail loudly rather than deploying a frontend that points at a placeholder.
if grep -q 'JUDGE_ORIGIN' vercel.json; then
  echo "error: vercel.json still contains the JUDGE_ORIGIN placeholder." >&2
  echo "       Replace it with your judge backend's origin, e.g." >&2
  echo "       https://stroj-judge.fly.dev — see DEPLOY.md." >&2
  exit 1
fi

echo "built public/ ($(find public -type f | wc -l | tr -d ' ') files)"
