#!/usr/bin/env bash
#
# Is what's deployed what's on main?
#
#   ./scripts/check-versions.sh
#   ./scripts/check-versions.sh https://stroj.ethanyanxu.com
#
# The frontend and backend deploy independently — Vercel on push, the judge on
# its own timer — so either can lag. Exits non-zero if either is behind, which
# makes it usable as a pre-contest check.
set -uo pipefail

SITE="${1:-https://stroj.ethanyanxu.com}"
cd "$(dirname "$0")/.."

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
row()  { printf '  %-10s %-10s %s\n' "$1" "$2" "$3"; }

# --- what main actually is -------------------------------------------------
if ! git fetch -q origin main 2>/dev/null; then
    echo "warning: could not reach the git remote; comparing against local refs" >&2
fi
latest="$(git rev-parse origin/main 2>/dev/null || echo unknown)"

# --- what is deployed ------------------------------------------------------
backend="$(curl -fsS --max-time 20 "$SITE/api/version" 2>/dev/null \
    | sed -n 's/.*"commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
frontend="$(curl -fsS --max-time 20 "$SITE/version.json" 2>/dev/null \
    | sed -n 's/.*"commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
if [ -z "$frontend" ]; then
    # No version.json — either an older deploy or the judge serving the pages
    # itself. The meta tag is in the HTML either way.
    frontend="$(curl -fsS --max-time 20 "$SITE/" 2>/dev/null \
        | sed -n 's/.*name="stroj-commit"[[:space:]]*content="\([^"]*\)".*/\1/p')"
    case "$frontend" in __*) frontend="unstamped" ;; esac
fi

# A site that is up but has no version endpoint is a deploy from before this
# existed — quite different from a site that is down, so do not conflate them.
if curl -fsS --max-time 20 -o /dev/null "$SITE/" 2>/dev/null; then
    : "${backend:=unstamped-deploy}"
    : "${frontend:=unstamped-deploy}"
else
    : "${backend:=unreachable}"
    : "${frontend:=unreachable}"
fi

verdict() {
    case "$1" in
        unreachable)      echo "could not reach it" ;;
        unstamped-deploy) echo "deployed before version reporting existed" ;;
        unstamped)        echo "served straight from a checkout, not a build" ;;
        unknown)          echo "reports no commit (built without a stamp)" ;;
        "$latest")        echo "up to date" ;;
        *)                echo "BEHIND — main is ${latest:0:7}" ;;
    esac
}

bold "$SITE"
row "main"     "${latest:0:7}"   ""
row "backend"  "${backend:0:7}"  "$(verdict "$backend")"
row "frontend" "${frontend:0:7}" "$(verdict "$frontend")"

# Uncommitted work is the other reason "deployed" and "what I wrote" differ.
if ! git diff --quiet 2>/dev/null || [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo
    echo "  note: you have uncommitted changes, so main is not what you have locally"
fi

stale=0
[ "$backend"  = "$latest" ] || stale=1
[ "$frontend" = "$latest" ] || stale=1
exit "$stale"
