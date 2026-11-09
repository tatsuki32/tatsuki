"""サブスク自作: list what is being paid, judge what could be self-made, follow the trial.

Data: <data>/tools/18-subs/subs.json and scope.json. Only derived facts are stored (service,
amount, date, mail id), never a mail's subject or body.

Nothing here cancels anything or talks to a service. The furthest it goes is a Desk task
proposing that the person cancels; cancelled() only records that they did.
"""
import fcntl
import json
import os
import re
import statistics
import tempfile
from contextlib import contextmanager
from datetime import date, timedelta

from lifeos.lib import md, paths, tasks, tools

SOURCE = "lifeos-18"
INTERVAL_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
INTERVAL_JA = {"monthly": "毎月", "quarterly": "3か月ごと", "yearly": "毎年"}
# days after the last charge before a subscription counts as probably stopped
STALE_DAYS = {"monthly": 45, "quarterly": 110, "yearly": 400, None: 45}
ZERO_DECIMAL = ("JPY", "VND")

STATUS_JA = {"found": "未決定", "keep": "そのまま", "replace": "置き換える", "trial": "試用中",
             "proposed": "解約の提案中", "cancelled": "解約済み", "ignore": "対象外"}
# statuses that are still being paid for
PAYING = ("found", "keep", "replace", "trial", "proposed")

DIFFICULTY_ORDER = ("易", "中", "難", "未判定", "不可")
# category: (label, difficulty, why). Generic words only: no service names in the repository.
CATEGORIES = {
    "content": ("動画・音楽・本などの配信", "不可", "中身の権利が相手にある"),
    "ai": ("AI・モデルの利用", "不可", "中身のモデルは自作できない"),
    "infra": ("保存・サーバー・通信", "難", "止まらず動かす手間が大きい"),
    "app": ("多機能なアプリ", "中", "使う機能だけなら作れる"),
    "automation": ("自動化・連携・通知", "中", "接続先ごとに作る手間がある"),
    "simple": ("メモ・タスク・習慣・記録", "易", "手元のファイルと画面で足りる"),
}
CATEGORY_WORDS = (
    ("content", ("video", "music", "movie", "streaming", "kindle", "動画", "音楽", "映画", "配信", "電子書籍")),
    ("ai", (" ai", "gpt", "llm", "人工知能")),
    ("infra", ("storage", "cloud", "hosting", "vpn", "domain", "server", "ストレージ", "サーバー", "ドメイン")),
    ("automation", ("automation", "zap", "webhook", "自動化", "連携")),
    ("simple", ("note", "todo", "task", "habit", "tracker", "bookmark", "メモ", "ノート", "タスク", "習慣", "家計")),
)

# what counts as "used it for a month"; see README
TRIAL_MIN_DAYS_USED = 4
TRIAL_RECENT_DAYS = 14
MAX_OPEN_PROPOSALS = 3

_NUM = r"(\d[\d.,]*)"
_CUR = {"¥": "JPY", "￥": "JPY", "円": "JPY", "jpy": "JPY", "$": "USD", "us$": "USD", "usd": "USD", "ドル": "USD",
        "€": "EUR", "eur": "EUR", "ユーロ": "EUR", "£": "GBP", "gbp": "GBP",
        "₫": "VND", "đ": "VND", "vnd": "VND", "ドン": "VND"}
_AMOUNT = re.compile(r"(?P<pre>us\$|¥|￥|\$|€|£|₫|jpy|usd|eur|gbp|vnd)\s*" + _NUM + r"|" + _NUM +
                     r"\s*(?P<post>円|ドル|ユーロ|ドン|₫|đ|jpy|usd|eur|gbp|vnd)", re.I)
_TOTAL = re.compile(r"(合計|請求額|ご請求|お支払い金額|お支払金額|ご利用金額|月額|年額|(?<!sub)total|amount|charged)[^\d]{0,15}$", re.I)
_BILL = re.compile(r"領収|請求|ご利用料金|定期|更新|サブスク|月額|年額|receipt|invoice|subscription|renewal|payment|billing", re.I)
# only the subject: a footer's "refund policy" must not throw a real receipt away
_NOT_A_CHARGE = re.compile(r"返金|払い戻し|失敗|できませんでした|無料|refund|failed|declined|free trial", re.I)
_YEARLY = re.compile(r"年額|年間|毎年|年払い|annual|yearly|per year|/\s*yr|/\s*year", re.I)
_MONTHLY = re.compile(r"月額|毎月|月払い|monthly|per month|/\s*mo", re.I)
_SECOND_LEVEL = ("co", "ne", "or", "ac", "com", "net", "org", "go")


class ScopeNotDecided(Exception):
    """The person has not decided what to look through, so no mail may be read."""


class NotFound(Exception):
    pass


class WrongState(Exception):
    pass


# ---------- storage ----------

def _file(name):
    return paths.data_path("tools", "18-subs", name)


def _read_json(name, default):
    try:
        with open(_file(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _write_json(name, value):
    p = _file(name)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".subs", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


@contextmanager
def _changing():
    """Yields the {id: sub} store under a lock and saves it afterwards, unless an error was raised."""
    with open(_file("subs.json.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        store = _read_json("subs.json", {})
        yield store
        _write_json("subs.json", store)


def load():
    return {k: v for k, v in _read_json("subs.json", {}).items() if not v.get("deleted")}


# ---------- scope (the person's decision; nothing is read before it) ----------

def get_scope():
    return _read_json("scope.json", None)


def set_scope(months, keywords=None, exclude_senders=None, platform_senders=None):
    months = int(months)
    if months < 1:
        raise ValueError("期間は1か月以上にしてください")
    scope = {"months": months, "keywords": keywords or [], "exclude_senders": exclude_senders or [],
             "platform_senders": platform_senders or [], "decidedAt": paths.today()}
    _write_json("scope.json", scope)
    return scope


def require_scope():
    scope = get_scope()
    if not scope:
        raise ScopeNotDecided("洗い出す範囲がまだ決まっていません。先に scope で決めてください")
    return scope


# ---------- reading a billing mail ----------

def _to_number(raw, currency):
    raw = raw.rstrip(".,")
    if currency in ZERO_DECIMAL:
        return float(re.sub(r"[.,]", "", raw))
    m = re.search(r"[.,](\d{1,2})$", raw)
    whole = re.sub(r"[.,]", "", raw[:m.start()] if m else raw)
    return float(whole + ("." + m.group(1) if m else ""))


def find_amounts(text):
    """[(amount, currency, is_labelled_total)] for every money amount in the text."""
    out = []
    for m in _AMOUNT.finditer(text or ""):
        cur = _CUR[(m.group("pre") or m.group("post")).lower()]
        raw = m.group(2) or m.group(3)
        try:
            value = _to_number(raw, cur)
        except ValueError:
            continue
        out.append((value, cur, bool(_TOTAL.search(text[max(0, m.start() - 30):m.start()]))))
    return out


def pick_amount(text):
    """(amount, currency, sure). Prefers a labelled total; a guess among several is not "sure"."""
    found = [a for a in find_amounts(text) if a[0] > 0]
    if not found:
        return None, None, False
    labelled = [a for a in found if a[2]]
    best = max(labelled or found, key=lambda a: a[0])
    sure = bool(labelled) or len({(a[0], a[1]) for a in found}) == 1
    return best[0], best[1], sure


def _sender(raw):
    """(display name, domain) from 'Name <a@b.example.com>'."""
    m = re.search(r"([^<\s@]+)@([\w.-]+)", raw or "")
    domain = m.group(2).lower() if m else ""
    name = re.sub(r"<[^>]*>", "", raw or "").strip().strip('"')
    return ("" if "@" in name else name), domain


def _domain_label(domain):
    parts = domain.split(".")
    while len(parts) > 1:
        parts.pop()
        if parts[-1] not in _SECOND_LEVEL:
            break
    return parts[-1] if parts else ""


def slug(name):
    return re.sub(r"[^\w]+", "-", str(name).lower()).strip("-")


def read_message(msg, scope=None):
    """One mail -> a charge dict, or (None, reason). Text that is not a charge is dropped here."""
    subject = str(msg.get("subject") or "")
    text = subject + "\n" + str(msg.get("snippet") or "")
    hinted = msg.get("amount") is not None
    if _NOT_A_CHARGE.search(subject):
        return None, "not_a_charge"
    if not hinted and not _BILL.search(text):
        return None, "not_a_bill"
    day = str(msg.get("date") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return None, "no_date"
    if hinted:
        amount, currency, sure = float(msg["amount"]), str(msg.get("currency") or "").upper(), True
    else:
        amount, currency, sure = pick_amount(text)
    if not amount or amount <= 0 or not currency:
        return None, "no_amount"
    display, domain = _sender(msg.get("from"))
    service = msg.get("service")
    platforms = (scope or {}).get("platform_senders") or []
    if not service and any(domain == p or domain.endswith("." + p) for p in platforms):
        # a store or payment company bills for someone else: only a reader can name the service
        return None, "platform_needs_service"
    key = slug(service) if service else _domain_label(domain) or slug(display)
    if not key:
        return None, "no_sender"
    interval = msg.get("interval") if msg.get("interval") in INTERVAL_MONTHS else None
    if not interval:
        interval = "yearly" if _YEARLY.search(text) else "monthly" if _MONTHLY.search(text) else None
    return {"key": key, "name": service or display or key, "date": day, "amount": amount, "currency": currency,
            "mail": str(msg.get("id") or ""), "interval_hint": interval, "sure": sure}, None


def _interval_from_dates(charges):
    days = []
    for c in charges:
        d = date.fromisoformat(c["date"])
        # a receipt and an invoice for one charge arrive days apart
        if not days or (d - days[-1]).days > 3:
            days.append(d)
    if len(days) < 2:
        return None
    gap = statistics.median((b - a).days for a, b in zip(days, days[1:]))
    if 25 <= gap <= 35:
        return "monthly"
    if 80 <= gap <= 100:
        return "quarterly"
    if 350 <= gap <= 380:
        return "yearly"
    return None


def _money_round(value, currency):
    return float(round(value)) if currency in ZERO_DECIMAL else round(value, 2)


def view(sub, day=None):
    """The sub with its worked-out facts. What the person typed (manual) wins over the mails."""
    charges = sorted(sub.get("charges") or [], key=lambda c: c["date"])
    last = charges[-1] if charges else {}
    manual = sub.get("manual") or {}
    interval = manual.get("interval") or _interval_from_dates(charges) or sub.get("interval_hint")
    amount = manual.get("amount", last.get("amount"))
    currency = manual.get("currency", last.get("currency"))
    monthly = None
    if amount is not None and interval in INTERVAL_MONTHS:
        monthly = _money_round(amount / INTERVAL_MONTHS[interval], currency)
    last_day = last.get("date")
    today = date.fromisoformat(day or paths.today())
    stale = bool(last_day) and not manual.get("amount") and \
        (today - date.fromisoformat(last_day)).days > STALE_DAYS[interval]
    sure = monthly is not None and (bool(manual) or bool(last.get("sure")))
    return {**sub, "name": manual.get("name") or sub.get("name"), "amount": amount, "currency": currency,
            "interval": interval, "monthly": monthly, "last_charge": last_day, "stale": stale, "sure": sure}


# ---------- 洗い出し ----------

def _new_sub(key, name, source):
    return {"id": key, "name": name, "source": source, "status": "found", "charges": [], "manual": {},
            "createdAt": paths.today()}


def ingest(messages):
    """Adds mails to the list. Repeats are ignored; the person's edits and decisions are kept."""
    scope = require_scope()
    result = {"charges": 0, "new": 0, "repeat": 0, "skipped": {}}
    with _changing() as store:
        for msg in messages:
            charge, why = read_message(msg, scope)
            if not charge:
                result["skipped"][why] = result["skipped"].get(why, 0) + 1
                continue
            key = charge.pop("key")
            name = charge.pop("name")
            hint = charge.pop("interval_hint")
            sub = store.get(key)
            if sub is None:
                sub = store[key] = _new_sub(key, name, "mail")
                result["new"] += 1
            seen = any(charge["mail"] and c.get("mail") == charge["mail"] for c in sub["charges"])
            same = any((c["date"], c["amount"]) == (charge["date"], charge["amount"]) for c in sub["charges"])
            if seen or same:
                result["repeat"] += 1
                continue
            sub["charges"].append(charge)
            if hint:
                sub["interval_hint"] = hint
            result["charges"] += 1
    return result


def add_manual(name, amount, currency="JPY", interval="monthly"):
    """Adds one by hand. Works with no mail connection at all."""
    if interval not in INTERVAL_MONTHS:
        raise ValueError("支払いの間隔は monthly / quarterly / yearly のどれか")
    key = slug(name)
    if not key:
        raise ValueError("名前が空です")
    with _changing() as store:
        sub = store.get(key)
        if sub is None or sub.get("deleted"):
            sub = store[key] = _new_sub(key, name, "manual")
        sub["manual"].update({"name": name, "amount": float(amount), "currency": currency.upper(), "interval": interval})
    return view(sub)


def _find(store, ref):
    live = {k: v for k, v in store.items() if not v.get("deleted")}
    if ref in live:
        return live[ref]
    wanted = slug(ref)
    hits = [v for k, v in live.items() if k == wanted or slug(view(v).get("name") or "") == wanted]
    hits = hits or [v for k, v in live.items() if wanted and k.startswith(wanted)]
    if len(hits) != 1:
        raise NotFound("「{}」に当たるサブスクが{}".format(ref, "複数あります" if hits else "ありません"))
    return hits[0]


def get(ref):
    return view(_find(load(), ref))


def edit(ref, name=None, amount=None, currency=None, interval=None):
    """Corrects what the mails got wrong. Stays corrected across later scans."""
    if interval is not None and interval not in INTERVAL_MONTHS:
        raise ValueError("支払いの間隔は monthly / quarterly / yearly のどれか")
    with _changing() as store:
        sub = _find(store, ref)
        new = {"name": name, "amount": None if amount is None else float(amount),
               "currency": currency.upper() if currency else None, "interval": interval}
        sub["manual"].update({k: v for k, v in new.items() if v is not None})
    return view(sub)


def remove(ref):
    """Takes it off the list (deleted:true). The caller must have the person's approval."""
    with _changing() as store:
        sub = _find(store, ref)
        sub["deleted"] = True
    return sub


def current(day=None):
    """What is being paid now. Unsure and probably-stopped ones are listed apart, not summed."""
    paying, unsure, stale = [], [], []
    for sub in load().values():
        v = view(sub, day)
        if v.get("status") not in PAYING:
            continue
        (stale if v["stale"] else paying if v["sure"] else unsure).append(v)
    return {"paying": sorted(paying, key=lambda v: -(v["monthly"] or 0)), "unsure": unsure, "stale": stale}


def totals(subs):
    out = {}
    for v in subs:
        if v.get("monthly") is not None:
            out[v["currency"]] = _money_round(out.get(v["currency"], 0) + v["monthly"], v["currency"])
    return out


def money(value, currency):
    if value is None:
        return "?"
    return "{:,.0f} {}".format(value, currency) if currency in ZERO_DECIMAL else "{:,.2f} {}".format(value, currency)


def list_text(day=None):
    now = current(day)
    if not any(now.values()):
        return "まだ何もありません。/subs で洗い出すか、add で手で足せます。"
    lines = []
    if now["paying"]:
        lines.append("今払っているもの")
        for v in now["paying"]:
            lines.append("- {}（{}）：月 {}／最後の請求 {}／{}".format(
                v["name"], v["id"], money(v["monthly"], v["currency"]), v["last_charge"] or "手入力", STATUS_JA[v["status"]]))
        lines.append("月の合計：" + "、".join(money(a, c) for c, a in sorted(totals(now["paying"]).items())))
    if now["unsure"]:
        lines.append("確かめてほしいもの（合計に入れていません。edit で直せます）")
        for v in now["unsure"]:
            lines.append("- {}（{}）：{}／間隔 {}".format(
                v["name"], v["id"], money(v["amount"], v["currency"]), INTERVAL_JA.get(v["interval"], "不明")))
    if now["stale"]:
        lines.append("もう止まっているかもしれないもの（合計に入れていません）")
        for v in now["stale"]:
            lines.append("- {}（{}）：最後の請求 {}".format(v["name"], v["id"], v["last_charge"]))
    return "\n".join(lines)


# ---------- 判定 ----------

def guess_category(name):
    text = " " + str(name or "").lower()
    for category, words in CATEGORY_WORDS:
        if any(w in text for w in words):
            return category
    return None


def judge_one(v):
    """difficulty and saving for one sub. An unknown service stays 未判定, it is not guessed at."""
    category = v.get("category") or guess_category(v.get("name"))
    label, difficulty, why = CATEGORIES.get(category, ("不明", "未判定", "用途を教えてください"))
    difficulty = v.get("difficulty") or difficulty
    why = v.get("judge_note") or why
    saving = None
    if v.get("monthly") is not None and difficulty != "不可":
        saving = _money_round(max(v["monthly"] - float(v.get("run_cost") or 0), 0), v["currency"])
    return {"id": v["id"], "name": v.get("name"), "category": label, "difficulty": difficulty, "why": why,
            "monthly": v.get("monthly"), "saving": saving, "currency": v.get("currency"), "status": v.get("status")}


def judge_table(day=None):
    now = current(day)
    rows = [judge_one(v) for v in now["paying"] + now["unsure"]]
    return sorted(rows, key=lambda r: (DIFFICULTY_ORDER.index(r["difficulty"]), r["currency"] or "", -(r["saving"] or 0)))


def set_judgement(ref, category=None, difficulty=None, run_cost=None, note=None):
    if category is not None and category not in CATEGORIES:
        raise ValueError("用途は " + " / ".join(CATEGORIES) + " のどれか")
    if difficulty is not None and difficulty not in DIFFICULTY_ORDER:
        raise ValueError("難易度は " + " / ".join(DIFFICULTY_ORDER) + " のどれか")
    with _changing() as store:
        sub = _find(store, ref)
        new = {"category": category, "difficulty": difficulty, "judge_note": note,
               "run_cost": None if run_cost is None else float(run_cost)}
        sub.update({k: v for k, v in new.items() if v is not None})
    return judge_one(view(sub))


def table_text(day=None):
    rows = judge_table(day)
    if not rows:
        return "判定するものがまだありません。"
    out = ["| サービス | 用途 | 難易度 | 月額 | 節約（月） | 節約（年） | 理由 | 状態 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        yearly = None if r["saving"] is None else r["saving"] * 12
        out.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["name"], r["category"], r["difficulty"], money(r["monthly"], r["currency"]),
            "—" if r["saving"] is None else money(r["saving"], r["currency"]),
            "—" if yearly is None else money(yearly, r["currency"]), r["why"], STATUS_JA[r["status"]]))
    return "\n".join(out)


def save_report(day=None):
    """Writes the list and the table next to the data, for reading outside the terminal."""
    return md.save("tools/18-subs/report.md", "# サブスク自作（{}）\n\n{}\n\n## 判定\n\n{}".format(
        day or paths.today(), list_text(day), table_text(day)))


# ---------- 作成（09番へ渡す） ----------

def newtool_line(ref):
    """The line to hand to 09's /newtool: `/newtool 名前 やりたいこと`.

    09 writes both into tool.json and README in the public repository, so the service's name
    stays out of it: the line is built from the purpose only.
    """
    v = get(ref)
    purpose = " ".join(str(v.get("purpose") or "").split())
    if not purpose:
        return None
    return "/newtool {} {}".format(("自作" + re.sub(r"\s+", "", purpose))[:40], purpose)


def tool_exists(name):
    return any(tools.info(d).get("name") == name for d in tools.tool_dirs())


def newtool_ready():
    return os.path.exists(os.path.join(paths.REPO, ".claude", "commands", "newtool.md"))


def decide(ref, choice, purpose=None):
    """replace / keep / ignore. Only changes this list; building is the person's next step."""
    if choice not in ("replace", "keep", "ignore"):
        raise ValueError("replace / keep / ignore のどれか")
    with _changing() as store:
        sub = _find(store, ref)
        if sub["status"] in ("proposed", "cancelled"):
            raise WrongState("{}は「{}」なので決め直せません".format(sub["id"], STATUS_JA[sub["status"]]))
        sub["status"] = choice
        if purpose:
            sub["purpose"] = purpose
    return view(sub)


# ---------- 1か月の試用と解約の提案 ----------

def _add_month(d):
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def start_trial(ref, tool, day=None):
    """The self-made tool is ready and in use: the month starts counting."""
    with _changing() as store:
        sub = _find(store, ref)
        if sub["status"] != "replace":
            raise WrongState("先に decide で「置き換える」にしてください")
        sub["status"] = "trial"
        sub["trial"] = {"tool": tool, "started": day or paths.today(), "used": [], "confirmed": False}
    return view(sub)


def used(ref, day=None):
    day = day or paths.today()
    with _changing() as store:
        sub = _find(store, ref)
        if sub["status"] != "trial":
            raise WrongState("試用中ではありません")
        if day not in sub["trial"]["used"]:
            sub["trial"]["used"] = sorted(sub["trial"]["used"] + [day])
    return view(sub)


def confirm_month(ref):
    """The person says it did the job for the month, whatever the log of used days shows."""
    with _changing() as store:
        sub = _find(store, ref)
        if sub["status"] != "trial":
            raise WrongState("試用中ではありません")
        sub["trial"]["confirmed"] = True
    return view(sub)


def give_up(ref):
    """The self-made one did not fit. The subscription stays; nothing is lost."""
    with _changing() as store:
        sub = _find(store, ref)
        if sub["status"] != "trial":
            raise WrongState("試用中ではありません")
        sub["status"] = "keep"
        sub["trial"]["ended"] = paths.today()
    return view(sub)


def trial_state(v, day=None):
    """(state, text). state: waiting (under a month) / ask (a month, not enough record) / ok."""
    trial = v["trial"]
    today = date.fromisoformat(day or paths.today())
    due = _add_month(date.fromisoformat(trial["started"]))
    if today < due:
        return "waiting", "{} まで試用（あと{}日）".format(due.isoformat(), (due - today).days)
    if trial.get("confirmed"):
        return "ok", "1か月使えたと確かめました"
    recent = [d for d in trial["used"] if (today - date.fromisoformat(d)).days <= TRIAL_RECENT_DAYS]
    if len(trial["used"]) >= TRIAL_MIN_DAYS_USED and recent:
        return "ok", "1か月のあいだに{}日使いました".format(len(trial["used"]))
    return "ask", "1か月たちました。足りていたら confirm、合わなければ giveup で教えてください"


def _proposal_title(v):
    return "Life OS 18：解約を検討（{}）".format(v["name"])


def check(day=None):
    """Turns finished trials into Desk tasks proposing a cancellation. Cancels nothing."""
    open_titles = {t.get("title") for t in tasks.open_tasks()}
    made, waiting = [], []
    with _changing() as store:
        live = [s for s in store.values() if not s.get("deleted")]
        open_now = sum(1 for s in live if s["status"] == "proposed")
        for sub in live:
            if sub["status"] != "trial":
                continue
            v = view(sub, day)
            state, text = trial_state(v, day)
            if state != "ok" or open_now >= MAX_OPEN_PROPOSALS:
                waiting.append({"id": v["id"], "name": v["name"], "state": state, "text": text})
                continue
            title = _proposal_title(v)
            if title not in open_titles:
                task = tasks.add(title, source=SOURCE, note=(
                    "自作の「{}」で1か月使えました。{}。解約は本人が行います。Claude もコードも解約しません。"
                    "解約したら subs.py cancelled {} で記録します。残すなら、このタスクを閉じるだけで構いません。").format(
                        sub["trial"]["tool"], text, sub["id"]))
                sub["proposal_task"] = task["id"]
            sub["status"] = "proposed"
            sub["proposedAt"] = day or paths.today()
            open_now += 1
            made.append({"id": v["id"], "name": v["name"], "saving": judge_one(v)["saving"], "currency": v["currency"]})
    return {"proposed": made, "waiting": waiting}


def cancelled(ref):
    """Records that the person cancelled it themselves. Contacts nobody."""
    with _changing() as store:
        sub = _find(store, ref)
        if sub["status"] != "proposed":
            raise WrongState("解約の提案中のものだけ記録できます")
        sub["status"] = "cancelled"
        sub["cancelledAt"] = paths.today()
        task_id = sub.get("proposal_task")
    if task_id and tasks.get(task_id):
        tasks.done(task_id)
    return view(sub)


def stop_proposal(ref):
    """The person decided to keep paying after all. The proposal is not raised again."""
    with _changing() as store:
        sub = _find(store, ref)
        if sub["status"] != "proposed":
            raise WrongState("解約の提案中ではありません")
        sub["status"] = "keep"
        task_id = sub.get("proposal_task")
    if task_id and tasks.get(task_id):
        tasks.update(task_id, status="archived")
    return view(sub)


def brief_lines(day=None):
    """For the morning brief. Reads only; at most 3 lines."""
    out = []
    for sub in load().values():
        v = view(sub, day)
        if v["status"] == "proposed":
            out.append("サブスク：{}の解約を検討（解約はご自身で）".format(v["name"]))
        elif v["status"] == "trial" and trial_state(v, day)[0] != "waiting":
            out.append("サブスク：{}の自作が1か月たちました".format(v["name"]))
    return out[:3]
