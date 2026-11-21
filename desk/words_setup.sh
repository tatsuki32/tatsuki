#!/bin/bash
# One-time: the key that lets the Desk write the "<Language> words" pages on Rhizome Box (desk/words.py).
# It goes to the macOS keychain (tatsuki-desk-rhizomebox-words) and to the Worker as the secret WORDS_KEY.
# The key is never printed or written to a file. Run the Worker deploy first so it knows /_/api/desk/words.
set -euo pipefail
cd "$(dirname "$0")/../rhizomebox"
KEY=$(openssl rand -hex 32)
security add-generic-password -U -s tatsuki-desk-rhizomebox-words -a desk -w "$KEY"
printf %s "$KEY" | npx wrangler secret put WORDS_KEY
echo "Done. The Desk can now write the word pages. Open Words in the Desk, press Preview pages, then Start publishing."
