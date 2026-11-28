#!/bin/bash
# One-time: the key that lets the Desk publish reflections on Rhizome Box (desk/reflect.py).
# It goes to the macOS keychain (tatsuki-desk-rhizomebox-reflect) and to the Worker as the secret REFLECT_KEY.
# The key is never printed or written to a file. Deploy the Worker first so it knows /_/api/desk/reflection.
set -euo pipefail
cd "$(dirname "$0")/../rhizomebox"
KEY=$(openssl rand -hex 32)
security add-generic-password -U -s tatsuki-desk-rhizomebox-reflect -a desk -w "$KEY"
printf %s "$KEY" | npx wrangler secret put REFLECT_KEY
echo "Done. Reflections can now be published from the Desk."
