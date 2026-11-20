#!/bin/bash
# Sets up the Cards app on Cloudflare Pages and the passphrase that ties it to this Mac. Run once, on the Mac, signed in to
# wrangler (npx wrangler login). Usage: bash cards/setup.sh
# What it does: makes the KV namespace, writes its id into wrangler.toml, puts the same passphrase into the keychain
# (tatsuki-desk-cards, read by desk/cards_sync.py) and into the Pages secret SYNC_KEY, builds and deploys.
set -euo pipefail
cd "$(dirname "$0")"

if grep -q REPLACE_WITH_KV_ID wrangler.toml; then
  echo "Making the KV namespace…"
  OUT="$(npx wrangler kv namespace create CARDS 2>&1)"
  ID="$(printf '%s' "$OUT" | grep -oE 'id = "[0-9a-f]+"' | head -1 | grep -oE '[0-9a-f]{32}')"
  [ -n "$ID" ] || { echo "Could not read the namespace id from wrangler:"; echo "$OUT"; exit 1; }
  sed -i '' "s/REPLACE_WITH_KV_ID/$ID/" wrangler.toml
fi

if ! /usr/bin/security find-generic-password -s tatsuki-desk-cards -w >/dev/null 2>&1; then
  KEY="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)"
  /usr/bin/security add-generic-password -s tatsuki-desk-cards -a desk -w "$KEY" -U
  echo "A new passphrase is in the keychain (tatsuki-desk-cards)."
fi
KEY="$(/usr/bin/security find-generic-password -s tatsuki-desk-cards -w)"

python3 build.py
npx wrangler pages project create tatsuki-cards --production-branch main >/dev/null 2>&1 || true
printf '%s' "$KEY" | npx wrangler pages secret put SYNC_KEY --project-name tatsuki-cards
npx wrangler pages deploy dist --project-name tatsuki-cards --branch main
echo "Done. On the Desk: Cards → Use on iPhone gives the one-time link for the phone."
