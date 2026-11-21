"""Desk Reader: books kept on this Mac, read aloud by this Mac.

A book is desk/data/reader/books/<id>.json: title, author, lang, chapters [{title, paras[]}], pos {ch, para} (where to continue),
addedAt, lastReadAt, words. The original file sits next to it (books/<id>.<ext>). Nothing here leaves the Mac: the text is never
sent anywhere, and the folder stays out of the GitHub copy of the data (books are copyrighted).

Reading in: pasted text, .txt, .epub (the chapters of the spine) and PDFs with text (a small Swift helper, app/pdftext.swift,
built the first time it is needed). Bought e-books with a lock cannot be read.

Reading aloud: the page uses the browser's voices first. When the Desk window has none, POST /api/reader/speak asks macOS `say`
for the sentence (an .m4a that is played and dropped).

Tests: python3 -m unittest desk.tests.test_reader
"""
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_READER_DIR") or os.path.join(ROOT, "data", "reader")
BOOKS = os.path.join(DIR, "books")
BOOK_MAX = 50 * 1024 * 1024
TEXT_MAX = 3_000_000  # characters kept per book
PDFTEXT_SOURCE = os.path.join(ROOT, "app", "pdftext.swift")
PDFTEXT = os.path.expanduser("~/Library/Application Support/TatsukiDesk/pdftext")
SPEAK_MAX = 1200
VOICE_LANG = {"en": "en_", "ja": "ja_", "ru": "ru_", "es": "es_", "fr": "fr_", "de": "de_", "vi": "vi_", "zh": "zh_", "ko": "ko_", "it": "it_", "pt": "pt_"}
_voices = None


def now_ms():
    return int(time.time() * 1000)


def _path(book_id):
    if not re.fullmatch(r"[a-z0-9]{6,40}", book_id or ""):
        raise ValueError("bad id")
    return os.path.join(BOOKS, book_id + ".json")


def _write(path, rec):
    os.makedirs(BOOKS, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, path)


def read_book(book_id):
    try:
        with open(_path(book_id), encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError):
        return None
    return {**v, "id": book_id} if isinstance(v, dict) and not v.get("deleted") else None


def meta(book):
    chapters = book.get("chapters") or []
    total = sum(len(c.get("paras") or []) for c in chapters)
    pos = book.get("pos") or {"ch": 0, "para": 0}
    before = sum(len(c.get("paras") or []) for c in chapters[: int(pos.get("ch") or 0)]) + int(pos.get("para") or 0)
    return {"id": book["id"], "title": book.get("title") or "Untitled", "author": book.get("author") or "", "lang": book.get("lang") or "en",
            "chapters": [{"title": c.get("title") or "", "n": len(c.get("paras") or [])} for c in chapters], "paras": total,
            "pos": pos, "progress": round(before / total, 3) if total else 0, "addedAt": book.get("addedAt") or 0,
            "lastReadAt": book.get("lastReadAt") or 0, "words": book.get("words") or 0, "file": book.get("file") or ""}


def list_books():
    out = []
    for name in sorted(os.listdir(BOOKS)) if os.path.isdir(BOOKS) else []:
        if name.endswith(".json") and not name.startswith("."):
            b = read_book(name[:-5])
            if b:
                out.append(meta(b))
    return sorted(out, key=lambda m: -(m["lastReadAt"] or m["addedAt"]))


# ---------- language ----------

STOP = {
    "en": "the and of to in is that it for was with as his on be at by this had not are but from or have an they which one you were her all she there would their we him been has when who will more no if out so said what up its about into than them can only other new some could time these two may then do first any my now such like our over man me even most made after also did many before must through back years where much your way well down should because each just those people how too little state good very make world still own see men work long get here between both life being under never day same another know while last might us great old year off come since against go came right used take three",
    "es": "de la que el en y a los se del las un por con no una su para es al lo como más pero sus le ya o fue este ha sí porque esta son entre cuando muy sin sobre ser tiene también me hasta hay donde han quien están estado desde todo nos durante estados todos uno les ni contra otros fueron ese eso había ante ellos e esto mí antes algunos qué unos yo otro otras otra él tanto esa estos mucho quienes nada muchos cual sea poco ella estar haber estas estaba estamos algunas algo nosotros",
    "fr": "de la le et les des en un du une que est pour qui dans a par plus pas au sur ne se ce il sont son avec ou être cette aux ses mais comme on tout nous leur été vous fait elle sa ont deux ces même aussi bien où y très sans peut dont entre encore faire ils autres cela ans était après avait depuis dit tous toute autre mon moi lui ma mes",
    "de": "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine als auch es an werden aus er hat dass sie nach wird bei einer um am sind noch wie einem über einen so zum war haben nur oder aber vor zur bis mehr durch man sein wurde sei ich du wir ihr euch mir mich dir dich uns ihnen ihm ihn",
    "it": "di che e la il un a per è in una sono mi ho ma lo ha le si ti con cosa io come non da questo qui del al ci anche sei bene più della me se gli tu nel solo quando era sua suo molto può ancora essere tutto tutti noi loro nella delle dei dal ne lei lui così dove perché",
    "pt": "de a o que e do da em um para é com não uma os no se na por mais as dos como mas foi ao ele das tem à seu sua ou ser quando muito há nos já está eu também só pelo pela até isso ela entre era depois sem mesmo aos ter seus quem nas me esse eles estão você tinha foram essa num nem suas meu às minha têm numa pelos elas havia seja qual será nós tenho lhe deles essas esses pelas este fosse dele",
    "vi": "và của là có không được trong một người cho những với để này các đã về khi đến từ như ra tôi anh thì cũng nhưng nó bị bạn lại họ chúng sẽ hay ở rất mà tại việc sự còn làm nhiều hơn vào theo trên thế đó nên",
}
STOP = {k: set(v.split()) for k, v in STOP.items()}


def detect_lang(text):
    """A guess from the script, then the common words. The person can change it."""
    s = text[:20000]
    n = max(1, len(re.sub(r"\s", "", s)))
    kana = len(re.findall(r"[ぁ-んァ-ヶー]", s))
    cjk = len(re.findall(r"[一-鿿]", s))
    hangul = len(re.findall(r"[가-힣]", s))
    cyr = len(re.findall(r"[А-яЁё]", s))
    arab = len(re.findall(r"[؀-ۿ]", s))
    thai = len(re.findall(r"[฀-๿]", s))
    if kana / n > 0.05:
        return "ja"
    if hangul / n > 0.2:
        return "ko"
    if cjk / n > 0.3:
        return "zh"
    if cyr / n > 0.3:
        return "ru"
    if arab / n > 0.3:
        return "ar"
    if thai / n > 0.3:
        return "th"
    words = re.findall(r"[a-zA-ZÀ-ɏ]+", s.lower())
    if not words:
        return "en"
    best, score = "en", -1
    for lang, stop in STOP.items():
        hits = sum(1 for w in words if w in stop)
        if hits > score:
            best, score = lang, hits
    if best == "vi" and len(re.findall(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", s.lower())) < 5:
        return "en"
    return best


# ---------- text into chapters and paragraphs ----------

HEAD_RE = re.compile(r"^(chapter|part|book|prologue|epilogue|introduction|preface|appendix|section|глава|часть|capítulo|chapitre|kapitel|第[0-9〇一二三四五六七八九十百]+[章節部話]|[0-9]{1,3}\.?|[IVXLC]{1,6}\.?)(\s|$)", re.I)


def paragraphs(text):
    """Blank lines split paragraphs. A paste with no blank lines uses every line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", text)]
    blocks = [b for b in blocks if b]
    if len(blocks) <= 2 and text.count("\n") > 6:
        blocks = [" ".join(l.split()) for l in text.split("\n") if l.strip()]
    return blocks


def is_heading(p):
    if len(p) > 80 or not p:
        return False
    if HEAD_RE.match(p) and len(p.split()) <= 8:
        return True
    letters = re.findall(r"[A-Za-zА-Яа-я]", p)
    return len(letters) >= 3 and all(c.isupper() for c in letters) and len(p.split()) <= 8


def chapters_from_text(text):
    paras = paragraphs(text)[:200000]
    chapters, cur = [], {"title": "", "paras": []}
    for p in paras:
        if is_heading(p) and (cur["paras"] or cur["title"]):
            if cur["paras"]:
                chapters.append(cur)
            cur = {"title": p, "paras": []}
        elif is_heading(p) and not cur["title"]:
            cur["title"] = p
        else:
            cur["paras"].append(p)
    if cur["paras"]:
        chapters.append(cur)
    if not chapters:
        chapters = [{"title": "", "paras": paras}]
    return chapters


# ---------- epub ----------

class _Blocks(HTMLParser):
    BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "blockquote", "td", "th", "dd", "dt", "pre", "section", "article", "tr", "br", "hr"}
    SKIP = {"script", "style", "head", "title", "svg", "nav"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.cur, self.skip, self.heads, self.inhead = [], [], 0, [], 0

    def flush(self):
        t = " ".join("".join(self.cur).split())
        self.cur = []
        if t:
            self.out.append(t)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.flush()
        if tag in ("h1", "h2", "h3"):
            self.inhead += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.flush()
        if tag in ("h1", "h2", "h3") and self.inhead:
            self.inhead -= 1
            if self.out and len(self.heads) < 3:
                self.heads.append(self.out[-1])

    def handle_data(self, data):
        if not self.skip:
            self.cur.append(data)
            if self.inhead:
                pass

    def close(self):
        super().close()
        self.flush()


def html_blocks(text):
    p = _Blocks()
    p.feed(text)
    p.close()
    return p.out, p.heads


def _xml(data):
    """ElementTree never fetches external entities, and entity declarations of any kind are refused here (billion laughs)."""
    if re.search(rb"<!(ENTITY|DOCTYPE[^>]*\[)", data[:200000], re.I):
        raise ValueError("This EPUB has XML the reader does not accept")
    return ElementTree.fromstring(data)


def parse_epub(data):
    """The spine's documents become chapters. Returns (title, author, lang, chapters)."""
    z = zipfile.ZipFile(data if hasattr(data, "read") else __import__("io").BytesIO(data))
    names = set(z.namelist())
    try:
        root = _xml(z.read("META-INF/container.xml"))
    except (KeyError, ElementTree.ParseError):
        raise ValueError("Not an EPUB: no container.xml")
    opf_path = next((e.get("full-path") for e in root.iter() if e.tag.endswith("rootfile")), None)
    if not opf_path or opf_path not in names:
        raise ValueError("Not an EPUB: no package file")
    try:
        opf = _xml(z.read(opf_path))
    except ElementTree.ParseError:
        raise ValueError("Not an EPUB: the package file is broken")
    base = os.path.dirname(opf_path)
    tagname = lambda e: e.tag.split("}")[-1]
    title = author = lang = ""
    for e in opf.iter():
        if tagname(e) == "title" and not title:
            title = " ".join((e.text or "").split())
        elif tagname(e) == "creator" and not author:
            author = " ".join((e.text or "").split())
        elif tagname(e) == "language" and not lang:
            lang = (e.text or "").strip().lower()[:2]
    items, nav = {}, set()
    for e in opf.iter():
        if tagname(e) == "item":
            href = html.unescape(e.get("href") or "")
            items[e.get("id")] = (os.path.normpath(os.path.join(base, href)).replace("\\", "/"), e.get("media-type") or "")
            if "nav" in (e.get("properties") or "").split():
                nav.add(e.get("id"))
    order = [e.get("idref") for e in opf.iter() if tagname(e) == "itemref" and e.get("linear", "yes") != "no"]
    if "encryption.xml" in " ".join(names) and any(n.endswith("META-INF/encryption.xml") for n in names):
        enc = z.read(next(n for n in names if n.endswith("META-INF/encryption.xml"))).decode("utf-8", "ignore")
        if "html" in enc or "xhtml" in enc:
            raise ValueError("This book is locked (DRM), so it cannot be read here")
    chapters = []
    for idref in order:
        if idref in nav or idref not in items:
            continue
        path, mtype = items[idref]
        if "html" not in mtype and not path.endswith((".html", ".xhtml", ".htm")):
            continue
        if path not in names:
            continue
        blocks, heads = html_blocks(z.read(path).decode("utf-8", "ignore"))
        head = heads[0] if heads else ""
        paras = [b for b in blocks if b != head]
        words = sum(len(b.split()) for b in paras) + sum(len(b) for b in paras if re.search(r"[ぁ-ん一-鿿]", b)) // 2
        if words < 20 and not head:
            continue
        chapters.append({"title": head[:120], "paras": paras})
    if not chapters:
        raise ValueError("No readable text in this EPUB")
    return title, author, lang, chapters


# ---------- PDF ----------

def _pdftext():
    if os.path.exists(PDFTEXT) and os.path.getmtime(PDFTEXT) >= os.path.getmtime(PDFTEXT_SOURCE):
        return PDFTEXT
    os.makedirs(os.path.dirname(PDFTEXT), exist_ok=True)
    subprocess.run(["swiftc", "-O", "-framework", "Cocoa", "-framework", "PDFKit", PDFTEXT_SOURCE, "-o", PDFTEXT], check=True, capture_output=True, timeout=240)
    return PDFTEXT


def pdf_text(path):
    try:
        tool = _pdftext()
    except (OSError, subprocess.SubprocessError):
        raise ValueError("The PDF helper could not be built. Run xcode-select --install, then try again")
    r = subprocess.run([tool, path], capture_output=True, text=True, timeout=300)
    if r.returncode == 2:
        raise ValueError("This PDF is locked with a password")
    if r.returncode != 0:
        raise ValueError("Could not read this PDF")
    return r.stdout


def chapters_from_pdf(text):
    """Pages come apart with form feeds. Lines are joined into paragraphs: a line ending a sentence ends a paragraph when the
    page has no blank lines of its own."""
    chapters, pages = [], text.split("\f")
    paras = []
    for pg in pages:
        pg = pg.replace("\r", "")
        if re.search(r"\n\s*\n", pg):
            paras.extend(paragraphs(pg))
            continue
        cur = []
        for line in pg.split("\n"):
            line = line.strip()
            if not line:
                continue
            if re.fullmatch(r"\d{1,4}", line):  # a page number on its own
                continue
            if is_heading(line):  # a chapter title on its own line
                if cur:
                    paras.append(" ".join(cur))
                    cur = []
                paras.append(line)
                continue
            cur.append(line)
            if re.search(r"[.!?。！？」”\"']$", line):
                paras.append(" ".join(cur))
                cur = []
        if cur:
            paras.append(" ".join(cur))
    paras = [" ".join(p.split()) for p in paras if p.strip()]
    if not paras:
        raise ValueError("No text in this PDF (a scan has none; this reader does not do OCR)")
    cur = {"title": "", "paras": []}
    for p in paras:
        if is_heading(p) and cur["paras"]:
            chapters.append(cur)
            cur = {"title": p, "paras": []}
        elif is_heading(p) and not cur["title"]:
            cur["title"] = p
        else:
            cur["paras"].append(p)
    if cur["paras"]:
        chapters.append(cur)
    return chapters or [{"title": "", "paras": paras}]


# ---------- adding and keeping books ----------

def _decode(data):
    for enc in ("utf-8-sig", "utf-16", "cp932", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def _count_words(chapters):
    n = 0
    for c in chapters:
        for p in c.get("paras") or []:
            n += len(p.split()) if not re.search(r"[ぁ-ん一-鿿]", p) else len(p) // 2
    return n


def _save_new(title, author, lang, chapters, ext=None, raw=None):
    text_len = sum(len(p) for c in chapters for p in c["paras"])
    if text_len > TEXT_MAX:
        raise ValueError("This book is too long for the reader (3 million characters)")
    sample = "\n".join(p for c in chapters[:3] for p in c["paras"][:40])
    lang = (lang or "")[:2].lower() if lang and lang[:2].lower() in VOICE_LANG else ""
    rec = {"title": (title or "").strip()[:200] or (chapters[0]["paras"][0][:60] if chapters and chapters[0]["paras"] else "Untitled"),
           "author": (author or "").strip()[:120], "lang": lang or detect_lang(sample), "chapters": chapters,
           "pos": {"ch": 0, "para": 0}, "addedAt": now_ms(), "lastReadAt": 0, "updatedAt": now_ms(), "words": _count_words(chapters), "file": ""}
    book_id = "b" + format(now_ms(), "x") + os.urandom(2).hex()
    if raw is not None and ext:
        os.makedirs(BOOKS, mode=0o700, exist_ok=True)
        with open(os.path.join(BOOKS, book_id + ext), "wb") as f:
            f.write(raw)
        rec["file"] = ext
    _write(_path(book_id), rec)
    return {**meta({**rec, "id": book_id}), "chapters_full": None}


def add_text(text, title="", author="", lang=""):
    """POST /api/reader/books with JSON {text, title, author, lang}."""
    text = str(text or "")
    if not text.strip():
        return 400, {"error": "No text"}
    if len(text) > TEXT_MAX:
        return 413, {"error": "Too long (3 million characters)"}
    try:
        return 200, _save_new(title, author, lang, chapters_from_text(text))
    except ValueError as e:
        return 400, {"error": str(e)}


def add_file(data, filename=""):
    """POST /api/reader/books with the file itself (X-Desk-Upload: 1, X-File-Name). The kind comes from the bytes."""
    if not data:
        return 400, {"error": "Empty file"}
    if len(data) > BOOK_MAX:
        return 413, {"error": "A file can be up to 50 MB"}
    name = os.path.splitext(os.path.basename(filename or ""))[0][:200]
    try:
        if data[:2] == b"PK":
            title, author, lang, chapters = parse_epub(data)
            return 200, _save_new(title or name, author, lang, chapters, ".epub", data)
        if data[:5] == b"%PDF-":
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(data)
                tmp = f.name
            try:
                chapters = chapters_from_pdf(pdf_text(tmp))
            finally:
                os.unlink(tmp)
            return 200, _save_new(name, "", "", chapters, ".pdf", data)
        if b"\0" in data[:4096]:
            return 415, {"error": "Only text, .epub and PDFs with text can be read"}
        return 200, _save_new(name, "", "", chapters_from_text(_decode(data)), ".txt", data)
    except zipfile.BadZipFile:
        return 400, {"error": "Not an EPUB"}
    except ValueError as e:
        return 400, {"error": str(e)}


def get(book_id):
    try:
        b = read_book(book_id)
    except ValueError:
        return 404, {"error": "not found"}
    return (200, {**meta(b), "chapters_full": b.get("chapters") or []}) if b else (404, {"error": "not found"})


def set_pos(book_id, rec):
    try:
        b = read_book(book_id)
    except ValueError:
        b = None
    if not b:
        return 404, {"error": "not found"}
    ch, para = int(rec.get("ch") or 0), int(rec.get("para") or 0)
    chapters = b.get("chapters") or []
    ch = max(0, min(ch, len(chapters) - 1))
    para = max(0, min(para, len(chapters[ch].get("paras") or []) - 1)) if chapters else 0
    b.update(pos={"ch": ch, "para": para}, lastReadAt=now_ms(), updatedAt=now_ms())
    _write(_path(book_id), {k: v for k, v in b.items() if k != "id"})
    return 200, meta(b)


def edit(book_id, rec):
    """PUT /api/reader/books/<id>: title, author and language only."""
    try:
        b = read_book(book_id)
    except ValueError:
        b = None
    if not b:
        return 404, {"error": "not found"}
    if "title" in rec:
        b["title"] = str(rec["title"] or "").strip()[:200] or b["title"]
    if "author" in rec:
        b["author"] = str(rec["author"] or "").strip()[:120]
    if "lang" in rec and str(rec["lang"])[:2].lower() in VOICE_LANG:
        b["lang"] = str(rec["lang"])[:2].lower()
    b["updatedAt"] = now_ms()
    _write(_path(book_id), {k: v for k, v in b.items() if k != "id"})
    return 200, meta(b)


def remove(book_id):
    """The book leaves the list; the file stays until the person empties the folder (a logical delete, like the rest of the Desk)."""
    try:
        b = read_book(book_id)
    except ValueError:
        b = None
    if not b:
        return 404, {"error": "not found"}
    b.update(deleted=True, updatedAt=now_ms())
    _write(_path(book_id), {k: v for k, v in b.items() if k != "id"})
    return 200, {"ok": True}


# ---------- reading aloud with macOS ----------

def voices():
    """The Mac's voices: [{name, lang}], read once."""
    global _voices
    if _voices is not None:
        return _voices
    out = []
    try:
        r = subprocess.run(["/usr/bin/say", "-v", "?"], capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines():
            m = re.match(r"^(.+?)\s{2,}([a-z]{2,3}_[A-Z]{2,}|[a-z]{2,3}-[a-z]+)\s*#", line)
            if m:
                out.append({"name": m.group(1).strip(), "lang": m.group(2).replace("-", "_")})
    except (OSError, subprocess.SubprocessError):
        pass
    _voices = out
    return out


NOVELTY = re.compile(r"^(Albert|Bad News|Bahh|Bells|Boing|Bubbles|Cellos|Wobble|Fred|Good News|Jester|Junior|Organ|Superstar|Ralph|Trinoids|Whisper|Zarvox|Kathy)\b", re.I)
ELOQUENCE = re.compile(r"^(Eddy|Flo|Grandma|Grandpa|Reed|Rocko|Sandy|Shelley)\b", re.I)


def voice_rank(name):
    """Best first, as in the page (rdVoiceRank): Premium, Enhanced, the natural compact voices, then the old Eloquence ones."""
    if re.search(r"premium", name, re.I):
        return 0
    if re.search(r"enhanced|siri", name, re.I):
        return 1
    return 3 if ELOQUENCE.match(name) else 2


def voices_for(lang):
    """The Mac's voices for a language, best first; novelty voices (Bells, Zarvox, ...) are left out."""
    pre = VOICE_LANG.get((lang or "en")[:2], (lang or "en")[:2] + "_")
    vs = [v for v in voices() if v["lang"].startswith(pre) and not NOVELTY.match(v["name"])]
    return sorted(vs, key=lambda v: voice_rank(v["name"]))


def speak(rec):
    """POST /api/reader/speak {text, lang, rate, voice} -> (200, m4a bytes) or an error. The file is made in a temp folder and dropped."""
    text = " ".join(str(rec.get("text") or "").split())[:SPEAK_MAX]
    if not text:
        return 400, {"error": "No text"}
    if sys.platform != "darwin":
        return 501, {"error": "mac only"}
    lang = str(rec.get("lang") or "en")[:2]
    rate = float(rec.get("rate") or 1)
    rate = min(1.3, max(0.7, rate))
    want = rec.get("voice") if isinstance(rec.get("voice"), str) else ""
    if want.startswith("google:"):  # Google's natural voice (desk/gtts.py); on any trouble the Mac reads instead
        import gtts
        try:
            return 200, gtts.speak(text, want[7:], rate)
        except gtts.Unavailable:
            pass
    names = {v["name"] for v in voices()}
    voice = rec.get("voice") if isinstance(rec.get("voice"), str) and rec.get("voice") in names else None
    if not voice:
        cands = voices_for(lang)
        voice = cands[0]["name"] if cands else None
    with tempfile.TemporaryDirectory() as d:
        out, src = os.path.join(d, "s.m4a"), os.path.join(d, "s.txt")
        with open(src, "w", encoding="utf-8") as f:  # the text goes in through a file: a sentence starting with "-" is never an option
            f.write(text)
        cmd = ["/usr/bin/say", "-r", str(int(175 * rate)), "-o", out, "-f", src] + (["-v", voice] if voice else [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return 500, {"error": "say failed"}
        if r.returncode != 0 or not os.path.exists(out):
            return 500, {"error": "say failed"}
        with open(out, "rb") as f:
            return 200, f.read()


if __name__ == "__main__":
    if sys.argv[1:2] == ["list"]:
        print(json.dumps(list_books(), ensure_ascii=False, indent=1))
    elif sys.argv[1:2] == ["add"] and len(sys.argv) > 2:
        with open(sys.argv[2], "rb") as f:
            print(json.dumps(add_file(f.read(), os.path.basename(sys.argv[2])), ensure_ascii=False))
    elif sys.argv[1:2] == ["voices"]:
        print(json.dumps(voices(), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
