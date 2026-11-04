"""Life map: one question a day about one area of life, answers kept per area, monthly patterns.

Never nags: an unanswered question simply expires, and nothing mentions it again.
At most one question a day: the question is recorded before it is sent, so a crash or a failed
send loses that day's question instead of asking twice.
"""
import fcntl
import hashlib
import json
import os
import random
import re
import tempfile
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta

from lifeos.bot import telegram
from lifeos.lib import logs, md, paths, tasks, tools

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = "lifemap"
COLLECTION = "lifemap"          # the Desk's collection: questions, answers and monthly insights
START_HOUR, END_HOUR = 9, 21    # questions only in this window, never late at night
TICK_MINUTES = 15               # how often the scheduler runs tick()
ANSWER_HOURS = 2                # how long a plain message counts as the answer
RECENT_AREAS = 3                # do not repeat the areas of the last few questions
RECENT_QUESTIONS = 30
MAX_PATTERNS = 3

AREAS = {
    "work": "仕事",
    "money": "お金",
    "health": "健康",
    "people": "人間関係",
    "learning": "学び",
    "home": "住まい・移動",
}

QUESTIONS = {
    "work": [
        "最近の仕事で、時間を忘れた場面はありましたか？",
        "今の仕事で、やめられるならやめたいことは何ですか？",
        "この1週間の仕事で、いちばん気になっていることは何ですか？",
        "仕事で、最近うまくいったことを1つ挙げるなら？",
        "1年後も続けていたい仕事はどれですか？",
    ],
    "money": [
        "最近、払ってよかったと思ったお金は何ですか？",
        "お金のことで、頭の片隅にずっとあることは何ですか？",
        "今の暮らしに、毎月いくらあれば落ち着きますか？",
        "最近、なくても困らなかった出費はありましたか？",
        "お金が今の倍あったら、最初に何を変えますか？",
    ],
    "health": [
        "この1週間で、体が軽かった日は何をしていましたか？",
        "最近の眠りはどんな感じですか？",
        "最近、体を動かして気持ちよかったことはありますか？",
        "最近、疲れがたまるのはどんなときですか？",
        "食事で、最近おいしかったものは何ですか？",
    ],
    "people": [
        "最近、話してよかったと思った人はいますか？",
        "しばらく会っていないけれど、気になる人はいますか？",
        "最近、人づきあいで引っかかっていることはありますか？",
        "最近、誰かに助けられたことは何ですか？",
        "もっと時間を一緒に過ごしたいのはどんな人ですか？",
    ],
    "learning": [
        "最近、知って面白かったことは何ですか？",
        "いま、少しでも上手になりたいことは何ですか？",
        "最近読んだり聞いたりしたもので、残っている言葉はありますか？",
        "やってみたいけれど、まだ手を付けていないことは何ですか？",
        "最近、前よりできるようになったことは何ですか？",
    ],
    "home": [
        "今の住まいで、気に入っている場所はどこですか？",
        "住まいで、小さく変えたいところはありますか？",
        "最近の移動で、楽しかったのはどこですか？",
        "最近の移動で、面倒に感じたのはどこですか？",
        "次に暮らしてみたい場所はありますか？",
    ],
}

# Feelings found in answers. "heavy" pairs make the 仕事のストレスとお金の不安 kind of pattern.
FEELINGS = {
    "heavy": {
        "不安": ("不安", "心配", "怖"),
        "ストレス": ("ストレス", "イライラ", "しんど", "きつ", "つら", "辛"),
        "疲れ": ("疲", "だる", "眠れ", "寝不足"),
        "焦り": ("焦", "間に合わ", "追われ"),
    },
    "light": {
        "楽しさ": ("楽し", "嬉し", "うれし", "わくわく", "面白"),
        "安心": ("落ち着", "安心", "ほっと"),
    },
}
MIN_WEEKS = 2  # one shared week could be chance
WORD = re.compile(r"[一-龥]{2,6}|[ァ-ヶー]{3,}")
COMMON_WORDS = {
    "今日", "昨日", "明日", "今週", "先週", "来週", "今月", "先月", "来月", "今年", "去年", "来年",
    "最近", "時間", "自分", "気持", "感じ", "場合", "以外", "今後", "全部", "一番", "毎日", "毎週", "毎月",
} | {w for g in FEELINGS.values() for ws in g.values() for w in ws}
HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\s*$")


def _state_path(mkdir=True):
    return paths.data_path("tools", "14-lifemap", "state.json", mkdir=mkdir)


@contextmanager
def _locked():
    with open(paths.data_path("tools", "14-lifemap", "state.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def load_state():
    try:
        with open(_state_path(mkdir=False), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state):
    p = _state_path()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".state", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _desk_file():
    return os.path.join(paths.desk_data_dir(), COLLECTION + ".json")


def desk_docs():
    """The Desk's lifemap collection: through the server when it is up, else from its file."""
    if tasks._server_up():
        try:
            return tasks._call("GET", "/api/" + COLLECTION)
        except (OSError, ValueError):
            pass
    try:
        with open(_desk_file(), encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError):
        return []
    return [x for x in v if isinstance(x, dict) and x.get("id")] if isinstance(v, list) else []


def _desk_put(doc):
    rec = {**doc, "updatedAt": paths.now_ms()}
    if tasks._server_up():
        try:
            body = {k: v for k, v in rec.items() if k != "id"}
            tasks._call("PUT", f"/api/{COLLECTION}/" + urllib.parse.quote(doc["id"], safe=""), body)
            return rec
        except (OSError, ValueError):
            pass  # an older server, or it went away: fall through to the file
    os.makedirs(paths.desk_data_dir(), exist_ok=True)
    with open(_desk_file() + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        items = [x for x in desk_docs() if x["id"] != doc["id"]] + [rec]
        fd, tmp = tempfile.mkstemp(dir=paths.desk_data_dir(), prefix=".lifemap", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _desk_file())
    return rec


def _desk_doc(doc_id):
    return next((d for d in desk_docs() if d["id"] == doc_id), None)


def _question_id(day):
    return "q-" + day


def _closed(doc):
    return bool(doc and (doc.get("answer") or doc.get("skipped") or doc.get("deleted")))


def _stopped():
    return tools.info(HERE).get("status") == "stopped"


def _can_send():
    token, chat = telegram.config()
    return bool(token and chat) and not paths.is_sandbox()


def _plan(day_now, rng):
    """A random minute between now and the end of today's window, or None if it is over.

    Ends one tick early: a time in the last minutes would have no tick left to send it.
    """
    start = day_now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
    end = day_now.replace(hour=END_HOUR, minute=0, second=0, microsecond=0)
    start = max(start, day_now.replace(second=0, microsecond=0))
    minutes = int((end - start).total_seconds() // 60) - TICK_MINUTES
    if minutes <= 0:
        return None
    return (start + timedelta(minutes=rng.randrange(minutes))).strftime("%H:%M")


def _new_day(state, now, rng):
    day = now.strftime("%Y-%m-%d")
    if state.get("day") != day:
        keep = {k: state[k] for k in ("paused", "lastInsight") if k in state}
        state.clear()
        state.update(keep, day=day, planned=_plan(now, rng), askedAt=None, open=False)


def _asks():
    return [r for r in logs.read(LOG) if r.get("kind") == "ask"]


def _pick(asks, rng):
    recent = [r.get("area") for r in asks[-RECENT_AREAS:]]
    area = rng.choice([a for a in AREAS if a not in recent] or list(AREAS))
    used = {r.get("question") for r in asks[-RECENT_QUESTIONS:]}
    return area, rng.choice([q for q in QUESTIONS[area] if q not in used] or QUESTIONS[area])


def format_question(area, question):
    return f"{AREAS[area]}について：{question}\n（答えるなら{ANSWER_HOURS}時間以内にそのまま返信を。答えなくても大丈夫です）"


def _ask(state, now, send, rng):
    """Asks today's question unless one was already asked. The caller holds the lock."""
    day = now.strftime("%Y-%m-%d")
    asks = _asks()
    if state.get("askedAt") or any(r.get("day") == day for r in asks):
        return None
    area, question = _pick(asks, rng)
    state.update(askedAt=now.isoformat(timespec="seconds"), area=area, question=question, open=False)
    _save_state(state)
    logs.append(LOG, {"kind": "ask", "day": day, "area": area, "question": question})
    _desk_put({"id": _question_id(day), "day": day, "area": area, "question": question,
               "askedAt": state["askedAt"], "deleted": False})
    text = format_question(area, question)
    sent = (send or telegram.notify)(text)
    # A question that never arrived must not swallow the next message. Without Telegram the
    # only way in is the command line, so the answer stays possible there.
    state["open"] = bool(sent) or (send is None and not _can_send())
    _save_state(state)
    return text


def tick(now=None, send=None, rng=None):
    """Runs every few minutes from the scheduler. Returns what it sent: {"question", "insight"}."""
    now = now or paths.now()
    rng = rng or random.Random()
    out = {"question": None, "insight": None}
    if _stopped():
        return out
    with _locked():
        import_desk_answers()
        state = load_state()
        if state.get("paused"):
            return out
        _new_day(state, now, rng)
        _save_state(state)
        if START_HOUR <= now.hour < END_HOUR:
            out["insight"] = _monthly(state, now, send)
        due = state.get("planned") and now.strftime("%H:%M") >= state["planned"] and now.hour < END_HOUR
        if due:
            out["question"] = _ask(state, now, send, rng)
    return out


def ask_now(now=None, send=None, rng=None):
    """Asks by hand. Still only one question a day. Returns the text, or None if already asked."""
    now = now or paths.now()
    rng = rng or random.Random()
    with _locked():
        state = load_state()
        _new_day(state, now, rng)
        return _ask(state, now, send, rng)


def pending(state=None, now=None):
    """The open question as {"area", "question"}, or None."""
    state = load_state() if state is None else state
    if state.get("paused") or not state.get("open") or not state.get("askedAt"):
        return None
    asked = datetime.fromisoformat(state["askedAt"])
    if (now or paths.now()) - asked > timedelta(hours=ANSWER_HOURS):
        return None
    if _closed(_desk_doc(_question_id(state["askedAt"][:10]))):  # answered on the Desk already
        return None
    return {"area": state["area"], "question": state["question"]}


def area_file(area):
    return f"life/{area}.md"


def _answer_key(doc_id, text):
    return doc_id + ":" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _write_answer(area, question, text, when, doc_id):
    """Adds one answer to the area's file and remembers it, so the Desk's copy is not added again."""
    rel = area_file(area)
    if md.read(rel) is None:
        md.save(rel, f"# {AREAS[area]}\n")
    md.append(rel, f"\n## {when.strftime('%Y-%m-%d %H:%M')}\n> {question}\n\n{text.strip()}\n")
    logs.append(LOG, {"kind": "answer", "day": when.strftime("%Y-%m-%d"), "area": area,
                      "key": _answer_key(doc_id, text.strip())})


def add_answer(text, now=None, is_voice=False):
    """Appends a reply from the bot to the area's file and closes the question. Returns the area or None."""
    now = now or paths.now()
    text = text.strip() + ("（音声）" if is_voice else "")
    with _locked():
        state = load_state()
        q = pending(state, now)
        if not q:
            return None
        doc_id = _question_id(state["askedAt"][:10])
        _write_answer(q["area"], q["question"], text, now, doc_id)
        state["open"] = False
        _save_state(state)
        _desk_put({**(_desk_doc(doc_id) or {"id": doc_id, "day": doc_id[2:], "area": q["area"], "question": q["question"]}),
                   "answer": text, "answeredAt": int(now.timestamp() * 1000), "answeredVia": "telegram"})
    return q["area"]


def import_desk_answers():
    """Copies answers written on the Desk (Mac or online) into the area files, each one once.

    The key holds the text: if the bot and the online Desk both got an answer before a sync,
    the sync keeps one on the Desk, and the file keeps both words.
    """
    done = {r.get("key") for r in logs.read(LOG) if r.get("kind") == "answer"}
    added = 0
    for d in desk_docs():
        text = (d.get("answer") or "").strip()
        if d.get("deleted") or not text or d.get("area") not in AREAS or not str(d["id"]).startswith("q-"):
            continue
        if _answer_key(d["id"], text) in done:
            continue
        try:
            when = datetime.fromtimestamp(float(d["answeredAt"]) / 1000, paths.HCMC)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            when = paths.now()
        _write_answer(d["area"], d.get("question") or "", text, when, d["id"])
        added += 1
    return added


def skip():
    """Closes the open question without writing anything."""
    with _locked():
        state = load_state()
        state["open"] = False
        _save_state(state)
        _close_on_desk(state)


def _close_on_desk(state):
    if state.get("askedAt"):
        doc = _desk_doc(_question_id(state["askedAt"][:10]))
        if doc and not _closed(doc):
            _desk_put({**doc, "skipped": True})


def entries(month):
    """Answers of a month ("YYYY-MM") as [{"area", "day", "text"}]. The question lines are left out
    so that words of the question itself never count as a pattern. Hand-written notes are ignored."""
    out = []
    for area in AREAS:
        cur = None
        for line in (md.read(area_file(area)) or "").splitlines():
            m = HEADING.match(line)
            if m:
                cur = {"area": area, "day": m.group(1), "text": ""}
                if m.group(1).startswith(month + "-"):
                    out.append(cur)
            elif cur is not None and not line.startswith(">"):
                cur["text"] += line + "\n"
    return out


def _feelings(text, tone):
    return [name for name, words in FEELINGS[tone].items() if any(w in text for w in words)]


def _week(day):
    return datetime.strptime(day, "%Y-%m-%d").isocalendar()[:2]


def _feeling_patterns(items):
    found = []
    for tone in FEELINGS:
        weeks = {}  # (week, area) -> feeling names
        for e in items:
            names = _feelings(e["text"], tone)
            if names:
                weeks.setdefault((_week(e["day"]), e["area"]), set()).update(names)
        pairs = {}  # (area, area) -> {"weeks", "a", "b"}
        for (week, a), names_a in weeks.items():
            for (other, b), names_b in weeks.items():
                if other == week and list(AREAS).index(a) < list(AREAS).index(b):
                    p = pairs.setdefault((a, b), {"weeks": set(), "a": set(), "b": set()})
                    p["weeks"].add(week)
                    p["a"] |= names_a
                    p["b"] |= names_b
        for (a, b), p in pairs.items():
            if len(p["weeks"]) >= MIN_WEEKS:
                found.append((len(p["weeks"]), f"{AREAS[a]}の「{'・'.join(sorted(p['a']))}」と、"
                              f"{AREAS[b]}の「{'・'.join(sorted(p['b']))}」が、同じ週に{len(p['weeks'])}回出ています。"))
    return [t for _, t in sorted(found, key=lambda x: -x[0])]


def _word_patterns(items):
    seen = {}  # word -> {area: count}
    for e in items:
        for w in set(WORD.findall(e["text"])) - COMMON_WORDS:
            seen.setdefault(w, {}).setdefault(e["area"], 0)
            seen[w][e["area"]] += 1
    shared = [(w, a) for w, a in seen.items() if len(a) >= 2]
    shared.sort(key=lambda x: (-len(x[1]), -sum(x[1].values()), x[0]))
    return [f"「{w}」という言葉が、{'と'.join(AREAS[k] for k in AREAS if k in a)}に出ています。" for w, a in shared]


def find_patterns(items):
    """Patterns across areas. Words only, no meaning: replace it by passing finder= to insight()."""
    return (_feeling_patterns(items) + _word_patterns(items))[:MAX_PATTERNS]


def previous_month(now):
    return (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def insight(month, finder=find_patterns):
    """The month's summary text, or None when there is nothing across areas to show."""
    items = entries(month)
    found = list(finder(items))[:MAX_PATTERNS]
    if not found:
        return None
    lines = [f"{month} の人生マップ（答え {len(items)} 件）"] + ["・" + p for p in found]
    lines.append("言葉の一致で拾ったものです。当てはまらなければ流してください。")
    return "\n".join(lines)


def _monthly(state, now, send):
    """Once a month, the previous month's patterns. A quiet month sends nothing at all."""
    month = previous_month(now)
    if state.get("lastInsight") == month:
        return None
    state["lastInsight"] = month
    _save_state(state)
    text = insight(month)
    if not text:
        return None
    md.save(f"life/insights/{month}.md", f"# {month} の気づき\n\n{text}\n")
    _desk_put({"id": "insight-" + month, "month": month, "text": text, "deleted": False})
    (send or telegram.notify)(text)
    return text


def set_paused(paused):
    with _locked():
        state = load_state()
        state["paused"] = bool(paused)
        if paused:
            state["open"] = False
            _close_on_desk(state)
        _save_state(state)
