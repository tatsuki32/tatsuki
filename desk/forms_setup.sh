#!/bin/bash
# Connects Tatsuki's Desk to the forms on tany.im, once.
# 1. A passphrase goes into the Mac keychain (tatsuki-desk-forms) and into the Worker (secret FORMS_KEY).
# 2. The site is deployed so /f/<slug> and /_forms/ exist (the D1 tables are made on the first request).
# Optional: Cloudflare Turnstile (the "are you human" check). Make a widget for tany.im at
# https://dash.cloudflare.com -> Turnstile, then put its site key in wrangler.jsonc ("TURNSTILE_SITE") and run
#   npx wrangler secret put TURNSTILE_SECRET
# in the site folder. Without it, forms still work (honeypot and 3 posts a minute per address).
set -e
SITE="$(cd "$(dirname "$0")" && pwd)/data/tany/site"
[ -f "$SITE/forms.js" ] || { echo "The site folder is not on this Mac: $SITE"; exit 1; }
if security find-generic-password -s tatsuki-desk-forms -w >/dev/null 2>&1; then
  KEY=$(security find-generic-password -s tatsuki-desk-forms -w)
  echo "Keychain already has tatsuki-desk-forms; the Worker gets the same value."
else
  KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
  security add-generic-password -s tatsuki-desk-forms -a desk -w "$KEY" -U
  echo "Passphrase saved in the keychain as tatsuki-desk-forms."
fi
cd "$SITE"
printf '%s' "$KEY" | npx --yes wrangler secret put FORMS_KEY
npx --yes wrangler deploy
echo "Done. Restart the Desk server (or wait for the next pull) and open Forms."
