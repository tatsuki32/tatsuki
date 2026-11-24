"""Auto-record: when a meeting on the calendar begins and a call is really using the microphone, Transcribe records it by itself.

- Off until it is switched on (desk/data/transcribe/auto.json). It only runs on the real recorder (8791), or in tests.
- Calendar: desk/data/docs/calendar/upcoming.json, which the Desk already reads every 5 minutes. An event counts when it has a
  meeting link (`meet`), is not all day and was not declined. From 5 minutes before it starts until 30 minutes after it ends.
- Microphone: `TatsukiMeetWatch who` says which programs are listening. Only browsers and meeting apps count, never dictation,
  Fathom, or this recorder. 10 seconds of that starts a recording (mode `meeting`: mic = Me, the Mac's sound = Them).
- It ends when no call has used the microphone for 2 minutes, after 4 hours, or with Stop. Discard stops and erases it.
  After Stop or Discard the same event does not start again.
- People in the call cannot see this recording (there is no bot), so every start shows a Mac notification that says to tell them.
- Nothing new leaves this Mac. The notification holds the event title only.
"""
import datetime
import json
import os
import shlex
import shutil
import subprocess
import threading
import time

BEFORE, AFTER = 5 * 60, 30 * 60
HOLD = 10            # seconds a call must use the mic before a recording starts
QUIET = 120          # seconds without it before the recording ends
MAX = 4 * 3600
MIN_FREE = 5 * 1024 ** 3
TICK = 5
KEEP = 7 * 86400     # how long a stopped or discarded event is remembered
# Chrome's and Edge's sound runs in a helper; Safari's in com.apple.WebKit.GPU. Prefixes, so helpers and web apps count too.
CALLS = ('com.google.Chrome', 'com.brave.Browser', 'com.apple.Safari', 'com.apple.WebKit', 'com.microsoft.edgemac', 'company.thebrowser.',
         'org.mozilla.firefox', 'us.zoom.', 'com.microsoft.teams', 'com.apple.FaceTime', 'com.apple.avconferenced', 'Cisco-Systems.Spark')
APP = os.environ.get('TRANSCRIBE_WATCH_APP') or os.path.expanduser('~/Applications/TatsukiMeetWatch.app')
BIN = os.path.join(APP, 'Contents', 'MacOS', 'TatsukiMeetWatch')
CMD = os.environ.get('TRANSCRIBE_WATCH_CMD')          # tests: a stand-in for the app
TAP_READY = os.environ.get('TRANSCRIBE_TAP_READY')    # tests: '1'
LANGS = ('auto', 'ja', 'en', 'vi', 'zh', 'ko')

S = None             # desk/transcribe/server.py hands itself over in attach()
lock = threading.RLock()
# what the last look saw; nothing here is kept on disk
now_state = {'since': None, 'app': '', 'event': None, 'last_mic': 0.0, 'problem': '', 'at': 0.0, 'next': None}


def attach(server):
    global S
    S = server


# ---------- settings ----------

def path():
    return os.path.join(S.DATA, 'auto.json')


def load():
    d = S.read_json(path(), None) or {}
    return {'on': bool(d.get('on')), 'language': d.get('language') if d.get('language') in LANGS else 'auto',
            'skip': d.get('skip') if isinstance(d.get('skip'), dict) else {}}


def save(**fields):
    with lock:
        d = {**load(), **fields}
        d['skip'] = {k: t for k, t in d['skip'].items() if time.time() - t < KEEP}
        S.write_json(path(), d)
        return d


def skip_event(eid):
    if eid:
        save(skip={**load()['skip'], eid: time.time()})


# ---------- the calendar ----------

def calendar_file():
    return os.environ.get('TRANSCRIBE_CALENDAR') or os.path.join(os.path.dirname(S.DATA), 'docs', 'calendar', 'upcoming.json')


def ts(v):
    try:
        d = datetime.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        return d.timestamp() if d.tzinfo else d.astimezone().timestamp()
    except ValueError:
        return None


def meetings():
    """Events on the calendar that are calls: [{id, title, start, end (epoch), raw}], in order."""
    out = []
    for e in (S.read_json(calendar_file(), None) or {}).get('events') or []:
        if not isinstance(e, dict) or e.get('allDay') or e.get('declined') or not e.get('meet'):
            continue
        a, b = ts(e.get('start')), ts(e.get('end'))
        if a is None or not e.get('id'):
            continue
        out.append({'id': str(e['id']), 'title': str(e.get('title') or 'Meeting')[:200], 'start': a, 'end': max(b or a, a), 'raw': e})
    return sorted(out, key=lambda m: m['start'])


def pick(now, found=None):
    """The meeting this moment belongs to: one going on now first, else the nearest start."""
    near = [m for m in (meetings() if found is None else found) if m['start'] - BEFORE <= now <= m['end'] + AFTER]
    if not near:
        return None
    # two in a row: 5 minutes before the next one, a call still running belongs to the one going on
    return min(near, key=lambda m: (not m['start'] <= now < m['end'], abs(m['start'] - now)))


# ---------- the app ----------

def who():
    """(bundle or name of a call using the microphone now, screen locked). None when the Mac will not say."""
    try:
        r = subprocess.run((shlex.split(CMD) if CMD else [BIN]) + ['who'], capture_output=True, text=True, timeout=10)
        d = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not d.get('ok'):
        return None
    for u in d.get('users') or []:
        if str(u.get('bundle') or '').startswith(CALLS):
            return (u.get('name') or u['bundle'], bool(d.get('locked')))
    return ('', bool(d.get('locked')))


def app_ready():
    return bool(CMD) or os.path.exists(BIN)


def run_app(*args, wait=False):
    if CMD:
        cmd = shlex.split(CMD) + list(args)
        if wait:
            subprocess.run(cmd, capture_output=True, timeout=15)
        else:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    else:  # through `open`, so macOS asks for the microphone for this app alone, not for Python
        subprocess.run(['/usr/bin/open', '-n', '-g', *(['-W'] if wait else []), '-a', APP, '--args', *args], capture_output=True, timeout=20)


def permissions(ask=False):
    """What macOS has allowed. `setup` shows its prompt once; the answer is read on the next look."""
    if not app_ready():
        return {'app': False, 'microphone': 'missing', 'tap': os.path.exists(S.TAP_APP)}
    try:
        run_app('setup' if ask else 'status', S.DATA, wait=not ask)
    except (OSError, subprocess.SubprocessError):
        pass
    return {'app': True, 'microphone': (S.read_json(os.path.join(S.DATA, 'mic-permission.json'), None) or {}).get('microphone', 'ask'),
            'tap': os.path.exists(S.TAP_APP) or bool(S.TAP_CMD)}


# ---------- one recording ----------

def start_mic(sid):
    folder = S.sdir(sid)
    S.touch(os.path.join(folder, 'mic-alive'))
    try:
        os.remove(os.path.join(folder, 'mic-stop'))
    except FileNotFoundError:
        pass
    S.update_meta(sid, mic_started_at=time.time())
    try:
        run_app('record', folder)
    except (OSError, subprocess.SubprocessError) as e:
        print(f'[{sid[:8]}] the microphone app did not start: {type(e).__name__}', flush=True)


def mic_state(sid):
    m = S.read_json(os.path.join(S.sdir(sid), 'mic.json'), None)
    if m is None:
        return {'state': 'starting'}
    if m.get('state') in ('starting', 'recording') and time.time() - m.get('updated', 0) > 5:
        return {**m, 'state': 'lost'}
    return m


def stop_mic(sid):
    folder = S.sdir(sid)
    if not os.path.isdir(folder):
        return
    S.touch(os.path.join(folder, 'mic-stop'))
    deadline = time.time() + 5
    while time.time() < deadline and mic_state(sid).get('state') in ('starting', 'recording'):
        time.sleep(0.2)


def active():
    """The auto-recording going on, or None. Raw status: a restart of this server must not lose it."""
    for m in S.list_sessions():
        raw = S.load_meta(m['id'])
        if raw and raw.get('auto') and raw.get('status') == 'recording':
            return raw
    return None


def other_recording():
    return any(m['status'] == 'recording' and not m.get('auto') for m in S.list_sessions())


def notify(title, text):
    if os.environ.get('TRANSCRIBE_NO_NOTIFY'):
        return
    script = ['on run argv', 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"', 'end run']
    cmd = ['/usr/bin/osascript']
    for line in script:
        cmd += ['-e', line]
    try:  # "--": an event title that starts with "-e" must not be run as a script
        subprocess.Popen(cmd + ['--', title, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def begin(event, app):
    meta = S.create_session(event['title'], load()['language'], 'recording', 'recording', 'meeting')
    sid = meta['id']
    raw = event['raw']
    S.update_meta(sid, auto=True, auto_app=app,
                  event={'id': event['id'], 'title': event['title'], 'start': str(raw.get('start') or ''), 'end': str(raw.get('end') or '')})
    start_mic(sid)
    now_state.update(last_mic=time.time(), sizes=None, problem='')
    print(f'[{sid[:8]}] auto-recording started', flush=True)
    notify('Recording started. Tell the others.', event['title'])
    return sid


def end(sid, why):
    """Stop and turn it into text."""
    stop_mic(sid)
    S.stop_tap(sid, wait=False)
    S.update_meta(sid, auto_end=why)
    keep_clock(sid)
    S.queue_transcription(sid)
    now_state.update(since=None)
    print(f'[{sid[:8]}] auto-recording ended ({why})', flush=True)


def discard(sid):
    meta = S.load_meta(sid)
    if not meta or not meta.get('auto'):
        return False
    skip_event((meta.get('event') or {}).get('id'))
    S.touch(os.path.join(S.sdir(sid), 'mic-stop'))
    S.stop_tap(sid, wait=False)
    stop_mic(sid)
    with S.lock:
        S.update_meta(sid, status='deleted')
        shutil.rmtree(S.sdir(sid), ignore_errors=True)
    now_state.update(since=None)
    print(f'[{sid[:8]}] auto-recording discarded', flush=True)
    return True


def keep_clock(sid):
    """The wall-clock time of the mic's first sample, so the two sides line up."""
    meta, mic = S.load_meta(sid), S.read_json(os.path.join(S.sdir(sid), 'mic.json'), None) or {}
    if meta and not meta.get('mic_start') and mic.get('started'):
        S.update_meta(sid, mic_start=mic['started'])


def tend(meta, now):
    """A recording going on: keep both apps alive, notice trouble, and end it when the call is over."""
    sid = meta['id']
    folder = S.sdir(sid)
    S.touch(os.path.join(folder, 'mic-alive'))
    S.touch(os.path.join(folder, 'alive'))
    keep_clock(sid)
    sizes = (S.file_size(S.pcm_path(sid)), S.file_size(S.track_pcm(sid, 'them')))
    if sizes != now_state.get('sizes') and any(sizes):
        now_state['sizes'] = sizes
        S.update_meta(sid, last_chunk=now)
    mic, tap = mic_state(sid), S.tap_state(sid)
    age = now - meta['created']
    if mic['state'] == 'lost' and now - meta.get('mic_started_at', 0) > 30:
        print(f'[{sid[:8]}] the microphone app stopped; starting it again', flush=True)
        start_mic(sid)
    mic_bad, tap_bad = mic['state'] == 'error', tap.get('state') == 'error'
    now_state['problem'] = ('microphone' if mic.get('error') == 'permission' else 'mic') if mic_bad else \
        ('screen' if tap.get('error') == 'permission' else 'tap') if tap_bad else ''
    if not any(sizes) and (mic_bad and tap_bad or age > 60):  # nothing at all is being recorded
        discard(sid)
        notify('This meeting is not being recorded', 'Allow the microphone and screen recording: Desk › Transcribe')
        return
    if age > 25 and now_state['problem'] and not meta.get('told_problem'):
        S.update_meta(sid, told_problem=True)
        notify('Only one side is being recorded', 'Your voice is missing. Desk › Transcribe' if mic_bad else 'Their voice is missing. Desk › Transcribe')
    heard = who()
    if heard is None or heard[0]:  # when the Mac will not say, a call is never cut off
        now_state['last_mic'] = now
    if age > MAX:
        skip_event((meta.get('event') or {}).get('id'))
        return end(sid, 'four hours')
    if now - max(now_state['last_mic'], meta['created']) >= QUIET:
        return end(sid, 'call over')


def look(now=None):
    """One look, every 5 seconds. Returns what it did, for the tests."""
    now = now or time.time()
    with lock:
        now_state['at'] = now
        going = active()
        if going:
            if now - now_state['last_mic'] > MAX:
                now_state['last_mic'] = now  # this server was just restarted: give the call its two minutes again
            tend(going, now)
            return 'recording'
        conf = load()
        found = meetings() if conf['on'] else []
        now_state['next'] = next(({'title': m['title'], 'start': m['start']} for m in found
                                  if m['end'] > now and m['id'] not in conf['skip']), None)
        now_state['problem'] = ''
        event = pick(now, [m for m in found if m['id'] not in conf['skip']])
        if not conf['on'] or not event or not app_ready():
            now_state.update(since=None, event=None)
            return 'idle'
        now_state['event'] = event['title']
        heard = who()
        if not heard or not heard[0] or heard[1] or other_recording():
            now_state.update(since=None)
            return 'waiting'
        if now_state['since'] is None:
            now_state.update(since=now, app=heard[0])
        if now - now_state['since'] < HOLD:
            return 'hearing'
        now_state['since'] = None
        if shutil.disk_usage(S.DATA).free < MIN_FREE:
            skip_event(event['id'])
            notify('This meeting is not being recorded', 'Less than 5 GB is free on this Mac')
            return 'full'
        begin(event, heard[0])
        return 'started'


def watch():
    while True:
        try:
            look()
        except Exception as e:  # one bad look must not end the watch; the message names no content
            print(f'auto-record: {type(e).__name__}: {e}', flush=True)
        time.sleep(TICK)


# ---------- for the Desk ----------

def view(check=False):
    conf = load()
    going = active()
    out = {'on': conf['on'], 'language': conf['language'], 'ready': app_ready(), 'next': now_state['next'],
           'watching': time.time() - now_state['at'] < 30, 'recording': None, 'problem': now_state['problem']}
    if going:
        out['recording'] = {'id': going['id'], 'title': going['title'], 'created': going['created'], 'app': going.get('auto_app', '')}
    if check:
        out['permissions'] = permissions()
    return out


def act(data):
    """(status code, answer) for POST /api/auto."""
    op = data.get('op')
    if op == 'set':
        fields = {}
        if 'on' in data:
            fields['on'] = bool(data['on'])
        if data.get('language') in LANGS:
            fields['language'] = data['language']
        save(**fields)
        if not fields.get('on', True):
            now_state.update(next=None, event=None, since=None)
        return 200, view()
    if op == 'setup':
        return 200, {**view(), 'permissions': permissions(ask=True)}
    if op == 'settings':  # System Settings, on the page that was asked for; nothing is changed there
        pane = {'microphone': 'Privacy_Microphone', 'screen': 'Privacy_ScreenCapture'}.get(data.get('pane'))
        if not pane:
            return 400, {'error': 'Unknown action'}
        subprocess.run(['/usr/bin/open', f'x-apple.systempreferences:com.apple.preference.security?{pane}'], capture_output=True, timeout=15)
        return 200, view()
    if op in ('stop', 'discard'):
        with lock:
            going = active()
            if not going or (data.get('id') and data['id'] != going['id']):
                return 409, {'error': 'That recording has already ended'}
            if op == 'discard':
                discard(going['id'])
            else:
                skip_event((going.get('event') or {}).get('id'))
                end(going['id'], 'stopped')
        return 200, view()
    return 400, {'error': 'Unknown action'}
