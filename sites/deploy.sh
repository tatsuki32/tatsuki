#!/bin/bash
# Publish a personal site to <name>.tany.im (Cloudflare Worker with static assets).
# Usage: bash sites/deploy.sh tatsuki
set -euo pipefail
cd "$(dirname "$0")"
name="${1:?usage: deploy.sh <name>}"
src=$(python3 -c "import json,sys;print(json.load(open('sites.json'))[sys.argv[1]]['src'])" "$name")
[ -d "$name" ] || { echo "no sites/$name/wrangler.toml"; exit 1; }
rm -rf "$name/build"; mkdir -p "$name/build"
rsync -a --exclude 'README.md' --exclude '.*' "$src"/ "$name/build"/
WR="$PWD/../sift/node_modules/.bin/wrangler"; [ -x "$WR" ] || WR="npx wrangler"
(cd "$name" && $WR deploy)
rm -rf "$name/build"
