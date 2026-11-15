#!/usr/bin/env python3
"""Tatsuki's Desk — 地球キャンパス通信 (Campus Letter), the free weekly newsletter.

Issues are written on the Desk screen #campus and kept on this Mac (desk/data/campus/issues/<id>.json).
Readers live only in Cloudflare (the tany.im Worker's D1, tables campus_*): this Mac never holds their addresses.

Sending, only when the person presses a button:
- Send test: the Worker sends the issue to the test address in settings.
- Send to readers: the issue's teaser page goes up on tany.im/campus/<no>/ (with its photos), the site is deployed,
  then the Worker sends the issue to every confirmed reader who has not had it. The issue is locked after that.
Nothing here sends on its own; no scheduled task calls send().

Settings (desk/data/campus/settings.json, never in git): the sender's name, postal address and contact address
(the law on commercial e-mail asks for them in every issue), and the test address.
The Worker's passphrase is in the keychain as tatsuki-desk-campus (the Worker has it as CAMPUS_KEY).
"""
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import lifelog
import site_news

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_CAMPUS_DIR") or os.path.join(ROOT, "data", "campus")
ISSUES = os.path.join(DIR, "issues")
SETTINGS = os.path.join(DIR, "settings.json")
UPLOADS = os.path.join(ROOT, "data", "uploads")
KEYCHAIN = "tatsuki-desk-campus"
WORKER = os.environ.get("DESK_CAMPUS_WORKER") or "https://tany.im"
TITLE = "地球キャンパス通信"
SUBTITLE = "通信制大学生が、世界を歩いて人類を考える"
MODEL = "claude-opus-5-5"
TEASER_MAX = 200
ID = re.compile(r"^[0-9a-f]{12}$")
HCM = timezone(timedelta(hours=7))
_lock = threading.Lock()


# ---------- storage ----------

def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def settings():
    s = _read(SETTINGS, {})
    return {"fromName": str(s.get("fromName") or TITLE), "address": str(s.get("address") or ""),
            "contact": str(s.get("contact") or ""), "testTo": str(s.get("testTo") or "")}


EMAIL = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[A-Za-z]{2,}$")


def save_settings(rec):
    s = settings()
    for k in ("fromName", "address", "contact", "testTo"):
        if k in rec:
            v = re.sub(r"\s+", " ", str(rec.get(k) or "")).strip()[:200]
            if k in ("contact", "testTo") and v and not EMAIL.match(v):
                return 400, {"error": f"{'Contact' if k == 'contact' else 'Test'} address doesn't look like an email address"}
            s[k] = v
    _write(SETTINGS, s)
    return 200, s


def _path(iid):
    return os.path.join(ISSUES, iid + ".json")


def get(iid):
    if not ID.match(iid or ""):
        return None
    d = _read(_path(iid), None)
    return d if d and not d.get("deleted") else None


def _all():
    out = []
    if os.path.isdir(ISSUES):
        for name in os.listdir(ISSUES):
            if name.endswith(".json") and ID.match(name[:-5]):
                d = _read(os.path.join(ISSUES, name), None)
                if d and not d.get("deleted"):
                    out.append(d)
    return out


def count(text):
    """Characters a reader reads (spaces and markdown marks left out) and minutes at about 500 a minute."""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")
    t = re.sub(r"\]\([^)]*\)", "", t)
    t = re.sub(r"[#>*_`\-\[\]\s]", "", t)
    n = len(t)
    return n, max(1, round(n / 500)) if n else 0


def summary(d):
    n, mins = count(d.get("body"))
    return {k: d.get(k) for k in ("id", "title", "status", "no", "series", "updatedAt", "createdAt", "sentAt", "sentCount", "testAt", "url")} | {"chars": n, "minutes": mins}


def view():
    items = sorted(_all(), key=lambda d: (d.get("status") == "sent", -(d.get("sentAt") or d.get("updatedAt") or 0)))
    s = settings()
    return {"issues": [summary(d) for d in items], "settings": s, "ready": ready(), "stats": stats(force=True) if key() else None,
            "nextNo": next_no(), "title": TITLE, "subtitle": SUBTITLE}


def ready():
    """What is missing before a send can work. Empty lists = ready."""
    s = settings()
    need = []
    if not key():
        need.append("worker")
    if not s["address"] or not s["contact"]:
        need.append("sender")
    if not site_news.ready():
        need.append("site")
    return {"send": not need, "test": bool(key() and s["testTo"]), "need": need}


def next_no():
    return max([d.get("no") or 0 for d in _all()] + [0]) + 1


def new():
    with _lock:
        iid = secrets.token_hex(6)
        now = int(time.time() * 1000)
        d = {"id": iid, "title": "", "body": "", "teaser": "", "series": "", "tags": [], "status": "draft",
             "createdAt": now, "updatedAt": now}
        _write(_path(iid), d)
    return 200, d


def save(iid, rec):
    """Words from the page. A sent issue keeps its title and body; only its teaser can still change."""
    with _lock:
        d = get(iid)
        if not d:
            return 404, {"error": "not found"}
        sent = d.get("status") == "sent"
        keys = ("teaser",) if sent else ("title", "body", "teaser", "series", "tags")
        for k in keys:
            if k not in rec:
                continue
            v = rec[k]
            if k == "tags":
                v = [re.sub(r"\s+", " ", str(x)).strip()[:30] for x in (v if isinstance(v, list) else str(v).split(",")) if str(x).strip()][:8]
            elif k == "body":
                v = str(v or "")[:200000]
            elif k == "teaser":
                v = str(v or "").strip()[:TEASER_MAX * 2]
            else:
                v = re.sub(r"\s+", " ", str(v or "")).strip()[:120]
            d[k] = v
        d["updatedAt"] = int(time.time() * 1000)
        _write(_path(iid), d)
    if sent and "teaser" in rec:
        err = publish_issue(d)
        if err:
            return 502, {"error": err, "issue": d}
    return 200, d


def remove(iid):
    with _lock:
        d = get(iid)
        if not d:
            return 404, {"error": "not found"}
        if d.get("status") == "sent":
            return 409, {"error": "A sent issue stays"}
        d["deleted"] = True
        d["updatedAt"] = int(time.time() * 1000)
        _write(_path(iid), d)
    return 200, {"ok": True}


# ---------- markdown -> HTML (email and preview) ----------

def _inline(s, pics):
    s = html.escape(s, quote=True)
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", lambda m: _img(m.group(2), m.group(1), pics), s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" style="color:#8A5A2B">\1</a>', s)
    s = re.sub(r"(?<![\"'>=])(https?://[^\s<]+)", r'<a href="\1" style="color:#8A5A2B">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _img(src, alt, pics):
    src = html.unescape(src)
    u = pics(src) if pics else src
    if not u:
        return ""
    return f'<img src="{html.escape(u)}" alt="{alt}" style="display:block;width:100%;max-width:600px;height:auto;border-radius:8px;margin:16px 0">'


def to_html(text, pics=None):
    """The issue's words as plain, email-safe HTML: ## headings, paragraphs, - lists, 1. lists, > quotes, --- rules,
    **bold**, *italic*, [links](https://), ![photos](/uploads/...). pics maps a photo's local path to its address."""
    out, block, kind = [], [], None
    P = 'style="margin:0 0 16px;line-height:1.9"'

    def flush():
        nonlocal block, kind
        if not block:
            return
        if kind == "ul":
            out.append('<ul style="margin:0 0 16px;padding-left:24px;line-height:1.9">' + "".join(f"<li>{_inline(x, pics)}</li>" for x in block) + "</ul>")
        elif kind == "ol":
            out.append('<ol style="margin:0 0 16px;padding-left:24px;line-height:1.9">' + "".join(f"<li>{_inline(x, pics)}</li>" for x in block) + "</ol>")
        elif kind == "quote":
            out.append('<blockquote style="margin:0 0 16px;padding:4px 16px;border-left:3px solid #C9A46A;color:#55524a;line-height:1.9">'
                       + "<br>".join(_inline(x, pics) for x in block) + "</blockquote>")
        else:
            out.append(f"<p {P}>" + "<br>".join(_inline(x, pics) for x in block) + "</p>")
        block, kind = [], None

    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.rstrip()
        h = re.match(r"^(#{1,4})\s+(.+)$", line)
        if h:
            flush()
            size = {1: 22, 2: 20, 3: 18, 4: 16}[len(h.group(1))]
            out.append(f'<h{min(len(h.group(1)) + 1, 4)} style="font-size:{size}px;margin:32px 0 12px;line-height:1.5">{_inline(h.group(2), pics)}</h{min(len(h.group(1)) + 1, 4)}>')
            continue
        if re.fullmatch(r"\s*(-{3,}|\*{3,})\s*", line):
            flush()
            out.append('<hr style="border:0;border-top:1px solid #ddd8cc;margin:24px 0">')
            continue
        if not line.strip():
            flush()
            continue
        for k, pat in (("ul", r"^\s*[-*・]\s+(.*)$"), ("ol", r"^\s*\d+[.)]\s+(.*)$"), ("quote", r"^\s*>\s?(.*)$")):
            m = re.match(pat, line)
            if m:
                if kind != k:
                    flush()
                    kind = k
                block.append(m.group(1))
                break
        else:
            if kind not in (None, "p"):
                flush()
            kind = "p"
            block.append(line.strip())
    flush()
    return "\n".join(out)


def to_text(text):
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"^#{1,4}\s+", "", t, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def photos(d):
    """Local photos the issue uses, in order: [(markdown src, file path, published name)]."""
    out = []
    for m in re.finditer(r"!\[[^\]]*\]\((/uploads/([A-Za-z0-9._-]+))\)", d.get("body") or ""):
        p = os.path.join(UPLOADS, m.group(2))
        if os.path.isfile(p) and all(o[0] != m.group(1) for o in out):
            out.append((m.group(1), p, f"{len(out) + 1}{os.path.splitext(p)[1].lower() or '.jpg'}"))
    return out


def subject(d, no=None):
    return f"{TITLE} #{no or d.get('no') or next_no()} {d.get('title') or ''}".strip()


def footer_html(s):
    e = html.escape
    contact = f'<a href="mailto:{e(s["contact"])}" style="color:#8A5A2B">{e(s["contact"])}</a>' if s["contact"] else ""
    return ('<hr style="border:0;border-top:1px solid #ddd8cc;margin:32px 0 16px">'
            '<p style="margin:0 0 8px;font-size:12px;color:#77736a;line-height:1.7">'
            f'このメールは「{TITLE}」に登録した方にお送りしています。<br>'
            f'発行：{e(s["fromName"])}<br>{e(s["address"])}<br>お問い合わせ：{contact}<br>'
            f'過去の号：<a href="{site_news.BASE}/campus/" style="color:#8A5A2B">{site_news.BASE}/campus/</a></p>'
            '<p style="margin:0;font-size:12px;color:#77736a">配信の停止：<a href="{{STOP}}" style="color:#8A5A2B">ここを押すと止まります</a></p>')


def footer_text(s):
    return (f"\n\n――\nこのメールは「{TITLE}」に登録した方にお送りしています。\n発行：{s['fromName']}\n{s['address']}\n"
            f"お問い合わせ：{s['contact']}\n過去の号：{site_news.BASE}/campus/\n配信の停止：{{{{STOP}}}}\n")


def email(d, no, pics=None):
    """(subject, html, text). {{STOP}} is filled by the Worker with each reader's own stop link."""
    s = settings()
    e = html.escape
    title = e(d.get("title") or "")
    series = f'<p style="margin:0 0 4px;font-size:12px;color:#8A5A2B">{e(d["series"])}</p>' if d.get("series") else ""
    body = (f'<div style="background:#F6F5F0;padding:24px 12px"><div style="max-width:600px;margin:0 auto;background:#fff;'
            f'border-radius:12px;padding:32px 24px;font-family:-apple-system,BlinkMacSystemFont,\'Hiragino Sans\',\'Noto Sans JP\',sans-serif;'
            f'font-size:16px;color:#1B1B18">'
            f'<p style="margin:0 0 24px;font-size:12px;color:#77736a">{TITLE} #{no}　{SUBTITLE}</p>'
            f'{series}<h1 style="font-size:24px;line-height:1.5;margin:0 0 24px">{title}</h1>'
            f'{to_html(d.get("body"), pics)}{footer_html(s)}</div></div>')
    page = f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head><body style="margin:0">{body}</body></html>'
    text = f"{TITLE} #{no}\n\n{d.get('title') or ''}\n\n{to_text(d.get('body'))}{footer_text(s)}"
    return subject(d, no), page, text


def preview(iid):
    d = get(iid)
    if not d:
        return None
    no = d.get("no") or next_no()
    _, page, _ = email(d, no)
    return page.replace("{{STOP}}", "#")


# ---------- tany.im pages ----------

def _shell(title, desc, main, depth, canonical, image=None):
    """A page in the site's own look: the head, header and footer of a hand-written article, with our main part."""
    t = site_news._template()
    e = html.escape
    t = re.sub(r"<title>.*?</title>", f"<title>{e(title)}</title>", t, count=1, flags=re.S)
    for prop, val in (('name="description"', desc), ('property="og:title"', title), ('property="og:description"', desc)):
        t = re.sub(rf'(<meta {prop} content=")[^"]*', lambda m: m.group(1) + e(val), t, count=1)
    t = re.sub(r'(<meta property="og:image" content=")[^"]*', lambda m: m.group(1) + e(image or f"{site_news.BASE}/og.png"), t, count=1)
    if "rel=\"canonical\"" not in t:
        t = t.replace("</head>", f'<link rel="canonical" href="{e(canonical)}">\n</head>', 1)
    up = "../" * depth
    t = t.replace('href="../../', f'href="{up}').replace('src="../../', f'src="{up}')
    return re.sub(r"<main id=\"top\">.*?</main>", lambda m: f'<main id="top">\n{main}\n</main>', t, count=1, flags=re.S)


CSS = """<style>
.cp-form{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0 8px}
.cp-form input[type=email]{flex:1 1 220px;min-width:0;font:inherit;font-size:16px;padding:12px 14px;border:1px solid #c9c3b4;border-radius:8px;background:#fff;color:#1B1B18}
.cp-form button{font:inherit;font-size:16px;font-weight:700;padding:12px 20px;border:0;border-radius:8px;background:#8A5A2B;color:#fff;cursor:pointer}
.cp-form .hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
.cp-note{font-size:13px;color:#6d685e;margin:0 0 24px}
.cp-list{list-style:none;padding:0;margin:0}
.cp-list li{border-top:1px solid #e3ddcf;padding:16px 0}
.cp-list a{text-decoration:none;color:inherit;display:block}
.cp-list time{font-size:13px;color:#6d685e}
.cp-list .t{display:block;font-weight:700;margin:4px 0}
.cp-list .s{display:block;font-size:14px;color:#55524a;line-height:1.8}
.article ul.cp-topics{display:flex;flex-wrap:wrap;gap:8px;list-style:none;padding:0;margin:8px 0 24px}
.article ul.cp-topics li{border:1px solid #d8d1c0;border-radius:999px;padding:4px 12px;font-size:14px;margin:0;width:auto;list-style:none}
.article ul.cp-topics li::before,.article ul.cp-list li::before{content:none}
.article ul.cp-list{list-style:none;padding:0;margin:0}
.article ul.cp-list li{margin:0;padding:16px 0}
.cp-box{border:1px solid #e3ddcf;border-radius:12px;padding:24px 16px;margin:32px 0;background:#fffdf8}
</style>"""


def _form(where):
    return (f'<form class="cp-form" method="post" action="/campus/subscribe">'
            f'<label class="hp" aria-hidden="true">Website<input name="website" tabindex="-1" autocomplete="off"></label>'
            f'<input type="hidden" name="from" value="{html.escape(where)}">'
            f'<input type="email" name="email" required autocomplete="email" placeholder="メールアドレス" aria-label="メールアドレス">'
            f'<button type="submit">無料で登録する</button></form>'
            f'<p class="cp-note">確認のメールが届きます。中のボタンを押すと登録が終わります。配信はいつでも止められます。</p>')


TOPICS = ("人類の進歩と文明", "理想の社会と平和", "文学と言語", "旅先の日々", "お金", "出会いと別れ", "文化の違い", "食べたもの")


def _sent():
    return sorted([d for d in _all() if d.get("status") == "sent"], key=lambda d: -(d.get("no") or 0))


def _teaser(d):
    t = (d.get("teaser") or "").strip()
    if not t:
        plain = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1", d.get("body") or "")
        plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", plain)
        plain = re.sub(r"^\s*(?:(?:#{1,4}|[-*・]|\d+[.)])\s+|>\s?)", "", plain, flags=re.M)
        plain = re.sub(r"\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`", lambda m: m.group(1) or m.group(2) or m.group(3), plain)
        t = re.sub(r"\s+", " ", plain).strip()
        t = t[:TEASER_MAX].rstrip() + ("…" if len(t) > TEASER_MAX else "")
    return t[:TEASER_MAX * 2]


def _date(ms):
    return datetime.fromtimestamp((ms or 0) / 1000, HCM).strftime("%Y-%m-%d")


def landing_page():
    e = html.escape
    rows = "".join(
        f'<li><a href="{d["no"]}/"><time datetime="{_date(d.get("sentAt"))}">{_date(d.get("sentAt")).replace("-", ".")}　#{d["no"]}</time>'
        f'<span class="t">{e(d.get("title") or "")}</span><span class="s">{e(_teaser(d))}</span></a></li>' for d in _sent())
    latest = ""
    if rows:
        latest = f'<h2 style="font-size:20px;margin:40px 0 8px">これまでの号</h2><ul class="cp-list">{rows}</ul>'
    topics = "".join(f"<li>{t}</li>" for t in TOPICS)
    main = f"""{CSS}
  <section class="band page" style="background:var(--paper)">
    <div class="inner">
      <p class="meta">無料のメールマガジン　週1回</p>
      <h1>{TITLE}</h1>
      <p style="font-size:18px;margin:0 0 8px">{SUBTITLE}</p>
      <div class="article">
<p>通信制大学に在籍しながら、世界のどこかにいます。歩いた国で見たもの、読んだもの、話した人から、人類はどこまで来たのか、理想の社会や平和はどんな形なのかを考えて書きます。日々の暮らしや、考えたこと、悩んだことも、そのまま書きます。</p>
{_form("landing")}
<h2 style="font-size:20px;margin:40px 0 8px">こんなことが届きます</h2>
<ul class="cp-topics">{topics}</ul>
<p>週に1回、日本語でお届けします。登録も購読も無料です。各号のあらすじは、このページでも読めます。</p>
{latest}
<div class="cp-box"><p style="margin:0 0 8px;font-weight:700">続きはメールで読めます</p>{_form("landing-bottom")}</div>
      </div>
      <p class="back"><a href="../">Tany のトップへ</a></p>
    </div>
  </section>"""
    return _shell(f"{TITLE} | 無料のメールマガジン", f"{SUBTITLE}。週1回、無料でお届けする日本語のメールマガジンです。", main, 1, f"{site_news.BASE}/campus/")


def issue_page(d, pics=()):
    e = html.escape
    no = d["no"]
    date = _date(d.get("sentAt"))
    img = f'<p><img src="{e(pics[0])}" alt="" style="width:100%;height:auto;border-radius:8px"></p>' if pics else ""
    series = f'<span class="tag">{e(d["series"])}</span>' if d.get("series") else ""
    main = f"""{CSS}
  <section class="band page" style="background:var(--paper)">
    <div class="inner">
      <p class="meta"><time datetime="{date}">{date.replace("-", ".")}</time>　{TITLE} #{no}　{series}</p>
      <h1>{e(d.get("title") or "")}</h1>
      <div class="article">
{img}<p>{e(_teaser(d))}</p>
<div class="cp-box"><p style="margin:0 0 8px;font-weight:700">この号の続きは、メールで読めます</p><p style="margin:0">{TITLE}は、週1回届く無料のメールマガジンです。登録すると、次の号から届きます。</p>{_form(f"issue-{no}")}</div>
      </div>
      <p class="back"><a href="../">{TITLE}のページへ</a></p>
    </div>
  </section>"""
    image = f"{site_news.BASE}/campus/{no}/{pics[0]}" if pics else None
    return _shell(f"{d.get('title') or ''} | {TITLE} #{no}", _teaser(d)[:110], main, 2, f"{site_news.BASE}/campus/{no}/", image)


def thanks_page():
    main = f"""{CSS}
  <section class="band page" style="background:var(--paper)">
    <div class="inner">
      <h1>確認のメールを送りました</h1>
      <div class="article">
<p>届いたメールの中の「登録を確かめる」を押すと、登録が終わります。</p>
<p>数分たっても届かないときは、迷惑メールのフォルダも見てください。</p>
      </div>
      <p class="back"><a href="../">{TITLE}のページへ</a></p>
    </div>
  </section>"""
    return _shell(f"確認のメールを送りました | {TITLE}", "登録の確認メールを送りました。", main, 2, f"{site_news.BASE}/campus/thanks/")


CAMPUS = os.path.join(site_news.PUBLIC, "campus")


def _put(rel, src):
    p = os.path.join(CAMPUS, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)


def _sitemap():
    p = os.path.join(site_news.PUBLIC, "sitemap.xml")
    try:
        with open(p, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return
    src = re.sub(r"\s*<url><loc>https://tany\.im/campus/[^<]*</loc>.*?</url>", "", src)
    today = datetime.now(HCM).strftime("%Y-%m-%d")
    lines = [f"  <url><loc>{site_news.BASE}/campus/</loc><lastmod>{today}</lastmod></url>"]
    lines += [f"  <url><loc>{site_news.BASE}/campus/{d['no']}/</loc><lastmod>{_date(d.get('sentAt'))}</lastmod></url>" for d in _sent()]
    src = src.replace("</urlset>", "\n".join(lines) + "\n</urlset>")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)


def write_pages(d=None):
    """The landing page, the thanks page and (with d) one issue's page with its photos. Nothing is deployed here."""
    if not site_news.ready():
        raise RuntimeError("The tany.im site files are not on this Mac")
    pics = []
    if d:
        folder = os.path.join(CAMPUS, str(d["no"]))
        os.makedirs(folder, exist_ok=True)
        for src, path, name in photos(d):
            shutil.copyfile(path, os.path.join(folder, name))
            pics.append(name)
        _put(f"{d['no']}/index.html", issue_page(d, pics))
    _put("index.html", landing_page())
    _put("thanks/index.html", thanks_page())
    _sitemap()
    return pics


def publish_issue(d):
    """Writes the pages and deploys. Returns '' or the reason."""
    try:
        with site_news._lock:
            write_pages(d)
            return site_news.deploy()
    except (OSError, RuntimeError) as e:
        return str(e)


def publish_landing():
    try:
        with site_news._lock:
            write_pages()
            return site_news.deploy()
    except (OSError, RuntimeError) as e:
        return str(e)


# ---------- the Worker ----------

def key():
    v = os.environ.get("DESK_CAMPUS_KEY")
    if v is not None:
        return v
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def worker(method, path, body=None, timeout=60):
    k = key()
    if not k:
        raise RuntimeError("The newsletter isn't connected yet (keychain: tatsuki-desk-campus)")
    req = urllib.request.Request(WORKER + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + k, "Content-Type": "application/json", "User-Agent": "TatsukiDesk"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except (ValueError, AttributeError):
            msg = ""
        raise RuntimeError(msg or f"The newsletter server answered {e.code}") from None
    except (OSError, ValueError):
        raise RuntimeError("Could not reach tany.im. Check the connection and try again") from None


_stats = {"at": 0, "v": None}


def stats_cached():
    return _stats["v"]


def stats(force=False):
    if not force and _stats["v"] and time.time() - _stats["at"] < 300:
        return _stats["v"]
    try:
        v = worker("GET", "/campus/api/stats", timeout=20)
    except RuntimeError as e:
        return {"error": str(e)}
    _stats.update(at=time.time(), v=v)
    return v


def send_test(iid):
    d = get(iid)
    if not d:
        return 404, {"error": "not found"}
    s = settings()
    if not s["testTo"]:
        return 400, {"error": "Add a test address in Settings first"}
    if not (d.get("title") or "").strip() or not (d.get("body") or "").strip():
        return 400, {"error": "Write a title and some text first"}
    no = d.get("no") or next_no()
    # photos in a test come from the Mac, so they show only on this Mac; the real send uses tany.im
    subj, page, text = email(d, no, pics=lambda src: None)
    try:
        r = worker("POST", "/campus/api/send", {"test": True, "to": s["testTo"], "subject": "[Test] " + subj, "html": page, "text": text})
    except RuntimeError as e:
        return 502, {"error": str(e)}
    with _lock:
        d = get(iid)
        d["testAt"] = int(time.time() * 1000)
        _write(_path(iid), d)
    return 200, {"ok": True, "to": s["testTo"], "result": r}


def send(iid, expect):
    """Publishes the teaser page, then asks the Worker to send. expect = the reader count the person confirmed."""
    d = get(iid)
    if not d:
        return 404, {"error": "not found"}
    rd = ready()
    if not rd["send"]:
        return 400, {"error": "Not ready to send: " + ", ".join(rd["need"])}
    if not (d.get("title") or "").strip() or not (d.get("body") or "").strip():
        return 400, {"error": "Write a title and some text first"}
    st = stats(force=True)
    if st.get("error"):
        return 502, {"error": st["error"]}
    if d.get("status") != "sent" and int(expect or -1) != int(st.get("active") or 0):
        return 409, {"error": "The reader count changed. Check it again", "stats": st}
    with _lock:
        d = get(iid)
        if d.get("status") != "sent":
            d["no"] = d.get("no") or next_no()
            d["status"] = "sent"
            d["sentAt"] = int(time.time() * 1000)
            _write(_path(iid), d)
    err = publish_issue(d)
    if err:
        return 502, {"error": "The page on tany.im did not go up: " + err, "issue": d}
    base = f"{site_news.BASE}/campus/{d['no']}/"
    names = {src: base + name for src, _, name in photos(d)}
    subj, page, text = email(d, d["no"], pics=lambda src: names.get(src))
    try:
        r = worker("POST", "/campus/api/send", {"no": d["no"], "subject": subj, "html": page, "text": text}, timeout=120)
    except RuntimeError as e:
        return 502, {"error": str(e), "issue": d}
    with _lock:
        d = get(iid)
        d["sentCount"] = int(r.get("sent") or 0) + int(r.get("before") or 0)
        d["pending"] = int(r.get("pending") or 0)
        d["url"] = base
        _write(_path(iid), d)
    stats(force=True)
    return 200, {"issue": d, "result": r}


# ---------- Tidy up (Claude) ----------

TIDY_SYSTEM = """You tidy a Japanese newsletter issue written quickly by its author, a correspondence-course university student travelling the world.
Rules:
- Keep the author's words, voice, opinions and order. Do not add ideas, facts, numbers, names or conclusions. Do not soften or strengthen claims.
- Only: fix typos and obvious slips, split very long paragraphs, add short "## " headings where the text changes topic (at most one per 600 characters), and fix markdown.
- Keep every photo line ![...](...) and every link exactly as written.
- Keep です・ます or だ・である as the author used it; do not mix them.
- Teaser: up to 200 Japanese characters, in the author's voice, that makes a reader want the rest. No exaggeration, no clickbait, no emoji. Do not reveal the ending.
- notes: up to 5 short notes in Japanese on what you changed."""

TIDY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["body", "teaser", "notes"],
               "properties": {"body": {"type": "string"}, "teaser": {"type": "string"},
                              "notes": {"type": "array", "items": {"type": "string"}}}}


def tidy(iid):
    """A suggestion only: nothing is saved until the page sends the accepted words back."""
    d = get(iid)
    if not d:
        return 404, {"error": "not found"}
    if not (d.get("body") or "").strip():
        return 400, {"error": "Write something first"}
    k = lifelog.api_key()
    if not k:
        return 400, {"error": "No Anthropic API key is registered on this Mac"}
    body = {"model": MODEL, "max_tokens": 16000, "system": TIDY_SYSTEM,
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": TIDY_SCHEMA}},
            "messages": [{"role": "user", "content": f"Title: {d.get('title') or ''}\n\n---\n{d['body']}"}]}
    req = urllib.request.Request(lifelog.API, data=json.dumps(body).encode(),
                                 headers={"x-api-key": k, "anthropic-version": lifelog.API_VERSION, "content-type": "application/json", "user-agent": "tatsuki-desk"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        return 502, {"error": f"Anthropic returned an error ({e.code}). Try again in a moment"}
    except (OSError, ValueError):
        return 502, {"error": "Could not reach Anthropic. Check the connection and try again"}
    text = next((b.get("text") for b in res.get("content", []) if b.get("type") == "text"), None)
    try:
        out = json.loads(text)
    except (TypeError, ValueError):
        return 502, {"error": "The suggestion came back unreadable. Try again"}
    # photos and links must survive: if any went missing, keep the author's text
    keep = re.findall(r"!\[[^\]]*\]\([^)]*\)|https?://[^\s)]+", d["body"])
    lost = [x for x in keep if x not in out.get("body", "")]
    if lost:
        return 502, {"error": "The suggestion dropped a photo or link, so it was not used. Try again"}
    return 200, {"body": out["body"], "teaser": out["teaser"][:TEASER_MAX], "notes": out.get("notes", [])[:5]}


# ---------- requests from server.py ----------

def handle(method, parts, rec):
    """parts: the path after /api/campus/. Returns (status, body)."""
    if method == "GET" and not parts:
        return 200, view()
    if method == "GET" and parts == ["stats"]:
        return 200, stats(force=True)
    if method == "PUT" and parts == ["settings"]:
        return save_settings(rec)
    if method == "POST" and parts == ["publish-landing"]:
        err = publish_landing()
        return (502, {"error": err}) if err else (200, {"url": f"{site_news.BASE}/campus/"})
    if method == "POST" and parts == ["issues"]:
        return new()
    if len(parts) >= 2 and parts[0] == "issues":
        iid = parts[1]
        if method == "GET" and len(parts) == 2:
            d = get(iid)
            return (200, d) if d else (404, {"error": "not found"})
        if method == "PUT" and len(parts) == 2:
            return save(iid, rec)
        if method == "POST" and parts[2:] == ["remove"]:
            return remove(iid)
        if method == "POST" and parts[2:] == ["tidy"]:
            return tidy(iid)
        if method == "POST" and parts[2:] == ["test"]:
            return send_test(iid)
        if method == "POST" and parts[2:] == ["send"]:
            return send(iid, rec.get("expect"))
    return 404, {"error": "not found"}


if __name__ == "__main__":
    print(json.dumps(view(), ensure_ascii=False, indent=1))
