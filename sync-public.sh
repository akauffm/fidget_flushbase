#!/usr/bin/env bash
#
# Copies the source files into public/, which is what Firebase Hosting actually
# serves. Wired into firebase.json as a hosting predeploy hook, so a deploy can
# no longer ship a stale copy of a page you just edited.
#
# Run it by hand any time with:  ./sync-public.sh
#
set -euo pipefail
cd "$(dirname "$0")"

# "<source file>:<name it takes inside public/>"
REQUIRED=(
    "flushtracker.html:index.html"
    "dashboard.html:dashboard.html"
    "firebase_config.js:firebase_config.js"
    "toilet.png:toilet.png"
)

# Files that may not exist on every branch; copied when present.
OPTIONAL=(
    "flush_model.js:flush_model.js"
)

mkdir -p public

copy_pair() {
    local src="${1%%:*}" dest="${1##*:}"
    cp "$src" "public/$dest"
    echo "  $src -> public/$dest"
}

echo "sync-public: refreshing public/ from source"

for pair in "${REQUIRED[@]}"; do
    src="${pair%%:*}"
    if [ ! -f "$src" ]; then
        echo "sync-public: ERROR - required source file '$src' is missing" >&2
        exit 1
    fi
    copy_pair "$pair"
done

for pair in "${OPTIONAL[@]}"; do
    src="${pair%%:*}"
    if [ -f "$src" ]; then
        copy_pair "$pair"
    fi
done

echo "sync-public: done"
