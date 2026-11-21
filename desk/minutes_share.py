"""Minutes → a Google Doc anyone with the link can read, and a thank-you mail as a Gmail draft.

- `make_doc(mid)`: the minute's text as a Google Doc in the Drive folder 「議事録 (Desk)」, shared "anyone with the link can view".
  A second press rewrites the same doc (the link stays). Permission: drive.file (the one "Should we meet?" uses).
- `people(mid)`: the names on the minute's 参加者 / Who line, each looked up in the owner's Gmail (From/To headers only)
  to suggest an address. Nothing is sent.
- `thanks(mid, to, subject, body)`: a Gmail draft. The Desk never sends mail; the owner presses Send in Gmail.
"""

import base64
import hashlib
import html
import re
import time
from email.mime.text import MIMEText
from email.utils import getaddresses, formataddr

import meetcheck
import minutes

FOLDER = '議事録 (Desk)'
EMAIL = re.compile(r'^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$')


class ShareError(Exception):
    pass


def _minute(mid):
    try:
        return minutes.get(mid)
    except minutes.MinutesError:
        raise ShareError('Not found') from None


def _ja(m):
    return bool(re.search(r'[぀-ヿ一-鿿]', (m.get('title') or '') + (m.get('body') or '')[:400]))


def md_html(src):
    """The minute's markdown (## headings, bullets one level deep, **bold**, one-line tables) as HTML for Google Docs."""
    inl = lambda s: re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html.escape(s))
    out, lines, i = [], (src or '').split('\n'), 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith('## '):
            out.append(f'<h2>{inl(line[3:])}</h2>')
            i += 1
            continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                if not re.match(r'^\|[\s:|-]+\|?$', lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            if rows:
                head, *body = rows
                out.append('<table border="1" cellpadding="4"><tr>' + ''.join(f'<th>{inl(c)}</th>' for c in head) + '</tr>'
                           + ''.join('<tr>' + ''.join(f'<td>{inl(c)}</td>' for c in r) + '</tr>' for r in body) + '</table>')
            continue
        if re.match(r'^\s*- ', line):
            items = []
            while i < len(lines) and re.match(r'^\s*- ', lines[i]):
                text = re.sub(r'^\s*- (\[( |x)\] )?', '', lines[i])
                if lines[i].startswith('  ') and items:
                    items[-1][1].append(text)
                else:
                    items.append([text, []])
                i += 1
            out.append('<ul>' + ''.join(f'<li>{inl(t)}' + (('<ul>' + ''.join(f'<li>{inl(x)}</li>' for x in sub) + '</ul>') if sub else '') + '</li>'
                                        for t, sub in items) + '</ul>')
            continue
        out.append(f'<p>{inl(line)}</p>')
        i += 1
    return ''.join(out)


SHARE_V = 2  # what goes into a doc changed (no raw transcript or notes): a new value makes every doc rewritten once


def fingerprint(m):
    return hashlib.sha1(f"{SHARE_V}\n{m.get('title') or ''}\n{doc_body(m)}".encode()).hexdigest()[:16]


RAW = re.compile(r'^## (Transcript|Original notes|元のメモ)\s*$', re.M)
PENDING = re.compile(r'^## Summary\nNot made yet\.', re.M)


def shareable(m):
    """Only minutes with a summary go to Google: never one that is still a raw transcript or notes waiting for minutes."""
    body = m.get('body') or ''
    return bool(body.strip()) and not PENDING.search(body) and not body.lstrip().startswith('## Summary\nNot made yet')


def doc_body(m):
    """The minute without the raw transcript or the original notes under it."""
    body = m.get('body') or ''
    cut = RAW.search(body)
    return body[:cut.start()].rstrip() if cut else body


def make_doc(mid):
    m = _minute(mid)
    if not shareable(m):
        raise ShareError('This minute has no summary yet. Make the minutes first')
    title = m.get('title') or 'Minutes'
    day = time.strftime('%Y-%m-%d', time.localtime(m.get('at') or time.time()))
    name = f'{day} {title}' + (' 議事録' if _ja(m) else ' — Minutes')
    page = ('<html><head><meta charset="utf-8"></head><body>'
            f'<h1>{html.escape(title)}</h1>' + md_html(doc_body(m)) + '</body></html>')
    old = (m.get('gdoc') or {}).get('id', '')
    try:
        doc = meetcheck.put_doc(name, page, FOLDER, 'reader', old)
    except meetcheck.NotConnected:
        raise
    except meetcheck.MeetError as e:
        raise ShareError(str(e.args[0])) from None
    minutes.set_share(mid, 'gdoc', {'id': doc['id'], 'url': doc['url'], 'at': time.time(), 'fp': fingerprint(m)})
    return {'url': doc['url'], 'again': bool(old)}


PEOPLE_LINE = re.compile(r'^- (参加者|Who)[：:]\s*(.+)$', re.M)
NOT_NAMES = re.compile(r'記録から分かりません|not clear|^me$|^自分$|^speaker [a-d]$|^話者', re.I)


def names(m):
    found = PEOPLE_LINE.search(m.get('body') or '')
    if not found:
        return []
    out = [x.strip() for x in re.split(r'[、,，/]', found.group(2)) if x.strip()]
    return [x for x in out if not NOT_NAMES.search(x)][:10]


def people(mid):
    """[{name, emails:[…]}]: addresses seen next to that name in the owner's mail (headers only)."""
    import gmail_clean
    m = _minute(mid)
    if meetcheck.fake():
        return {'people': [{'name': n, 'emails': [f'person{i}@example.com']} for i, n in enumerate(names(m))]}
    out = []
    for name in names(m):
        seen = {}
        try:
            res = gmail_clean.gmail('GET', '/messages?maxResults=8&q=' + _q(f'"{name}"'))
            for ref in (res.get('messages') or [])[:8]:
                msg = gmail_clean.gmail('GET', f'/messages/{ref["id"]}?format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Cc')
                for h in (msg.get('payload') or {}).get('headers', []):
                    for disp, addr in getaddresses([h.get('value', '')]):
                        if addr and _matches(name, disp, addr):
                            seen[addr.lower()] = seen.get(addr.lower(), 0) + 1
        except gmail_clean.NotConnected:
            raise ShareError('Gmail is not connected. Connect it once in Inbox Cleanup') from None
        except gmail_clean.GmailError:
            raise ShareError("Couldn't read Gmail. Try again in a minute") from None
        out.append({'name': name, 'emails': [a for a, _ in sorted(seen.items(), key=lambda x: -x[1])][:3]})
    return {'people': out}


def _q(s):
    import urllib.parse
    return urllib.parse.quote(s)


def _matches(name, disp, addr):
    n = re.sub(r'\s|さん|様|氏', '', name).lower()
    d = re.sub(r'\s', '', disp or '').lower()
    return bool(n) and (n in d or (len(n) >= 3 and n in addr.lower().split('@')[0]))


def draft_text(mid):
    """The thank-you mail the page shows before a draft is made (the owner can change every word)."""
    m = _minute(mid)
    link = (m.get('gdoc') or {}).get('url', '')
    title = m.get('title') or ''
    if _ja(m):
        return {'subject': f'本日はありがとうございました（{title}）',
                'body': f'本日はお時間をいただき、ありがとうございました。\n\n今日の議事録です。リンクを知っている人は誰でも見られます。\n{link}\n\n'
                        '内容に違いや足りないところがあれば、このメールに返信していただけると助かります。\n引き続きよろしくお願いいたします。\n'}
    return {'subject': f'Thank you for today ({title})',
            'body': f'Thank you for your time today.\n\nHere are the minutes from our meeting. Anyone with the link can view them.\n{link}\n\n'
                    'If anything is wrong or missing, just reply to this email.\nBest regards,\n'}


def thanks(mid, rec):
    """A Gmail draft to the given addresses. Never sends."""
    import gmail_clean
    m = _minute(mid)
    if not (m.get('gdoc') or {}).get('url'):
        raise ShareError('Make the Google Doc first, so the mail has its link')
    to = [a.strip() for a in (rec.get('to') or []) if isinstance(a, str) and EMAIL.match(a.strip())][:20]
    if not to:
        raise ShareError('Add at least one email address')
    subject = str(rec.get('subject') or '').strip()[:200]
    body = str(rec.get('body') or '').strip()[:5000]
    if not subject or not body:
        raise ShareError('The subject and the text are both needed')
    msg = MIMEText(body + '\n', 'plain', 'utf-8')
    msg['To'] = ', '.join(formataddr(('', a)) for a in to)
    msg['Subject'] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    if meetcheck.fake():
        minutes.set_share(mid, 'thanks', {'to': to, 'subject': subject, 'draftId': 'fake', 'at': time.time()})
        return {'url': 'about:blank'}
    try:
        d = gmail_clean.gmail('POST', '/drafts', {'message': {'raw': raw}})
    except gmail_clean.NotConnected:
        raise ShareError('Gmail is not connected. Connect it once in Inbox Cleanup') from None
    except gmail_clean.GmailError:
        raise ShareError("Couldn't make the Gmail draft. Try again in a minute") from None
    mail_id = (d.get('message') or {}).get('id', '')
    minutes.set_share(mid, 'thanks', {'to': to, 'subject': subject, 'draftId': d.get('id', ''), 'at': time.time()})
    return {'url': f'https://mail.google.com/mail/u/0/#drafts?compose={mail_id}' if mail_id else 'https://mail.google.com/mail/u/0/#drafts'}


AUTO_PER_PASS = 3   # docs made or rewritten per minute, so 30 old minutes do not hit Google at once
QUIET = 120         # a minute edited less than 2 minutes ago waits, so the doc is not rewritten while it is being edited


def auto():
    """Every minute gets its Google Doc, and the doc follows the text. Returns how many docs were made or rewritten."""
    if not meetcheck.status().get('connected'):
        return 0
    done = 0
    rows = sorted(minutes.view()['minutes'], key=lambda r: -(r.get('at') or 0))  # newest first
    for m in rows:
        if done >= AUTO_PER_PASS:
            break
        g = m.get('gdoc') or {}
        if not shareable(m):
            if g.get('id'):  # it had a doc, and now has no summary: the doc must not stay public
                forget(m['id'])
                minutes.set_share(m['id'], 'gdoc', None)
            continue
        if g and g.get('fp') == fingerprint(m):
            continue
        if g and time.time() - (m.get('updated') or 0) < QUIET:
            continue
        if (g.get('failedAt') or 0) > time.time() - 3600:  # a doc that failed waits an hour
            continue
        try:
            make_doc(m['id'])
            done += 1
        except meetcheck.NotConnected:
            return done
        except (ShareError, OSError):
            minutes.set_share(m['id'], 'gdoc', {**g, 'failedAt': time.time()})
            print('[minutes] a Google Doc could not be made', flush=True)
    return done


def forget(mid):
    """Before a minute is deleted: its Google Doc goes to the Drive trash, so the link stops working."""
    try:
        g = minutes.get(mid).get('gdoc') or {}
    except minutes.MinutesError:
        return
    if not g.get('id') or meetcheck.fake():
        return
    try:
        meetcheck._google('PATCH', f'{meetcheck.FILES}/{g["id"]}', {'trashed': True})
    except (meetcheck.MeetError, OSError):
        print('[minutes] the Google Doc of a deleted minute could not be trashed', flush=True)


def watch():
    """Run by the Desk server on 4747 only."""
    while True:
        try:
            auto()
        except Exception as e:  # keep the loop alive; the message names no content
            print(f'[minutes] doc pass failed: {type(e).__name__}', flush=True)
        time.sleep(60)


def handle(mid, action, rec):
    """POST /api/minutes/<id>/gdoc|people|thanks, GET …/thanks-text. Returns (status, body)."""
    try:
        if action == 'gdoc':
            return 200, make_doc(mid)
        if action == 'people':
            return 200, people(mid)
        if action == 'thanks':
            return 200, thanks(mid, rec)
        if action == 'thanks-text':
            return 200, draft_text(mid)
        return 404, {'error': 'not found'}
    except meetcheck.NotConnected as e:
        return 409, {'error': str(e), 'connect': True}
    except ShareError as e:
        return (404 if str(e) == 'Not found' else 400), {'error': str(e)}
