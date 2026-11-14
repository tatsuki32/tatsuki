#!/bin/bash
# Sift Daily: put the three secrets in place, once. Run on the Mac:  bash sift/setup-secrets.sh
# - SIFT_KEY: a new random passphrase. Saved in the keychain (tatsuki-desk-sift) for the Desk's task pickup,
#   and copied to the clipboard so you can sign in at https://sift.tany.im/login and save it in your password manager.
# - ANTHROPIC_KEY: copied from the keychain item the Desk already uses (tatsuki-desk-anthropic).
# - MAIL_TO: the inbox the daily email goes to. It must be verified in Cloudflare Email Routing for tany.im.
# Nothing is printed or written to a file.
set -euo pipefail
cd "$(dirname "$0")"
export WRANGLER_SEND_METRICS=false

key="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)"
security add-generic-password -U -s tatsuki-desk-sift -a sift -w "$key"
printf '%s' "$key" | npx wrangler secret put SIFT_KEY >/dev/null
printf '%s' "$key" | pbcopy
echo "SIFT_KEY set. The passphrase is on your clipboard and in the keychain (tatsuki-desk-sift)."

if ak="$(security find-generic-password -s tatsuki-desk-anthropic -w 2>/dev/null)"; then
  printf '%s' "$ak" | npx wrangler secret put ANTHROPIC_KEY >/dev/null
  echo "ANTHROPIC_KEY set from the keychain."
else
  echo "No tatsuki-desk-anthropic in the keychain. Run: npx wrangler secret put ANTHROPIC_KEY"
fi

read -r -p "Email address for the daily email (your Gmail): " to
if [ -n "$to" ]; then
  printf '%s' "$to" | npx wrangler secret put MAIL_TO >/dev/null
  echo "MAIL_TO set."
fi
echo "Done. Sign in at https://sift.tany.im/login with the passphrase on your clipboard."
