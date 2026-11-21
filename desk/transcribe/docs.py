"""Transcribe — meeting notes that were not recorded here (2026-11-21).

  - Text out of files: Word / RTF / ODT through macOS `textutil`, PDF through the Reader's helper (desk/app/pdftext.swift),
    plain .txt / .md as they are. All on this Mac.
  - Google Docs: a link is read through the Drive API with `drive.readonly` only. The OAuth client is the one the mail
    cleanup already keeps (keychain tatsuki-desk-gmail, account client); the Drive refresh token is kept apart in
    tatsuki-desk-gdrive. Nothing is written to Drive.
  - Minutes: only when the owner presses "Make minutes", the text goes to the Claude API (claude-sonnet-5-5, the key is
    tatsuki-desk-anthropic) and comes back as summary / decisions / actions / next. Audio is never sent.

  python3 docs.py connect      connect Google Drive once (opens the browser)
  python3 docs.py status
"""
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.dirname(HERE)

TEXT_EXT = {'.txt', '.md', '.markdown', '.text'}
TEXTUTIL_EXT = {'.docx', '.doc', '.rtf', '.rtfd', '.odt', '.html', '.htm', '.webarchive'}
DOC_EXT = TEXT_EXT | TEXTUTIL_EXT | {'.pdf'}
MAX_CHARS = 400_000  # about a day of talking; longer is refused rather than cut

DRIVE_SERVICE = 'tatsuki-desk-gdrive'
GMAIL_SERVICE = 'tatsuki-desk-gmail'
SCOPES = 'https://www.googleapis.com/auth/drive.readonly'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
DRIVE = 'https://www.googleapis.com/drive/v3/files'
TOKEN = {'value': '', 'until': 0}
CONNECT = {'running': False, 'url': '', 'error': ''}
LOCK = threading.Lock()

ANTHROPIC = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2026-10-21'
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
MINUTES_MODEL = 'claude-sonnet-5-5'


class DocError(Exception):
    """Something the owner should read, in plain words."""


class NotConnected(DocError):
    pass


# ---------- text out of files ----------

def clean(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace(' ', ' ')
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract(path, name=None):
    """Returns the text of a document file. Raises DocError with a reason the owner can act on."""
    ext = os.path.splitext(name or path)[1].lower()
    if ext in TEXT_EXT:
        with open(path, 'rb') as f:
            raw = f.read()
        for enc in ('utf-8-sig', 'utf-16', 'cp932'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode('utf-8', 'replace')
    elif ext in TEXTUTIL_EXT:
        r = subprocess.run(['/usr/bin/textutil', '-convert', 'txt', '-stdout', path], capture_output=True, timeout=120)
        if r.returncode != 0:
            raise DocError("This file couldn't be read. Save it again as .docx or .pdf and try once more")
        text = r.stdout.decode('utf-8', 'replace')
    elif ext == '.pdf':
        sys.path.insert(0, DESK)
        import reader  # the same PDF helper the Reader uses
        try:
            text = reader.pdf_text(path).replace('\f', '\n\n')
        except ValueError as e:
            raise DocError(str(e)) from None
    else:
        raise DocError("This file type isn't supported. Use Word, PDF, text or Markdown")
    text = clean(text)
    if not text:
        raise DocError('No text was found. A scanned PDF has only pictures of pages, and reading those comes later')
    if len(text) > MAX_CHARS:
        raise DocError(f'This is longer than {MAX_CHARS:,} characters. Split it into parts first')
    return text


# ---------- Google Drive ----------

def _kc_get(service, account):
    try:
        out = subprocess.run(['security', 'find-generic-password', '-s', service, '-a', account, '-w'],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.stdout.strip() if out.returncode == 0 else ''


def _kc_set(service, account, value):
    # through `security -i` so the secret never shows in a process list
    line = 'add-generic-password -U -s "%s" -a "%s" -w "%s"\n' % (service, account, value.replace('\\', '\\\\').replace('"', '\\"'))
    out = subprocess.run(['security', '-i'], input=line, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        raise OSError('keychain')


def _client():
    raw = _kc_get(DRIVE_SERVICE, 'client') or _kc_get(GMAIL_SERVICE, 'client')
    return json.loads(raw) if raw else None


def drive_status():
    if os.environ.get('TRANSCRIBE_DIR', '').startswith('/tmp'):  # test servers never touch the keychain
        return {'connected': False, 'client': False, 'connecting': False, 'error': ''}
    return {'connected': bool(_kc_get(DRIVE_SERVICE, 'refresh')), 'client': bool(_client()),
            'connecting': CONNECT['running'], 'url': CONNECT['url'] if CONNECT['running'] else '', 'error': CONNECT['error']}


def _form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            raise NotConnected('Google Drive needs to be connected again') from None
        raise DocError(f'Google returned an error ({e.code})') from None
    except OSError:
        raise DocError("Couldn't reach Google. Check the internet connection") from None


def start_connect():
    """Opens Google's sign-in in the browser and waits (in the background) for the answer. Returns the URL too."""
    client = _client()
    if not client:
        raise DocError('The Google connection file is missing. Connect the mail cleanup once first (python3 desk/gmail_clean.py auth)')
    with LOCK:
        if CONNECT['running']:
            return CONNECT['url']
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        got = {}

        class Once(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if q.get('state', [''])[0] == state:
                    got['code'] = q.get('code', [''])[0]
                    got['error'] = q.get('error', [''])[0]
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write('<p>Google Drive is connected to Transcribe. You can close this window.</p>'.encode())

            def log_message(self, *a):
                pass  # the request line holds the code

        srv = http.server.HTTPServer(('127.0.0.1', 0), Once)
        redirect = f'http://127.0.0.1:{srv.server_port}'
        url = AUTH_URL + '?' + urllib.parse.urlencode({
            'client_id': client['id'], 'redirect_uri': redirect, 'response_type': 'code', 'scope': SCOPES,
            'access_type': 'offline', 'prompt': 'consent', 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256'})
        CONNECT.update(running=True, url=url, error='')

    def wait():
        try:
            srv.timeout = 5
            deadline = time.time() + 300
            while 'code' not in got and time.time() < deadline:
                srv.handle_request()
            if not got.get('code'):
                raise DocError(got.get('error') or 'The sign-in was not finished in 5 minutes')
            data = _form(TOKEN_URL, {'code': got['code'], 'client_id': client['id'], 'client_secret': client['secret'],
                                     'redirect_uri': redirect, 'grant_type': 'authorization_code', 'code_verifier': verifier})
            if not data.get('refresh_token'):
                raise DocError('Google sent no lasting permission. Please try again')
            _kc_set(DRIVE_SERVICE, 'refresh', data['refresh_token'])
            TOKEN.update(value=data.get('access_token', ''), until=time.time() + int(data.get('expires_in', 3600)) - 60)
        except (DocError, OSError) as e:
            CONNECT['error'] = str(e)
        finally:
            srv.server_close()
            CONNECT.update(running=False, url='')

    threading.Thread(target=wait, daemon=True).start()
    webbrowser.open(url)
    return url


def access_token():
    with LOCK:
        if TOKEN['value'] and time.time() < TOKEN['until']:
            return TOKEN['value']
    client, refresh = _client(), _kc_get(DRIVE_SERVICE, 'refresh')
    if not client or not refresh:
        raise NotConnected('Connect Google Drive first')
    data = _form(TOKEN_URL, {'client_id': client['id'], 'client_secret': client['secret'],
                             'refresh_token': refresh, 'grant_type': 'refresh_token'})
    with LOCK:
        TOKEN.update(value=data['access_token'], until=time.time() + int(data.get('expires_in', 3600)) - 60)
        return TOKEN['value']


def _drive(path, query=None, raw=False):
    url = f'{DRIVE}/{path}' + ('?' + urllib.parse.urlencode(query) if query else '')
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + access_token()})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            TOKEN.update(value='', until=0)
            raise NotConnected('Google Drive needs to be connected again') from None
        if e.code in (403, 404):
            raise DocError("This file can't be opened with your Google account. Check the link and who it is shared with") from None
        raise DocError(f'Google Drive returned an error ({e.code})') from None
    except OSError:
        raise DocError("Couldn't reach Google Drive. Check the internet connection") from None
    return body if raw else json.loads(body)


DOC_ID = re.compile(r'(?:/d/|[?&]id=)([A-Za-z0-9_-]{20,})')


def file_id(link):
    link = (link or '').strip()
    m = DOC_ID.search(link)
    if m:
        return m.group(1)
    if re.fullmatch(r'[A-Za-z0-9_-]{20,}', link):
        return link
    raise DocError("That doesn't look like a Google Docs or Drive link")


def read_drive(link):
    """Returns {'title', 'text', 'met', 'id'} for a Google Doc, or a Word / PDF / text file kept in Drive."""
    fid = file_id(link)
    info = _drive(fid, {'fields': 'id,name,mimeType,createdTime,modifiedTime', 'supportsAllDrives': 'true'})
    mime = info.get('mimeType', '')
    name = info.get('name') or 'Google Doc'
    if mime == 'application/vnd.google-apps.document':
        text = clean(_drive(f'{fid}/export', {'mimeType': 'text/plain'}, raw=True).decode('utf-8-sig', 'replace'))
        title = name
    elif mime.startswith('application/vnd.google-apps.'):
        raise DocError('Only Google Docs can be read. Sheets and Slides come later')
    else:
        ext = os.path.splitext(name)[1].lower()
        if ext not in DOC_EXT:
            raise DocError("This file type isn't supported. Use Word, PDF, text or Markdown")
        body = _drive(fid, {'alt': 'media', 'supportsAllDrives': 'true'}, raw=True)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, 'file' + ext)
            with open(p, 'wb') as f:
                f.write(body)
            text = extract(p, name)
        title = os.path.splitext(name)[0]
    if not text:
        raise DocError('This document is empty')
    if len(text) > MAX_CHARS:
        raise DocError(f'This is longer than {MAX_CHARS:,} characters. Split it into parts first')
    met = guess_date(name) or (info.get('createdTime') or '')[:10]
    return {'title': title.strip()[:200], 'text': text, 'met': met, 'id': fid}


DATE_PATTERNS = [
    (re.compile(r'(20\d\d)[-/._年](\d{1,2})[-/._月](\d{1,2})'), (1, 2, 3)),
    (re.compile(r'(\d{1,2})(\d{2})(20\d\d)'), (3, 2, 1)),  # 03042025 = 3 Apr 2025 (day, month, year)
]


def guess_date(name):
    """The meeting day from a title like 2026-06-23_Meeting or 2026/08/01. Empty if none."""
    for rx, (y, m, d) in DATE_PATTERNS:
        g = rx.search(name or '')
        if g:
            try:
                yy, mm, dd = int(g.group(y)), int(g.group(m)), int(g.group(d))
                time.strptime(f'{yy}-{mm}-{dd}', '%Y-%m-%d')
                return f'{yy:04d}-{mm:02d}-{dd:02d}'
            except ValueError:
                continue
    return ''


# ---------- minutes ----------

MINUTES_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['summary', 'decisions', 'actions', 'next'],
    'properties': {
        'summary': {'type': 'array', 'items': {'type': 'string'}},
        'decisions': {'type': 'array', 'items': {'type': 'string'}},
        'actions': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['what', 'who', 'due'],
            'properties': {'what': {'type': 'string'}, 'who': {'type': 'string'}, 'due': {'type': 'string'}}}},
        'next': {'type': 'array', 'items': {'type': 'string'}},
    },
}

MINUTES_SYSTEM = """You turn a meeting transcript or meeting notes into minutes for the owner, who took part in the meeting.
Write in the same language as the material (Japanese material gets Japanese minutes).
- summary: 3 to 7 short points: what the meeting was about and what was said that matters later.
- decisions: what was agreed or decided. Only what the material shows was decided; leave it empty if nothing was.
- actions: things someone has to do. what = the task as a short imperative; who = the person named in the material, "Me" if it is the
  owner (the speaker labeled Me, or the note-taker), or "" if unclear; due = the date or timing as written ("by Friday", "2026-12-01"), or "".
- next: open questions and topics for the next meeting.
Use only what is in the material. Do not invent names, numbers, dates or decisions. Keep each item to one sentence."""


def anthropic_key():
    v = os.environ.get('ANTHROPIC_API_KEY')
    if v:
        return v
    if os.environ.get('TRANSCRIBE_DIR', '').startswith('/tmp'):  # test servers never touch the keychain
        return None
    return _kc_get('tatsuki-desk-anthropic', 'api') or None


def make_minutes(title, text, met=''):
    """One request to Claude. Returns ({summary, decisions, actions, next}, usage). Raises DocError."""
    key = anthropic_key()
    if not key:
        raise DocError('No Anthropic API key is saved on this Mac (keychain tatsuki-desk-anthropic)')
    head = f'Title: {title}\n' + (f'Date: {met}\n' if met else '')
    body = {'model': MINUTES_MODEL, 'max_tokens': 16000, 'system': MINUTES_SYSTEM,
            'output_config': {'effort': 'medium', 'format': {'type': 'json_schema', 'schema': MINUTES_SCHEMA}},
            'fallbacks': 'default',
            'messages': [{'role': 'user', 'content': f'{head}\n<material>\n{text}\n</material>'}]}
    headers = {'x-api-key': key, 'anthropic-version': API_VERSION, 'anthropic-beta': FALLBACK_BETA,
               'content-type': 'application/json', 'user-agent': 'tatsuki-desk'}
    req = urllib.request.Request(ANTHROPIC, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise DocError('Anthropic did not accept the API key') from None
        if e.code in (429, 500, 502, 503, 504, 529):
            raise DocError(f'Anthropic is busy ({e.code}). Try again in a minute') from None
        detail = ''
        try:
            detail = json.load(e).get('error', {}).get('message', '')[:200]
        except (ValueError, OSError):
            pass
        raise DocError(f'Anthropic returned an error ({e.code}) {detail}'.strip()) from None
    except OSError:
        raise DocError("Couldn't reach Anthropic. Check the internet connection") from None
    if res.get('stop_reason') == 'refusal':
        raise DocError('Claude declined to summarize this one')
    if res.get('stop_reason') == 'max_tokens':
        raise DocError('The minutes came out too long. Split the material and try again')
    out = next((b.get('text') for b in res.get('content', []) if b.get('type') == 'text'), None)
    try:
        minutes = json.loads(out)
    except (TypeError, ValueError):
        raise DocError("Claude's answer couldn't be read. Try again") from None
    u = res.get('usage') or {}
    usage = {'model': res.get('model') or MINUTES_MODEL, 'input': u.get('input_tokens', 0), 'output': u.get('output_tokens', 0)}
    return normalize_minutes(minutes), usage


def normalize_minutes(m):
    """Only the known fields, as strings. Used for Claude's answer and for the owner's edits alike."""
    strs = lambda v: [str(x).strip()[:1000] for x in (v or []) if str(x).strip()][:60]
    acts = []
    for a in (m.get('actions') or [])[:60]:
        if isinstance(a, dict) and str(a.get('what', '')).strip():
            acts.append({'what': str(a['what']).strip()[:500], 'who': str(a.get('who') or '').strip()[:80],
                         'due': str(a.get('due') or '').strip()[:80], 'taskId': str(a.get('taskId') or '')[:40]})
    return {'summary': strs(m.get('summary')), 'decisions': strs(m.get('decisions')), 'actions': acts, 'next': strs(m.get('next'))}


def minutes_text(title, met, m):
    """Plain text for Copy minutes."""
    lines = [title + (f' ({met})' if met else ''), '']
    for head, key in (('Summary', 'summary'), ('Decisions', 'decisions')):
        if m.get(key):
            lines += [head] + [f'- {x}' for x in m[key]] + ['']
    if m.get('actions'):
        lines.append('Actions')
        for a in m['actions']:
            extra = ', '.join(x for x in (a.get('who'), a.get('due')) if x)
            lines.append(f'- {a["what"]}' + (f' ({extra})' if extra else ''))
        lines.append('')
    if m.get('next'):
        lines += ['Next'] + [f'- {x}' for x in m['next']]
    return '\n'.join(lines).strip() + '\n'


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'status':
        print(json.dumps(drive_status(), ensure_ascii=False))
    elif cmd == 'connect':
        print('Opening Google sign-in…\n' + start_connect(), flush=True)
        while CONNECT['running']:
            time.sleep(1)
        print('Error: ' + CONNECT['error'] if CONNECT['error'] else 'Connected.')
    else:
        print(__doc__)
