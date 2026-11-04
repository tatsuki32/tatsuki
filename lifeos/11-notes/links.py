"""Tags, links between related memos, and the mapping to Cosense pages. Rules only: no outside service."""
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import quote

from lifeos.lib import paths

MAX_TAGS = 8
MAX_RELATED = 5
MIN_SCORE = 0.1
SKIP_TERM_DF = 300  # a word in this many memos says nothing and would be slow
# Generic words only. Tatsuki's own words come from tags.json and from his Desk note titles.
DEFAULT_TAGS = {
    "仕事": ["仕事", "会議", "打ち合わせ", "納期", "請求"],
    "お金": ["支払", "料金", "家賃", "予算", "振込", "税金"],
    "健康": ["睡眠", "運動", "病院", "体調", "通院"],
    "学習": ["勉強", "学習", "単語", "読書", "語学"],
    "開発": ["プログラム", "コード", "バグ", "python", "github"],
    "買い物": ["買い物", "買う", "注文"],
    "旅行": ["旅行", "航空券", "ホテル", "ビザ"],
}
STOP = {"今日", "明日", "昨日", "今週", "来週", "先週", "今月", "来月", "自分", "今年", "来年", "時間", "メモ"}
_HASH = re.compile(r"(?:^|(?<=[\s　]))[#＃]([^\s#＃\[\]　,、。]{1,30})", re.M)
_BRACKET = re.compile(r"\[([^\[\]\n]+)\]")
_TERM = re.compile(r"[一-龠々]{2,}|[ァ-ヶー]{2,}|[a-z0-9]{2,}")


def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).strip().lower()


def clean_tag(s):
    return re.sub(r"[\[\]\n]", "", str(s or "")).strip()[:30]


def _contains(text, word):
    """word in text. Latin words match whole words only, the same way Desk's auto-links do."""
    if re.match(r"^[a-z0-9]", word) or re.search(r"[a-z0-9]$", word):
        return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None
    return word in text


def tag_dictionary():
    """tools/11-notes/tags.json ({tag: [words]}) added to the generic words. A broken file is ignored."""
    out = dict(DEFAULT_TAGS)
    try:
        with open(paths.data_path("tools", "11-notes", "tags.json"), encoding="utf-8") as f:
            mine = json.load(f)
        if isinstance(mine, dict):
            out.update({str(k): [str(w) for w in v] for k, v in mine.items() if isinstance(v, list)})
    except (OSError, ValueError):
        pass
    return out


def tags_for(memo, vocabulary=(), dictionary=None):
    """In this order: kind, tags given by the source, #tags, [words], Desk note titles, the dictionary."""
    raw = memo["text"]
    text = norm(raw)
    found = [memo["kind"]] + list(memo["tags"]) + _HASH.findall(raw)
    found += [w for w in _BRACKET.findall(raw) if not w.strip().startswith("https://")]
    found += [w for w in vocabulary if _contains(text, norm(w))]
    for tag, words in (tag_dictionary() if dictionary is None else dictionary).items():
        if any(_contains(text, norm(w)) for w in words):
            found.append(tag)
    out, seen = [], set()
    for t in found:
        t = clean_tag(t)
        if t and norm(t) not in seen:
            seen.add(norm(t))
            out.append(t)
    return out[:MAX_TAGS]


def _terms(text):
    out = []
    for w in _TERM.findall(norm(text)):
        if re.match(r"^[一-龠々]", w) and len(w) > 2:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            out.append(w)
    return [w for w in out if w not in STOP]


def relate(memos):
    """{memo id: [related ids, best first]}, by shared rare words and shared tags.

    Each memo keeps its best few; a link is always shown from both sides.
    """
    n = len(memos)
    bags = {m["id"]: Counter(_terms(m["text"])) for m in memos}
    df = Counter(t for bag in bags.values() for t in bag)
    posting = defaultdict(list)
    for mid, bag in bags.items():
        v = {t: (1 + math.log(c)) * math.log(1 + n / df[t]) for t, c in bag.items()}
        size = math.sqrt(sum(x * x for x in v.values())) or 1
        for t, x in v.items():
            if df[t] <= SKIP_TERM_DF:
                posting[t].append((mid, x / size))
    scores = defaultdict(float)
    for items in posting.values():
        for i, (a, x) in enumerate(items):
            for b, y in items[i + 1:]:
                scores[(a, b) if a < b else (b, a)] += x * y
    # the kind only says what sort of memo it is, not what it is about
    tagged = defaultdict(list)
    for m in memos:
        for t in m["all_tags"]:
            if norm(t) != norm(m["kind"]):
                tagged[norm(t)].append(m["id"])
    for ids in tagged.values():
        if 2 <= len(ids) <= SKIP_TERM_DF:
            bonus = 0.25 / (1 + math.log(len(ids)))
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    scores[(a, b) if a < b else (b, a)] += bonus
    near = defaultdict(list)
    for (a, b), s in scores.items():
        if s >= MIN_SCORE:
            near[a].append((s, b))
            near[b].append((s, a))
    related = {m["id"]: set() for m in memos}
    best = {}
    for a, items in near.items():
        for s, b in sorted(items, key=lambda x: (-x[0], x[1]))[:MAX_RELATED]:
            related[a].add(b)
            related[b].add(a)
            best[(a, b)] = best[(b, a)] = s
    return {a: sorted(bs, key=lambda b: (-best[(a, b)], b)) for a, bs in related.items()}


# ---------- Cosense ----------
# What to take in is Tatsuki's decision. Until settings hold a scope, no page is used at all.

_loader = None


def set_cosense_loader(fn):
    """fn() returns the pages as in a Cosense export: [{"title", "updated", "lines"}]."""
    global _loader
    _loader = fn


def _export_pages():
    try:
        with open(paths.data_path("tools", "11-notes", "cosense-export.json"), encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError):
        return []
    pages = v.get("pages") if isinstance(v, dict) else v
    return [p for p in pages if isinstance(p, dict) and p.get("title")] if isinstance(pages, list) else []


def _lines(page):
    return "\n".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in page.get("lines") or [])


def _in_scope(page, scope):
    if scope.get("all"):
        return True
    if any(str(page["title"]).startswith(p) for p in scope.get("prefixes") or []):
        return True
    body = _lines(page)
    if any("#" + t in body or "[" + t + "]" in body for t in scope.get("tags") or []):
        return True
    since = scope.get("since")
    if since and page.get("updated"):
        return datetime.fromtimestamp(page["updated"], paths.HCMC).strftime("%Y-%m-%d") >= since
    return False


def cosense_pages(settings):
    """The titles in scope. Page bodies are read only to apply the scope, and are not kept or shown."""
    scope = (settings.get("cosense") or {}).get("scope")
    if not scope:
        return []
    pages = _loader() if _loader else _export_pages()
    return [str(p["title"]) for p in pages if _in_scope(p, scope)]


def cosense_links(memo, titles, project, limit=3):
    """[[title, url]] for the pages this memo mentions by title or tag."""
    if not project:
        return []
    text = norm(memo["text"])
    tags = {norm(t) for t in memo["all_tags"]}
    hits = [t for t in titles if len(norm(t)) >= 2 and (norm(t) in tags or _contains(text, norm(t)))]
    hits.sort(key=lambda t: -len(t))
    return [[t, "https://scrapbox.io/{}/{}".format(quote(project, safe=""), quote(t, safe=""))] for t in hits[:limit]]
