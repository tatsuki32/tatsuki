#!/bin/bash
# A Desk (4779) and a recorder (8778) to try auto-recording with: a fake microphone app and throw-away data.
# It starts and ends with the file /tmp/claude/desk-autorec-test/who.json (see the end of this script).
set -e
cd "$(dirname "$0")/.."
T=/tmp/claude/desk-autorec-test; rm -rf "$T"; mkdir -p "$T/data/docs/calendar" "$T/data/transcribe"
python3 - "$T" <<'PY'
import datetime, json, re, sys
t = sys.argv[1]
body = re.search(r"FAKE_APP = r'''(.*?)'''", open('tests/test_auto_record.py').read(), re.S).group(1)
open(t + '/app.py', 'w').write(body)
iso = lambda m: (datetime.datetime.now().astimezone() + datetime.timedelta(minutes=m)).isoformat(timespec='seconds')
json.dump({'events': [{'id': 'test_1', 'title': 'Test meeting', 'start': iso(-2), 'end': iso(60), 'allDay': False, 'meet': True, 'mine': True, 'location': '', 'url': ''}]},
          open(t + '/data/docs/calendar/upcoming.json', 'w'))
json.dump({'ok': True, 'locked': False, 'users': []}, open(t + '/who.json', 'w'))
PY
export DESK_DATA_DIR="$T/data" TRANSCRIBE_DIR="$T/data/transcribe" TRANSCRIBE_PORT=8778 TRANSCRIBE_WATCH_CMD="python3 $T/app.py" \
       TRANSCRIBE_TAP_CMD=/usr/bin/true TRANSCRIBE_CALENDAR="$T/data/docs/calendar/upcoming.json" TRANSCRIBE_NO_NOTIFY=1 TRANSCRIBE_AUTO=1
PORT=8778 python3 transcribe/server.py &
trap 'kill %1' EXIT
DESK_PORT=4779 python3 server.py
# a call begins: echo '{"ok":true,"locked":false,"users":[{"bundle":"us.zoom.xos","name":"zoom.us"}]}' > /tmp/claude/desk-autorec-test/who.json
