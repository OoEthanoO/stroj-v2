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
#
# The escape hatch is opt-in and noisy on purpose: set STROJ_FRONTEND_ONLY=1 to
# ship the static half before the judge exists, so domain/DNS/TLS can be
# validated in parallel with provisioning the backend. The site will load and
# report that no judge is connected.
if grep -q 'JUDGE_ORIGIN' vercel.json; then
  if [ "${STROJ_FRONTEND_ONLY:-}" = "1" ]; then
    echo "warning: JUDGE_ORIGIN is still a placeholder and STROJ_FRONTEND_ONLY=1." >&2
    echo "         Shipping the frontend WITHOUT a working judge: every /api/*" >&2
    echo "         request will fail until you set a real origin and redeploy." >&2
  else
    echo "error: vercel.json still contains the JUDGE_ORIGIN placeholder." >&2
    echo "       Replace it with your judge backend's origin, e.g." >&2
    echo "       https://judge.ethanyanxu.com — see DEPLOY.md." >&2
    echo "" >&2
    echo "       To ship the static half first and validate the domain, set" >&2
    echo "       STROJ_FRONTEND_ONLY=1 in the Vercel project's env vars." >&2
    exit 1
  fi
fi

echo "built public/ ($(find public -type f | wc -l | tr -d ' ') files)"
