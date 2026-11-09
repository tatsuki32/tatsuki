#!/bin/bash
# Sets up the bot's Cloudflare side. Run it in your own Terminal: keys are typed at hidden prompts
# and go straight to Cloudflare. They are never written to a file here.
set -euo pipefail
cd "$(dirname "$0")/.."

npm install
npx wrangler login

grep -q '^\[\[kv_namespaces\]\]' wrangler.toml || npx wrangler kv namespace create assistant --binding KV --update-config
npx wrangler queues create assistant-messages || echo "（キューはすでにあります）"

echo "BotFather の新しいボットのトークン："
npx wrangler secret put TELEGRAM_TOKEN
echo "Claude API キー："
npx wrangler secret put ANTHROPIC_API_KEY

# In a private chat the chat id is the person's id, so the existing bot's setting can be reused.
CHAT=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/TatsukiDesk/desk/data/lifeos/secrets/telegram.json')))['chat_id'])")
printf '%s' "$CHAT" | npx wrangler secret put OWNER_CHAT_ID

SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
printf '%s' "$SECRET" | npx wrangler secret put WEBHOOK_SECRET

npx wrangler deploy | tee /dev/stderr | grep -o 'https://[^ ]*workers.dev' | head -1 > .worker-url
URL=$(cat .worker-url); rm -f .worker-url

read -r -s -p "もう一度、ボットのトークン（Telegram に受け口を登録します）：" TOKEN; echo
curl -fsS "https://api.telegram.org/bot${TOKEN}/setWebhook" \
  --data-urlencode "url=${URL}" \
  --data-urlencode "secret_token=${SECRET}" \
  --data-urlencode 'allowed_updates=["message"]' >/dev/null
unset TOKEN SECRET
echo "できました。新しいボットに「使い方」と送ってください。"
