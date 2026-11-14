#!/bin/bash
# Tatsuki's Desk — run by launchd (com.tatsuki.desk.pull) every 5 minutes.
# Pulls main so GitHub changes reach this Mac, and restarts the Desk server when desk/server.py or a module it imports (desk/*.py) changed
# (index.html is read from disk on every request, but server.py is loaded once at start).
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${DESK_BRANCH:-main}"
cd "$REPO" || exit 1
if [ "$(git rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
  echo "$(date '+%F %T') skip: not on $BRANCH (git checkout $BRANCH で再開)"; exit 0
fi
BEFORE="$(git rev-parse HEAD)"
git pull --ff-only -q origin "$BRANCH"; RC=$?
AFTER="$(git rev-parse HEAD)"
if [ "$RC" -ne 0 ]; then
  echo "$(date '+%F %T') pull failed ($RC): ローカルの変更か認証が原因。cd $REPO && git status / git pull origin $BRANCH で確かめる"
fi
if [ "$BEFORE" != "$AFTER" ] && ! git diff --quiet "$BEFORE" "$AFTER" -- desk/server.py 'desk/*.py'; then
  launchctl kickstart -k "gui/$(id -u)/com.tatsuki.desk.server" && echo "$(date '+%F %T') server restarted (server.py changed)"
fi
echo "$(date '+%F %T') pull exit $RC $AFTER"
