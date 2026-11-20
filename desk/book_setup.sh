#!/bin/sh
# Bookings (tany.im/book): the two secrets the Worker needs, set once. Run from the repository root:
#   bash desk/book_setup.sh
# - BOOK_KEY: the passphrase Desk uses to talk to the Worker. Kept in the keychain as tatsuki-desk-book and in the Worker.
# - TOKEN_KEY: 32 random bytes (base64) that seal the Google Calendar permission inside D1. Only in the Worker.
# RESEND_KEY (the newsletter's) and the BOOK_FROM var in wrangler.jsonc are what the confirmation emails go out with.
# Nothing is deployed here: `cd desk/data/tany/site && npx wrangler deploy` puts the page up, after Tatsuki says so.
set -e
SITE="$(cd "$(dirname "$0")/.." && pwd)/desk/data/tany/site"
[ -f "$SITE/wrangler.jsonc" ] || { echo "The tany.im site files are not on this Mac ($SITE)"; exit 1; }
cd "$SITE"

if security find-generic-password -s tatsuki-desk-book -w >/dev/null 2>&1; then
  KEY="$(security find-generic-password -s tatsuki-desk-book -w)"
  echo "BOOK_KEY: using the passphrase already in the keychain (tatsuki-desk-book)."
else
  KEY="$(openssl rand -hex 24)"
  security add-generic-password -U -s tatsuki-desk-book -a desk -w "$KEY"
  echo "BOOK_KEY: a new passphrase is in the keychain (tatsuki-desk-book)."
fi
printf '%s' "$KEY" | npx --yes wrangler secret put BOOK_KEY
echo "BOOK_KEY is on tany.im."

if npx --yes wrangler secret list 2>/dev/null | grep -q '"TOKEN_KEY"'; then
  echo "TOKEN_KEY is already on tany.im (kept: changing it would lock the sealed Google permission)."
else
  openssl rand -base64 32 | tr -d '\n' | npx --yes wrangler secret put TOKEN_KEY
  echo "TOKEN_KEY is on tany.im."
fi

echo
echo "Next: deploy the site (npx wrangler deploy in $SITE), then in Desk > Bookings press Connect Google Calendar."
