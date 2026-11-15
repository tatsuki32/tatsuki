#!/usr/bin/env python3
"""Tatsuki's Desk — Post Studio's company-site news, published to tany.im/news/ from Desk.

The site's files live in desk/data/tany/site (outside git). Publishing writes one article page in the site's own style,
adds it to the news list (and the newest three on the top page) and the sitemap, then runs `npx wrangler deploy`
there. Only what the person pressed Publish on goes out, with the words as they approved them. Unpublish takes the
page, its list line and its sitemap line away and deploys again. Articles written by hand on the site are kept as
they are: only lines whose link is a Desk article are added or removed.
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
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("DESK_TANY_SITE") or os.path.join(ROOT, "data", "tany", "site")
PUBLIC = os.path.join(SITE, "public")
NEWS = os.path.join(PUBLIC, "news")
BASE = "https://tany.im"
TAGS = ("お知らせ", "メディア", "事業", "イベント")
SLUG = re.compile(r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{4}$")
DEPLOY = os.environ.get("DESK_TANY_DEPLOY")  # a test sets this to "echo" so nothing goes out
_lock = threading.Lock()
LI = re.compile(r'[ \t]*<li><a href="(?P<href>[^"]+)"><time datetime="(?P<date>[\d-]+)">[^<]*</time><span><span class="tag">(?P<tag>[^<]*)</span><span class="nt">(?P<title>[^<]*)</span></span></a></li>\n?')


def ready():
    return os.path.exists(os.path.join(NEWS, "index.html")) and os.path.exists(os.path.join(SITE, "wrangler.jsonc"))


def _template():
    """The site's own article page with the parts that change cut out. Taken from an article written by hand."""
    for name in sorted(os.listdir(NEWS)):
        p = os.path.join(NEWS, name, "index.html")
        if os.path.isdir(os.path.join(NEWS, name)) and not SLUG.match(name) and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    raise RuntimeError("No article on the site to take the page's style from")


def _paragraphs(text):
    """The approved words as the site's article HTML: paragraphs, "- " lists, and links as the site's link buttons."""
    out, items = [], []

    def flush():
        if items:
            out.append("<ul>\n" + "\n".join(f"<li>{x}</li>" for x in items) + "\n</ul>")
            items.clear()

    def inline(s):
        s = html.escape(s)
        return re.sub(r"https?://[^\s<>&]+", lambda m: f'<a href="{m.group(0)}" target="_blank" rel="noopener">{m.group(0)}</a>', s)

    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        head = re.match(r"^#{2,4}\s+(.+)$", line)
        if head:  # "## A heading" from the draft
            flush()
            out.append(f'<h2 style="font-size:20px;margin:40px 0 16px">{inline(head.group(1))}</h2>')
            continue
        if not line or line.startswith("#"):  # hashtags and empty lines end a list; hashtag lines are not for the site
            flush()
            continue
        if re.match(r"^[-・•]\s*", line):
            items.append(inline(re.sub(r"^[-・•]\s*", "", line)))
            continue
        flush()
        only = re.fullmatch(r"https?://\S+", line)
        if only:
            u = html.escape(line)
            out.append(f'<p><a class="go" href="{u}" target="_blank" rel="noopener">リンクを開く<span class="arr" aria-hidden="true">↗</span></a></p>')
        else:
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return "\n".join(out)


def page(title, date, tag, text, pictures=(), pdf=None, preview=False):
    """One article page. pictures are file names next to it; pdf is (file name, label)."""
    t = _template()
    desc = re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", text)).strip()[:110]
    e = html.escape
    t = re.sub(r"<title>.*?</title>", f"<title>{e(title)} | Tany</title>", t, count=1, flags=re.S)
    t = re.sub(r'(<meta name="description" content=")[^"]*', lambda m: m.group(1) + e(desc), t, count=1)
    t = re.sub(r'(<meta property="og:title" content=")[^"]*', lambda m: m.group(1) + e(title), t, count=1)
    t = re.sub(r'(<meta property="og:description" content=")[^"]*', lambda m: m.group(1) + e(desc), t, count=1)
    if pictures and not preview:
        t = re.sub(r'(<meta property="og:image" content=")[^"]*', lambda m: m.group(1) + "__OGIMAGE__", t, count=1)
    body = ""
    if pictures:
        body += f'<p><img src="{e(pictures[0])}" alt="" style="width:100%;height:auto;border-radius:8px"></p>\n'
    body += _paragraphs(text)
    for p in pictures[1:]:
        body += f'\n<p><img src="{e(p)}" alt="" style="width:100%;height:auto;border-radius:8px"></p>'
    if pdf:
        body += f'\n<p><a class="go" href="{e(pdf[0])}" target="_blank" rel="noopener">{e(pdf[1])}<span class="arr" aria-hidden="true">↗</span></a></p>'
    d = datetime.strptime(date, "%Y-%m-%d")
    meta = f'<p class="meta"><time datetime="{date}">{d:%Y.%m.%d}</time>　<span class="tag">{e(tag)}</span></p>'
    t = re.sub(r'<p class="meta">.*?</p>', lambda m: meta, t, count=1, flags=re.S)
    t = re.sub(r"<h1>.*?</h1>", lambda m: f"<h1>{e(title)}</h1>", t, count=1, flags=re.S)
    t = re.sub(r'(<div class="article">\n?).*?(\n?\s*</div>\s*<p class="back">)', lambda m: m.group(1) + body + m.group(2), t, count=1, flags=re.S)
    if preview:  # seen inside Desk: the site's style from tany.im, no visit counting
        t = t.replace('<script src="/t.js" defer></script>', "").replace('href="../../', f'href="{BASE}/')
    return t


def _set_list(path, href, entry, keep):
    """Adds or removes one line of a news list (keep: how many lines to keep, 0 for all)."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'(<ul class="news">\n)(.*?)(\s*</ul>)', src, flags=re.S)
    if not m:
        raise RuntimeError(f"No news list in {os.path.relpath(path, PUBLIC)}")
    rows = [x.groupdict() for x in LI.finditer(m.group(2))]
    rows = [r for r in rows if r["href"].rstrip("/").split("/")[-1] != href.rstrip("/").split("/")[-1]]
    if entry:
        rows.append(entry)
    rows.sort(key=lambda r: r["date"], reverse=True)
    if keep:
        rows = rows[:keep]
    prefix = "../news/" if path.endswith(os.path.join("news", "index.html")) else "news/"
    lines = "\n".join(
        f'        <li><a href="{prefix}{r["href"].rstrip("/").split("/")[-1]}/"><time datetime="{r["date"]}">{r["date"].replace("-", ".")}</time>'
        f'<span><span class="tag">{r["tag"]}</span><span class="nt">{r["title"]}</span></span></a></li>' for r in rows)
    src = src[:m.start()] + m.group(1) + lines + m.group(3) + src[m.end():]
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)


def _all_rows():
    with open(os.path.join(NEWS, "index.html"), encoding="utf-8") as f:
        m = re.search(r'<ul class="news">\n(.*?)\s*</ul>', f.read(), flags=re.S)
    return [x.groupdict() for x in LI.finditer(m.group(1))] if m else []


def _set_sitemap(slug, on, date):
    p = os.path.join(PUBLIC, "sitemap.xml")
    with open(p, encoding="utf-8") as f:
        src = f.read()
    loc = f"{BASE}/news/{slug}/"
    src = re.sub(rf"\s*<url><loc>{re.escape(loc)}</loc>.*?</url>", "", src)
    src = re.sub(r"(<url><loc>https://tany\.im/news/</loc><lastmod>)[^<]*", lambda m: m.group(1) + date, src)
    if on:
        src = src.replace("</urlset>", f"  <url><loc>{loc}</loc><lastmod>{date}</lastmod></url>\n</urlset>")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)


def deploy():
    """npx wrangler deploy in the site's folder. Returns '' or the reason it failed."""
    if DEPLOY == "echo":
        return ""
    npx = shutil.which("npx") or ("/usr/local/bin/npx" if os.path.exists("/usr/local/bin/npx") else "/opt/homebrew/bin/npx")
    env = {**os.environ, "PATH": os.pathsep.join([os.path.dirname(npx), "/usr/local/bin", "/opt/homebrew/bin", os.environ.get("PATH", "/usr/bin:/bin")])}
    try:
        r = subprocess.run([npx, "--yes", "wrangler", "deploy"], cwd=SITE, env=env, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return f"Could not run wrangler: {e}"
    if r.returncode:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        return "Deploy failed: " + " ".join(tail)[:300]
    return ""


def publish(slug, title, date, tag, text, pictures=(), pdf=None):
    """pictures: local JPEG paths; pdf: (local path, file name). Returns (url, error)."""
    if not SLUG.match(slug):
        return "", "bad slug"
    tag = tag if tag in TAGS else TAGS[0]
    with _lock:
        folder = os.path.join(NEWS, slug)
        os.makedirs(folder, exist_ok=True)
        for old in os.listdir(folder):
            os.remove(os.path.join(folder, old))
        names = []
        for i, p in enumerate(pictures[:10]):
            names.append(f"{i + 1}.jpg")
            shutil.copyfile(p, os.path.join(folder, names[-1]))
        pdf_link = None
        if pdf:
            fname = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.splitext(pdf[1])[0]).strip("-.")[:60] or "document"
            shutil.copyfile(pdf[0], os.path.join(folder, fname + ".pdf"))
            pdf_link = (fname + ".pdf", "PDF を読む")
        src = page(title, date, tag, text, names, pdf_link)
        src = src.replace("__OGIMAGE__", f"{BASE}/news/{slug}/{names[0]}" if names else f"{BASE}/og.png")
        with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
            f.write(src)
        entry = {"href": f"news/{slug}/", "date": date, "tag": html.escape(tag), "title": html.escape(title)}
        _set_list(os.path.join(NEWS, "index.html"), slug, entry, 0)
        _set_list(os.path.join(PUBLIC, "index.html"), slug, None, 0)
        top = _all_rows()[:3]
        for r in top:
            _set_list(os.path.join(PUBLIC, "index.html"), r["href"], r, 3)
        _set_sitemap(slug, True, date)
        err = deploy()
    return ("" if err else f"{BASE}/news/{slug}/"), err


def unpublish(slug):
    if not SLUG.match(slug):
        return "bad slug"
    with _lock:
        shutil.rmtree(os.path.join(NEWS, slug), ignore_errors=True)
        _set_list(os.path.join(NEWS, "index.html"), slug, None, 0)
        _set_list(os.path.join(PUBLIC, "index.html"), slug, None, 0)
        for r in _all_rows()[:3]:
            _set_list(os.path.join(PUBLIC, "index.html"), r["href"], r, 3)
        _set_sitemap(slug, False, datetime.now().strftime("%Y-%m-%d"))
        return deploy()


def new_slug(date):
    return f"{date}-{secrets.token_hex(2)}"


if __name__ == "__main__":
    print(json.dumps({"ready": ready(), "rows": _all_rows()}, ensure_ascii=False, indent=1))
