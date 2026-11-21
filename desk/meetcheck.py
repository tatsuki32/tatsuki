"""Should we meet? — a meeting has to earn its place.

The owner answers four questions (Claude drafts the answers from the title, the time and one line of note;
the owner keeps or changes them). Only the owner's answers decide: all four pass → meet, and a pre-read
Google Doc is made (Amazon style, anyone with the link can comment). Any one fails → don't meet: an
alternative, a reply draft for an invite (copied only, never sent) and the time and yen saved.

The records live in the Desk collection `meetchecks` (the page writes them). This module only:
  - checks answers (`verdict`, the same rules as the page's mcVerdict),
  - asks Claude Haiku for drafts (`draft`, `alternative`) — the title, time, note and answers only,
  - makes the Google Doc (`make_doc`) with the drive.file permission (keychain tatsuki-desk-gdocs).
Nothing is sent to anyone. MEETCHECK_FAKE=1 answers without Claude or Google (for the test server).
"""

import base64
import hashlib
import html
import http.server
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import lifelog

MODEL = 'claude-haiku-5-5'
API = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2026-10-21'
DOCS_SERVICE = 'tatsuki-desk-gdocs'  # refresh token for drive.file
GMAIL_SERVICE = 'tatsuki-desk-gmail'  # the OAuth client of the mail cleanup ("Tatsuki Desk" in Google Cloud)
SCOPES = 'https://www.googleapis.com/auth/drive.file'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
FILES = 'https://www.googleapis.com/drive/v3/files'
FOLDER = os.environ.get('MEETCHECK_FOLDER', '1KKgJG6LKBzGOGIAm4zB4Ma5T2d2M5O3u')  # Drive: 仕事の会議の考え方
OWN_FOLDER = '会議の事前資料 (Desk)'  # used when drive.file may not write into the folder above
LENGTHS = (15, 25, 50, 60)
WHY_TEXT = 'text'  # q2 choice meaning "text can decide it" → fails
MAX_REQUIRED = 8
ALTS = ('announce', 'doc', 'recording', 'call')
TOKEN = {'value': '', 'until': 0}
CONNECT = {'running': False, 'url': '', 'error': ''}
LOCK = threading.Lock()


class MeetError(Exception):
    pass


class NotConnected(MeetError):
    pass


def fake():
    return os.environ.get('MEETCHECK_FAKE') == '1'


def testing():
    return bool(os.environ.get('DESK_DATA_DIR')) and not fake()  # a test server never reads the keychain


def is_ja(s):
    return bool(re.search(r'[぀-ヿ一-鿿]', s or ''))


# ---------- the rules (mirrored by mcVerdict in index.html) ----------

VAGUE = re.compile(r'(について|に関して|の件|の共有|共有|報告|update|sync|catch[- ]?up|check[- ]?in)[。.!！]?$|^(about|re:|regarding)\b', re.I)
DECISION = re.compile(r'(か|かどうか|どちら|どっち|どれ)[。.]?$|[?？]$|\bwhether\b|\bwhich\b|\bor\b|\bshould\b', re.I)
MANY = re.compile(r'全員|みんな|皆|チーム全体|everyone|\ball\b|the team', re.I)


def people(v):
    if isinstance(v, list):
        xs = v
    else:
        xs = re.split(r'[,、，\n]+', str(v or ''))
    return [str(x).strip()[:80] for x in xs if str(x).strip()][:40]


def check(answers):
    """Returns {q1..q4: (ok, why)} for the owner's answers."""
    a = answers or {}
    q1 = str((a.get('q1') or {}).get('text') or '').strip()
    q2 = a.get('q2') or {}
    q3 = a.get('q3') or {}
    q4 = a.get('q4') or {}
    out = {}
    if not q1:
        out['q1'] = (False, 'No decision is written.')
    elif VAGUE.search(q1) or not DECISION.search(q1):
        out['q1'] = (False, 'This is a topic, not a decision. Write it as "whether to …" or "A or B".')
    else:
        out['q1'] = (True, '')
    if q2.get('choice') == WHY_TEXT:
        out['q2'] = (False, 'Text or comments can decide it.')
    elif not q2.get('choice') or not str(q2.get('why') or '').strip():
        out['q2'] = (False, 'No reason why text cannot decide it.')
    else:
        out['q2'] = (True, '')
    decider, req = str(q3.get('decider') or '').strip(), people(q3.get('required'))
    if not decider or MANY.search(decider) or len(people(decider)) > 1:
        out['q3'] = (False, 'There is no single person who decides.')
    elif not str(q3.get('proposal') or '').strip():
        out['q3'] = (False, 'There is no proposed answer to react to.')
    elif not req:
        out['q3'] = (False, 'No one is required.')
    elif len(req) > MAX_REQUIRED:
        out['q3'] = (False, f'More than {MAX_REQUIRED} people are required.')
    else:
        out['q3'] = (True, '')
    if int(q4.get('len') or 0) not in LENGTHS:
        out['q4'] = (False, 'No length is chosen.')
    elif q4.get('worth') != 'yes':
        out['q4'] = (False, 'The cost is more than the decision is worth.')
    else:
        out['q4'] = (True, '')
    return out


def verdict(answers):
    """('meet', '') or ('skip', 'q2') — the first question that stopped it."""
    c = check(answers)
    for q in ('q1', 'q2', 'q3', 'q4'):
        if not c[q][0]:
            return 'skip', q
    return 'meet', ''


def cost(answers, rate):
    a = answers or {}
    q3 = a.get('q3') or {}
    n = max(2, len(people(q3.get('required'))) + len(people(q3.get('optional'))))
    mins = int((a.get('q4') or {}).get('len') or 0)
    return {'people': n, 'minutes': mins, 'personMinutes': n * mins, 'yen': round(float(rate or 0) * n * mins / 60)}


# ---------- Claude drafts ----------

DRAFT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['decision', 'whyChoice', 'why', 'decider', 'proposal', 'options', 'required', 'optional', 'length', 'worth', 'before'],
    'properties': {
        'decision': {'type': 'string'}, 'whyChoice': {'type': 'string', 'enum': ['live', 'trust', 'unknowns', 'text']},
        'why': {'type': 'string'}, 'decider': {'type': 'string'}, 'proposal': {'type': 'string'}, 'options': {'type': 'string'},
        'required': {'type': 'array', 'items': {'type': 'string'}}, 'optional': {'type': 'array', 'items': {'type': 'string'}},
        'length': {'type': 'integer', 'enum': list(LENGTHS)}, 'worth': {'type': 'string', 'enum': ['yes', 'no']},
        'before': {'type': 'array', 'items': {'type': 'string'}}}}

DRAFT_SYSTEM = """You help the owner decide whether a meeting is needed. A meeting is allowed only when it decides one thing that text and comments cannot.
From the title, time and note, draft honest answers as the owner, in the first person. Do not make the meeting look necessary. If it looks like sharing information or a status update, say so: write the decision as the topic it really is, choose whyChoice "text" and worth "no".
- decision: one sentence, "whether to …" or "A or B". Write in the language of the title and note.
- whyChoice: live (needs fast back-and-forth), trust (relationship or a sensitive matter), unknowns (too open to write down), text (text or comments can decide it).
- why: one sentence.
- decider: one person. Use "Me" (or 自分 in Japanese) when the owner decides. Leave empty if unknown.
- proposal: the answer the decider would propose now, one sentence. Empty if unknown.
- options: other options and why they lose, one or two short lines.
- required / optional: names or roles only from the title and note; include the owner as "Me" (自分). Do not invent names.
- length: 15, 25, 50 or 60 minutes. Prefer the shortest.
- before: people to talk to one on one first, from the note only. Usually empty.
Use only what is given. Keep every field short."""

ALT_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['kind', 'text', 'reply'],
    'properties': {'kind': {'type': 'string', 'enum': list(ALTS)}, 'text': {'type': 'string'}, 'reply': {'type': 'string'}}}

ALT_SYSTEM = """The owner decided this meeting is not needed. Suggest one way to do it without a meeting.
- kind: announce (a short text announcement), doc (a document people comment on), recording (a short video or voice recording), call (a short call between two people).
- text: what to do, then a ready message or outline, under 600 characters, in the language of the answers.
- reply: only if the owner was invited by someone else: a short, warm reply in the same language that suggests handling it in writing and offers a short call if needed. Otherwise an empty string.
Write as the owner, in the first person; never use the word "owner". Never invent facts, names or dates."""


def _ask(system, user, schema, max_tokens=1500):
    key = None if testing() else lifelog.api_key()
    if not key:
        raise MeetError('No Anthropic API key on this Mac. Write the answers yourself.')
    body = {'model': MODEL, 'max_tokens': max_tokens, 'system': system,
            'output_config': {'effort': 'low', 'format': {'type': 'json_schema', 'schema': schema}},
            'messages': [{'role': 'user', 'content': user}]}
    headers = {'x-api-key': key, 'anthropic-version': API_VERSION, 'content-type': 'application/json', 'user-agent': 'tatsuki-desk'}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise MeetError(f'Claude is not available ({e.code}). Write the answers yourself.') from None
    except (OSError, ValueError):
        raise MeetError("Couldn't reach Claude. Write the answers yourself.") from None
    out = next((b.get('text') for b in res.get('content', []) if b.get('type') == 'text'), None)
    try:
        return json.loads(out)
    except (TypeError, ValueError):
        raise MeetError("Claude's answer couldn't be read. Write the answers yourself.") from None


def _s(v, n=600):
    return str(v or '').strip()[:n]


def draft(rec):
    """POST /api/meetcheck/draft {title, when, note, invited} → {answers}."""
    title, note = _s(rec.get('title'), 200), _s(rec.get('note'), 600)
    if not title and not note:
        raise MeetError('Write a title or a note first.')
    ja = is_ja(title + note)
    if fake():
        d = {'decision': '', 'whyChoice': 'text', 'why': '', 'decider': '自分' if ja else 'Me', 'proposal': '', 'options': '',
             'required': ['自分' if ja else 'Me'], 'optional': [], 'length': 25, 'worth': 'no', 'before': []}
    else:
        user = f'Title: {title}\nTime: {_s(rec.get("when"), 80)}\nInvited by someone else: {"yes" if rec.get("invited") else "no"}\nNote: {note}'
        d = _ask(DRAFT_SYSTEM, user, DRAFT_SCHEMA)
    return {'answers': {
        'q1': {'text': _s(d.get('decision'), 300)},
        'q2': {'choice': d.get('whyChoice') if d.get('whyChoice') in ('live', 'trust', 'unknowns', 'text') else '', 'why': _s(d.get('why'), 300)},
        'q3': {'decider': _s(d.get('decider'), 80), 'proposal': _s(d.get('proposal'), 300), 'options': _s(d.get('options'), 400),
               'required': people(d.get('required')), 'optional': people(d.get('optional'))},
        'q4': {'len': d.get('length') if d.get('length') in LENGTHS else 25, 'worth': 'yes' if d.get('worth') == 'yes' else 'no'},
        'q5': {'people': people(d.get('before'))}}}


ALT_FOR = {'q1': 'announce', 'q2': 'doc', 'q3': 'call', 'q4': 'recording'}


def alternative(rec):
    """POST /api/meetcheck/alt {title, answers, failed, invited} → {kind, text, reply}."""
    answers = rec.get('answers') or {}
    kind = ALT_FOR.get(rec.get('failed'), 'doc')
    if fake():
        return {'kind': kind, 'text': '', 'reply': ''}
    user = (f'Title: {_s(rec.get("title"), 200)}\nInvited by someone else: {"yes" if rec.get("invited") else "no"}\n'
            f'Stopped at: {check(answers).get(rec.get("failed"), (True, ""))[1]}\nAnswers: {json.dumps(answers, ensure_ascii=False)[:3000]}')
    d = _ask(ALT_SYSTEM, user, ALT_SCHEMA, 1200)
    return {'kind': d.get('kind') if d.get('kind') in ALTS else kind, 'text': _s(d.get('text'), 1200),
            'reply': _s(d.get('reply'), 1200) if rec.get('invited') else ''}


# ---------- Google: the pre-read doc ----------

def _kc_get(service, account):
    try:
        out = subprocess.run(['security', 'find-generic-password', '-s', service, '-a', account, '-w'],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.stdout.strip() if out.returncode == 0 else ''


def _kc_set(service, account, value):
    line = 'add-generic-password -U -s "%s" -a "%s" -w "%s"\n' % (service, account, value.replace('\\', '\\\\').replace('"', '\\"'))
    out = subprocess.run(['security', '-i'], input=line, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        raise OSError('keychain')


def _client():
    raw = _kc_get(GMAIL_SERVICE, 'client')
    return json.loads(raw) if raw else None


def status():
    if fake():
        return {'connected': True, 'client': True, 'connecting': False, 'error': '', 'ai': True}
    if testing():
        return {'connected': False, 'client': False, 'connecting': False, 'error': '', 'ai': False}
    return {'connected': bool(_kc_get(DOCS_SERVICE, 'refresh')), 'client': bool(_client()), 'ai': bool(lifelog.api_key()),
            'connecting': CONNECT['running'], 'url': CONNECT['url'] if CONNECT['running'] else '', 'error': CONNECT['error']}


def _form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            raise NotConnected('Google Docs needs to be connected again') from None
        raise MeetError(f'Google returned an error ({e.code})') from None
    except OSError:
        raise MeetError("Couldn't reach Google. Check the internet connection") from None


def connect():
    """Opens Google's consent page (drive.file: only files this app makes). The owner presses Allow."""
    if fake() or testing():
        raise MeetError('The test server does not connect to Google.')
    client = _client()
    if not client:
        raise MeetError('The Google connection file is missing. Connect the mail cleanup once first (python3 desk/gmail_clean.py auth)')
    with LOCK:
        if CONNECT['running']:
            return {'url': CONNECT['url']}
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
                self.wfile.write('<p>Google Docs is connected to the Desk. You can close this window.</p>'.encode())

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
                raise MeetError(got.get('error') or 'The sign-in was not finished in 5 minutes')
            data = _form(TOKEN_URL, {'code': got['code'], 'client_id': client['id'], 'client_secret': client['secret'],
                                     'redirect_uri': redirect, 'grant_type': 'authorization_code', 'code_verifier': verifier})
            if not data.get('refresh_token'):
                raise MeetError('Google sent no lasting permission. Please try again')
            _kc_set(DOCS_SERVICE, 'refresh', data['refresh_token'])
            TOKEN.update(value=data.get('access_token', ''), until=time.time() + int(data.get('expires_in', 3600)) - 60)
        except (MeetError, OSError) as e:
            CONNECT['error'] = str(e)
        finally:
            srv.server_close()
            CONNECT.update(running=False, url='')

    threading.Thread(target=wait, daemon=True).start()
    webbrowser.open(url)
    return {'url': url}


def _token():
    with LOCK:
        if TOKEN['value'] and time.time() < TOKEN['until']:
            return TOKEN['value']
    client, refresh = _client(), _kc_get(DOCS_SERVICE, 'refresh')
    if not client or not refresh:
        raise NotConnected('Connect Google Docs first')
    data = _form(TOKEN_URL, {'client_id': client['id'], 'client_secret': client['secret'], 'refresh_token': refresh, 'grant_type': 'refresh_token'})
    with LOCK:
        TOKEN.update(value=data['access_token'], until=time.time() + int(data.get('expires_in', 3600)) - 60)
        return TOKEN['value']


def _google(method, url, body=None, ctype='application/json'):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={'Authorization': 'Bearer ' + _token(), 'Content-Type': ctype})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            TOKEN.update(value='', until=0)
            raise NotConnected('Google Docs needs to be connected again') from None
        try:
            err = json.load(e).get('error', {})
        except (ValueError, OSError):
            err = {}
        if any(d.get('reason') == 'SERVICE_DISABLED' for d in err.get('details', [])):
            raise MeetError('The Google Drive API is turned off in the Google Cloud project. Turn it on once, then try again') from None
        raise MeetError(f'Google Drive returned an error ({e.code})', e.code) from None
    except OSError:
        raise MeetError("Couldn't reach Google Drive. Check the internet connection") from None


def _upload(name, page, parent):
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.document', 'parents': [parent] if parent else []}
    b = 'desk' + secrets.token_hex(8)
    body = (f'--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n'
            f'--{b}\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n{page}\r\n--{b}--').encode()
    return _google('POST', UPLOAD + '?uploadType=multipart&fields=id,webViewLink', body, f'multipart/related; boundary={b}')


def _own_folder(name=OWN_FOLDER):
    """A folder this app made (drive.file sees only those), made once."""
    q = urllib.parse.quote(f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    found = _google('GET', f'{FILES}?q={q}&fields=files(id)').get('files') or []
    if found:
        return found[0]['id']
    return _google('POST', FILES + '?fields=id', {'name': name, 'mimeType': 'application/vnd.google-apps.folder'})['id']


def put_doc(name, page, folder_name, role='reader', file_id=''):
    """Makes (or, with file_id, rewrites) a Google Doc from HTML in a folder this app made, readable by anyone
    with the link (role reader) or commentable (commenter). Returns {id, url}. Used by Minutes too."""
    if fake():
        fid = file_id or 'FAKE' + hashlib.sha1(name.encode()).hexdigest()[:20]
        return {'id': fid, 'url': f'https://docs.google.com/document/d/{fid}/edit?usp=sharing'}
    if file_id:
        b = 'desk' + secrets.token_hex(8)
        body = (f'--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps({"name": name})}\r\n'
                f'--{b}\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n{page}\r\n--{b}--').encode()
        try:
            made = _google('PATCH', f'{UPLOAD}/{file_id}?uploadType=multipart&fields=id,webViewLink', body, f'multipart/related; boundary={b}')
        except MeetError as e:
            if len(e.args) < 2 or e.args[1] != 404:
                raise
            made = _upload(name, page, _own_folder(folder_name))  # the old doc was deleted: make a new one
    else:
        made = _upload(name, page, _own_folder(folder_name))
    _google('POST', f'{FILES}/{made["id"]}/permissions?sendNotificationEmail=false', {'role': role, 'type': 'anyone'})
    return {'id': made['id'], 'url': made.get('webViewLink') or f'https://docs.google.com/document/d/{made["id"]}/edit'}


L = {
    'ja': {'title': '事前資料', 'decide': '決めること', 'decider': '決める人', 'proposal': '提案する結論', 'why': 'なぜ会議で決めるか',
           'options': '検討した案（選んだ案・外した案）', 'who': '参加者', 'req': '必須', 'opt': '任意（資料へのコメントだけでも可）',
           'time': '時間と費用', 'mins': '分', 'people': '人', 'how': '進め方', 'before': '事前に話す人', 'record': '記録（会議のあとに書く）',
           'decided': '決めたこと', 'actions': 'アクション', 'what': '何を', 'whom': '誰が', 'due': 'いつまで', 'open': '決まらなかったこと',
           'note': 'メモ', 'none': '（なし）',
           'steps': ['この資料に、会議の前にコメントしてください。', '最初の5分は全員で黙読し、コメントを足します。',
                     '質問とコメントを順に片づけます。', f'決める人が結論を出し、下の「記録」に書きます。', '時間になったら終わります。決まらなかったことは記録に残します。']},
    'en': {'title': 'Pre-read', 'decide': 'Decision', 'decider': 'Decider', 'proposal': 'Proposed answer', 'why': 'Why a meeting',
           'options': 'Options considered (chosen and dropped)', 'who': 'People', 'req': 'Required', 'opt': 'Optional (commenting on this doc is enough)',
           'time': 'Time and cost', 'mins': 'min', 'people': 'people', 'how': 'How the meeting runs', 'before': 'Talk to beforehand',
           'record': 'Record (filled in after the meeting)', 'decided': 'Decided', 'actions': 'Actions', 'what': 'What', 'whom': 'Who',
           'due': 'By when', 'open': 'Not decided', 'note': 'Note', 'none': '(none)',
           'steps': ['Comment on this doc before the meeting.', 'The first 5 minutes: everyone reads in silence and adds comments.',
                     'Go through the questions and comments.', 'The decider decides and writes it in the Record below.',
                     'End on time. Write down what was not decided.']},
}


def doc_html(rec, rate):
    a = rec.get('answers') or {}
    q1, q2, q3, q4, q5 = (a.get(k) or {} for k in ('q1', 'q2', 'q3', 'q4', 'q5'))
    lang = 'ja' if is_ja(json.dumps([rec.get('title'), a], ensure_ascii=False)) else 'en'
    t = L[lang]
    e = lambda s: html.escape(str(s or ''))
    lst = lambda xs: '<ul>' + ''.join(f'<li>{e(x)}</li>' for x in xs) + '</ul>' if xs else f'<p>{t["none"]}</p>'
    c = cost(a, rate)
    when = _s(rec.get('when'), 80)
    rows = ''.join('<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>' for _ in range(3))
    parts = [
        f'<h1>{e(rec.get("title"))} — {t["title"]}</h1>',
        f'<p>{e(when)}</p>' if when else '',
        f'<h2>{t["decide"]}</h2><p>{e(q1.get("text"))}</p>',
        f'<h2>{t["decider"]}</h2><p>{e(q3.get("decider"))}</p>',
        f'<h2>{t["proposal"]}</h2><p>{e(q3.get("proposal"))}</p>',
        f'<h2>{t["why"]}</h2><p>{e(q2.get("why"))}</p>' + (f'<p>{t["note"]}: {e(rec.get("note"))}</p>' if rec.get('note') else ''),
        f'<h2>{t["options"]}</h2><p>{e(q3.get("options")) or t["none"]}</p>',
        f'<h2>{t["who"]}</h2><h3>{t["req"]}</h3>{lst(people(q3.get("required")))}<h3>{t["opt"]}</h3>{lst(people(q3.get("optional")))}',
        f'<h2>{t["time"]}</h2><p>{c["minutes"]} {t["mins"]} × {c["people"]} {t["people"]} = {c["personMinutes"]} {t["mins"]} · ¥{c["yen"]:,}</p>',
        f'<h2>{t["how"]}</h2><ol>' + ''.join(f'<li>{e(s)}</li>' for s in t['steps']) + '</ol>',
        f'<h2>{t["before"]}</h2>{lst(people(q5.get("people")))}' if people(q5.get('people')) else '',
        f'<h2>{t["record"]}</h2><h3>{t["decided"]}</h3><ul><li>&nbsp;</li></ul><h3>{t["actions"]}</h3>'
        f'<table border="1" cellpadding="4"><tr><th>{t["what"]}</th><th>{t["whom"]}</th><th>{t["due"]}</th></tr>{rows}</table>'
        f'<h3>{t["open"]}</h3><ul><li>&nbsp;</li></ul>',
    ]
    return '<html><head><meta charset="utf-8"></head><body>' + ''.join(parts) + '</body></html>', lang, t


def make_doc(rec, rate):
    """POST /api/meetcheck/doc {record, rate} → {url, folder}. Only for a meeting that passed or was overridden."""
    v, _ = verdict(rec.get('answers'))
    if v != 'meet' and not _s(rec.get('override')):
        raise MeetError('This meeting did not pass the questions.')
    page, lang, t = doc_html(rec, rate)
    name = f'{_s(rec.get("title"), 120) or "Meeting"} — {t["title"]}'
    if fake():
        return {'url': 'https://docs.google.com/document/d/FAKE' + hashlib.sha1(name.encode()).hexdigest()[:20] + '/edit', 'folder': 'fake'}
    try:
        made, where = _upload(name, page, FOLDER), 'folder'
    except MeetError as e:
        if len(e.args) < 2 or e.args[1] not in (403, 404):
            raise
        made, where = _upload(name, page, _own_folder()), 'own'  # drive.file may not write into a folder it did not make
    _google('POST', f'{FILES}/{made["id"]}/permissions?sendNotificationEmail=false', {'role': 'commenter', 'type': 'anyone'})
    return {'url': made.get('webViewLink') or f'https://docs.google.com/document/d/{made["id"]}/edit', 'folder': where}


def handle(path, rec):
    """POST /api/meetcheck/<draft|alt|doc|connect>. Returns (status, body)."""
    try:
        if path == 'draft':
            return 200, draft(rec)
        if path == 'alt':
            return 200, alternative(rec)
        if path == 'doc':
            return 200, make_doc(rec.get('record') or {}, rec.get('rate') or 3000)
        if path == 'connect':
            return 200, connect()
        return 404, {'error': 'not found'}
    except NotConnected as e:
        return 409, {'error': str(e), 'connect': True}
    except MeetError as e:
        return 400, {'error': str(e.args[0])}
