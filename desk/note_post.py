#!/usr/bin/env python3
"""Tatsuki's Desk — Post Studio's "Save to note as draft".

note has no public API. This uses the same calls note's own editor makes (other people's open tools do the same:
create an empty draft, then draft_save; pictures go up through note's presigned upload). It only ever saves a
draft: publishing, the hashtags and note's own scheduled posting stay on note's page, done by the person.

Sign-in: the person signs in to note in their browser and copies the value of the `_note_session_v5` cookie into
`python3 note_post.py connect` (hidden input). It goes to the keychain (tatsuki-desk-note) and nowhere else; no
password is asked for or kept. When note signs the session out, the card says to connect again.

Because these calls are not public, note can change them at any time. When that happens the card shows the reason
and the Copy buttons still work.

  python3 note_post.py connect   paste the session cookie (from the browser, after signing in to note)
  python3 note_post.py status    who is signed in
"""
import getpass
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

SERVICE = "tatsuki-desk-note"
API = "https://note.com/api"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
_cache = {"at": 0, "me": None, "xsrf": ""}


def _kc_get():
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", "session", "-w"], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _kc_set(value):
    subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", "session", "-w", value], capture_output=True, timeout=20, check=True)


class NoteError(RuntimeError):
    pass


def _cookie():
    s = _kc_get()
    if not s:
        raise NoteError("note is not connected. Run: python3 desk/note_post.py connect")
    c = f"_note_session_v5={s}"
    if _cache["xsrf"]:
        c += f"; XSRF-TOKEN={urllib.request.quote(_cache['xsrf'])}"
    return c


def _req(method, url, body=None, ctype="application/json", editor=False):
    headers = {"User-Agent": UA, "Accept": "application/json", "Cookie": _cookie()}
    if method != "GET":
        headers.update({"Content-Type": ctype, "X-Requested-With": "XMLHttpRequest",
                        "Origin": "https://editor.note.com" if editor else "https://note.com",
                        "Referer": "https://editor.note.com/" if editor else "https://note.com/"})
        if _cache["xsrf"]:
            headers["X-XSRF-TOKEN"] = _cache["xsrf"]
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw, h, code = r.read(), r.headers, r.status
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            _cache.update(me=None, xsrf="")
            raise NoteError("note signed this Mac out. Connect again: python3 desk/note_post.py connect") from None
        raise NoteError(f"note answered {e.code}. note may have changed its editor; use Copy for now") from None
    except OSError:
        raise NoteError("Could not reach note") from None
    _take_xsrf(h)
    try:
        return code, json.loads(raw or b"{}")
    except ValueError:
        return code, {}


def _take_xsrf(h):
    tok = h.get("X-XSRF-TOKEN") or ""
    for c in h.get_all("Set-Cookie") or []:
        m = re.match(r"XSRF-TOKEN=([^;]+)", c)
        if m:
            tok = m.group(1)
    if tok:
        _cache["xsrf"] = urllib.request.unquote(tok)


def me(fresh=False):
    """{id, urlname, name} of the signed-in note account, or None. Kept 10 minutes."""
    if not _kc_get():
        return None
    if not fresh and _cache["me"] and time.time() - _cache["at"] < 600:
        return _cache["me"]
    _, v = _req("GET", f"{API}/v2/current_user")
    d = v.get("data") or {}
    if not d.get("id"):
        _cache["me"] = None
        raise NoteError("note did not say who is signed in. Connect again: python3 desk/note_post.py connect")
    _cache.update(me={"id": d["id"], "urlname": d.get("urlname") or "", "name": d.get("nickname") or d.get("urlname") or ""}, at=time.time())
    return _cache["me"]


def status():
    """For the page: connected or not, and the account name. Never raises."""
    if not _kc_get():
        return {"connected": False}
    try:
        m = me()
        return {"connected": True, "handle": m["urlname"], "name": m["name"]}
    except NoteError as e:
        return {"connected": False, "problem": str(e)}


# ---------- the article body ----------

def _inline(t):
    t = html.escape(t, quote=True)
    t = re.sub(r"\[([^\]]+)\]\((https://[^\s)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"(^|[\s（(])(https://[^\s<）)]+)", r'\1<a href="\2">\2</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(^|[^*])\*([^*\s][^*]*)\*", r"\1<em>\2</em>", t)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", t)


def _tag(name, inner=""):
    u = str(uuid.uuid4())  # note's editor gives every top-level block its own name and id
    return f'<{name} name="{u}" id="{u}">{inner}</{name}>' if name != "hr" else f'<hr name="{u}" id="{u}">'


def to_html(md):
    """The draft's Markdown as note's editor keeps it: h2/h3, paragraphs, lists, quotes, rules, bold, links."""
    out, items, kind = [], [], ""

    def close():
        nonlocal items, kind
        if items:
            out.append(_tag(kind, "".join(f"<li>{x}</li>" for x in items)))
        items, kind = [], ""

    for raw in str(md or "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            close()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close()
            n = len(m.group(1))
            out.append(_tag("h2" if n <= 2 else "h3", _inline(m.group(2))) if n <= 3 else _tag("p", f"<strong>{_inline(m.group(2))}</strong>"))
            continue
        if re.match(r"^(-{3,}|\*{3,}|━+)$", line.strip()):
            close()
            out.append(_tag("hr"))
            continue
        m = re.match(r"^\s*[-*・]\s+(.*)$", line) or re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m:
            k = "ul" if re.match(r"^\s*[-*・]\s", line) else "ol"
            if kind != k:
                close()
                kind = k
            items.append(_inline(m.group(1)))
            continue
        m = re.match(r"^>\s?(.*)$", line)
        close()
        out.append(_tag("blockquote", _inline(m.group(1))) if m else _tag("p", _inline(line)))
    close()
    return "".join(out)


def _figure(url):
    return _tag("figure", f'<img src="{html.escape(url, quote=True)}" alt="">')


# ---------- pictures ----------

def _multipart(fields, file=None):
    b = "----DeskNote" + uuid.uuid4().hex
    parts = []
    for k, v in fields:
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    if file:
        name, data, ctype = file
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def upload_image(path):
    """One picture into note's own storage. Returns its URL for the article body."""
    with open(path, "rb") as f:
        data = f.read()
    name = f"desk-{uuid.uuid4().hex[:10]}.jpg"
    body, ctype = _multipart([("filename", name)])
    _, v = _req("POST", f"{API}/v3/images/upload/presigned_post", body, ctype, editor=True)
    d = v.get("data") or {}
    if not d.get("action") or not isinstance(d.get("post"), dict) or not d.get("url"):
        raise NoteError("note did not accept the picture. note may have changed its editor")
    order = ["key", "acl", "Expires", "Content-Type", "success_action_status", "policy", "x-amz-credential",
             "x-amz-algorithm", "x-amz-date", "x-amz-security-token", "x-amz-signature"]
    post = d["post"]
    fields = [(k, post[k]) for k in order if post.get(k) not in (None, "")] + \
             [(k, x) for k, x in post.items() if k not in order and x not in (None, "")]
    body, ctype = _multipart(fields, (name, data, "image/jpeg"))
    req = urllib.request.Request(d["action"], data=body, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=120):
            pass
    except urllib.error.HTTPError as e:
        if e.code not in (200, 201, 204):
            raise NoteError(f"The picture upload stopped ({e.code})") from None
    except OSError:
        raise NoteError("Could not reach note's picture storage") from None
    return d["url"]


def set_eyecatch(note_id, path):
    with open(path, "rb") as f:
        data = f.read()
    body, ctype = _multipart([("note_id", str(note_id))], ("blob", data, "image/jpeg"))
    code, _ = _req("POST", f"{API}/v1/image_upload/note_eyecatch", body, ctype, editor=True)
    return code in (200, 201)


# ---------- the draft ----------

def save_draft(title, markdown, cover=None, pictures=(), note_id=None):
    """Creates (or, with note_id, updates) one draft. cover: the header picture. pictures: more pictures, placed after
    the first paragraph. Returns {id, key, editUrl, cover}. Never publishes."""
    title = (title or "").strip()[:100] or "Untitled"
    m = me()
    body = to_html(markdown)
    figs = "".join(_figure(upload_image(p)) for p in pictures)
    if figs:
        cut = body.find("</p>")
        body = body[:cut + 4] + figs + body[cut + 4:] if cut >= 0 else figs + body
    if not note_id:
        _, v = _req("POST", f"{API}/v1/text_notes?user_id={m['id']}",
                    {"body": "<p></p>", "body_length": 0, "name": title, "index": False, "is_lead_form": False}, editor=True)
        d = v.get("data") or v
        n = d.get("note") or d.get("text_note") or d
        note_id = n.get("id") or d.get("note_id")
        key = n.get("key") or d.get("note_key") or ""
        if not note_id:
            raise NoteError("note did not make a draft. note may have changed its editor; use Copy for now")
    else:
        key = ""
    plain = re.sub(r"<[^>]+>", "", body)
    _req("POST", f"{API}/v1/text_notes/draft_save?id={int(note_id)}&is_temp_saved=true",
         {"body": body, "body_length": len(plain), "name": title, "index": False, "is_lead_form": False}, editor=True)
    covered = bool(cover) and set_eyecatch(note_id, cover)
    key = key or f"n{note_id}"
    return {"id": int(note_id), "key": key, "editUrl": f"https://editor.note.com/notes/{key}/edit/", "cover": covered}


def connect():
    print("1. Sign in to note in your browser (Chrome).")
    print("2. Open the developer tools (Option+Command+I) > Application > Cookies > https://note.com")
    print("3. Copy the Value of _note_session_v5 and paste it below (it stays hidden).")
    v = getpass.getpass("_note_session_v5: ").strip().strip('"')
    if not re.fullmatch(r"[A-Za-z0-9%._\-]{20,600}", v):
        return "That does not look like the cookie value. Nothing was saved."
    _kc_set(v)
    _cache.update(me=None, xsrf="")
    try:
        m = me(fresh=True)
    except NoteError as e:
        return f"Saved, but note did not accept it: {e}"
    return f"Connected as {m['name']} (note.com/{m['urlname']})."


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "connect":
        print(connect())
    else:
        print(json.dumps(status(), ensure_ascii=False))
