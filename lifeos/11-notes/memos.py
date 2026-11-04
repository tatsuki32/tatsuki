"""Reads the sorted memos from <data>/notes/. Sources are only read here, never changed.

Accepted:
- .md / .txt: one memo per file, with optional front matter (id, title, kind, at, tags).
  04 writes notes/<memo id>-<n>-<idea|diary>.md with kind, at and from.
- .jsonl: one memo per line, with the same keys and the text in text, body or transcript.
- images: a photo or handwritten memo. Its text comes from a .txt with the same name beside it,
  or from the reader connected below.

Image reading is swappable and nothing is connected yet: the method is still undecided.
Connect one with set_image_reader(fn), or LIFEOS_IMAGE_READER="package.module:function".
The function takes an image file path and returns the text.
"""
import hashlib
import importlib
import json
import os
import re
from datetime import datetime

from lifeos.lib import paths

IMAGES = (".png", ".jpg", ".jpeg", ".heic", ".webp")
TEXT_KEYS = ("text", "body", "transcript")
KIND_KEYS = ("kind", "category")
TIME_KEYS = ("at", "date", "createdAt")
# 04 stores English values; tags and pages show Japanese (same labels as 04-voice/sort.py)
KIND_JA = {"task": "タスク", "event": "予定", "expense": "支出", "idea": "アイデア", "diary": "日記"}
_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_reader = None


def set_image_reader(fn):
    global _reader
    _reader = fn


def _current_reader():
    if _reader:
        return _reader
    spec = os.environ.get("LIFEOS_IMAGE_READER")
    if spec and ":" in spec:
        module, func = spec.split(":", 1)
        return getattr(importlib.import_module(module), func)
    return None


def reader_connected():
    return _current_reader() is not None


def _read_image(memo_id, path):
    """The image's text, or "" while unreadable. A result is kept so each image is read only once."""
    cache = paths.data_path("tools", "11-notes", "read", memo_id + ".txt")
    try:
        with open(cache, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        pass
    fn = _current_reader()
    if not fn:
        return ""
    try:
        text = str(fn(path) or "").strip()
    except Exception:
        return ""
    if text:
        with open(cache, "w", encoding="utf-8") as f:
            f.write(text)
    return text


def _front_matter(raw):
    """(fields, text). Files without front matter are all text."""
    m = re.match(r"^---\n(.*?)\n---\n?", raw, re.S)
    if not m:
        return {}, raw
    fields = {}
    for line in m.group(1).split("\n"):
        k, sep, v = line.partition(":")
        if sep and k.strip():
            fields[k.strip()] = v.strip().strip("\"'")
    return fields, raw[m.end():]


def _as_tags(v):
    if isinstance(v, str):
        v = re.split(r"[,、]", v.strip("[] "))
    return [str(x).strip() for x in (v or []) if str(x).strip()]


def _when(value, rel, mtime):
    """(day, ISO time) in Ho Chi Minh time: the record's own time, then a date in the file name, then the file's time."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = datetime.fromtimestamp(value / 1000, paths.HCMC)
        return dt.strftime("%Y-%m-%d"), dt.isoformat(timespec="seconds")
    if value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            dt = dt.astimezone(paths.HCMC) if dt.tzinfo else dt.replace(tzinfo=paths.HCMC)
            return dt.strftime("%Y-%m-%d"), dt.isoformat(timespec="seconds")
        except ValueError:
            pass
    m = _DATED.match(os.path.basename(rel))
    if m:
        return m.group(1), m.group(1) + "T00:00:00+07:00"
    dt = datetime.fromtimestamp(mtime, paths.HCMC)
    return dt.strftime("%Y-%m-%d"), dt.isoformat(timespec="seconds")


def _sha(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _first(rec, keys):
    return next((rec[k] for k in keys if rec.get(k)), None)


def _kind_ja(kind):
    kind = str(kind or "")
    return KIND_JA.get(kind, kind)


def _memo(rec, text, rel, mtime, default_id):
    day, at = _when(_first(rec, TIME_KEYS), rel, mtime)
    return {"id": str(rec.get("id") or default_id), "src": rel, "text": text.strip(), "day": day, "at": at,
            "title": str(rec.get("title") or ""), "kind": _kind_ja(_first(rec, KIND_KEYS)),
            "tags": _as_tags(rec.get("tags")), "unread": False}


def _image_memo(path, rel, mtime):
    memo_id = _sha(rel)
    side = os.path.splitext(path)[0] + ".txt"
    text = ""
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            text = f.read()
    text = text.strip() or _read_image(memo_id, path)
    kind = "手書き" if "手書き" in rel else "写真"
    m = _memo({"kind": kind, "title": "" if text else os.path.basename(rel)}, text, rel, mtime, memo_id)
    m["unread"] = not text
    m["file"] = os.path.basename(rel)
    return m


def _jsonl_memos(path, rel, mtime):
    out = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            text = _first(rec, TEXT_KEYS) if isinstance(rec, dict) else None
            if text:
                out.append(_memo(rec, str(text), rel, mtime, _sha(rel + "#" + str(i))))
    return out


def read_all():
    """Every memo in notes/, oldest first. A file that cannot be read is skipped, not fatal."""
    root = paths.data_path("notes", mkdir=False)
    out = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))
        stems_of_images = {os.path.splitext(n)[0] for n in files if os.path.splitext(n)[1].lower() in IMAGES}
        for name in sorted(files):
            if name.startswith((".", "_")):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, root)
            stem, ext = os.path.splitext(name)
            ext = ext.lower()
            try:
                mtime = os.path.getmtime(path)
                if ext in IMAGES:
                    out.append(_image_memo(path, rel, mtime))
                elif ext == ".jsonl":
                    out.extend(_jsonl_memos(path, rel, mtime))
                elif ext == ".md" or (ext == ".txt" and stem not in stems_of_images):
                    with open(path, encoding="utf-8") as f:
                        fields, text = _front_matter(f.read())
                    if text.strip():
                        out.append(_memo(fields, text, rel, mtime, _sha(rel)))
            except (OSError, UnicodeDecodeError):
                continue
    return sorted(out, key=lambda m: (m["at"], m["id"]))


def add(text, kind="", tags=None):
    """Writes one memo as a file in notes/ and returns its relative path."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("メモが空です")
    now = paths.now()
    rel = "notes/{}-{}.md".format(now.strftime("%Y-%m-%d-%H%M%S"), os.urandom(2).hex())
    head = ["at: " + now.isoformat(timespec="seconds")]
    if kind:
        head.append("kind: " + kind)
    if tags:
        head.append("tags: " + ", ".join(tags))
    with open(paths.data_path(rel), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(head) + "\n---\n" + text + "\n")
    return rel
