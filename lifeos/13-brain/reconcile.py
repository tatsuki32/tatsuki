"""Matches invoices with payments. It only reads and reports: nothing is written to accounting.

Only two things count as a match: the invoice number with the same amount, or the same customer
with the same amount and no other candidate. Everything doubtful stays for the owner to look at.
An amount alone is never a match.
"""
import csv
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from lifeos.lib import paths
from lifeos.lib.tools import load_tool

contacts = load_tool("13-brain", "contacts")
store = load_tool("13-brain", "store")

LABELS = {"matched": "一致", "diff": "差額あり", "review": "要確認", "unknown": "心当たりのない入金", "unpaid": "未入金", "overdue": "期限を過ぎた未入金"}
CLOSED = {"paid", "void", "cancelled", "canceled", "済", "入金済", "入金済み", "消込済", "取消", "取り消し"}
_ID_MIN = 3  # shorter numbers turn up in dates and account numbers by chance
INVOICE_COLUMNS = {"id": ("id", "請求番号", "請求書番号", "invoice"), "customer": ("customer", "取引先", "顧客", "請求先"), "amount": ("amount", "金額", "請求額", "請求金額"),
                   "currency": ("currency", "通貨"), "issued": ("issued", "発行日", "請求日"), "due": ("due", "期日", "支払期日", "支払期限"), "org": ("org", "取引元", "会社"), "status": ("status", "状態", "ステータス")}
PAYMENT_COLUMNS = {"id": ("id", "番号", "取引番号"), "payer": ("payer", "振込人", "振込名義", "名義", "相手"), "amount": ("amount", "入金額", "金額"),
                   "currency": ("currency", "通貨"), "date": ("date", "日付", "入金日"), "memo": ("memo", "摘要", "メモ", "内容")}


def to_amount(value):
    """A Decimal from '¥1,200' or '1200.50'. None if it is not a number."""
    s = re.sub(r"[,\s¥￥$円₫]", "", unicodedata.normalize("NFKC", str(value or "")))
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return v if v.is_finite() else None


def _day(value):
    s = str(value or "").strip().replace("/", "-")
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _clean_id(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def _mentions(text, invoice_id):
    """Whether text holds the invoice number as a whole token: INV-1 is not in INV-10."""
    if len(invoice_id) < _ID_MIN:
        return False
    norm = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return re.search(r"(?<![0-9a-z])" + re.escape(invoice_id) + r"(?![0-9a-z])", norm) is not None


def _load_csv(path, columns):
    """Rows renamed to our column names, and which of the file's columns each name came from."""
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                head = {(h or "").strip().casefold(): h for h in (reader.fieldnames or [])}
                names = {ours: next((head[a] for a in options if a in head), None) for ours, options in columns.items()}
                return [{ours: (row.get(col) or "").strip() if col else "" for ours, col in names.items()} for row in reader], names
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードを読めません: " + str(path))


def _need(names, required, label):
    missing = [r for r in required if not names.get(r)]
    if missing:
        raise ValueError(label + " の列が足りません: " + ", ".join(missing))


def load_invoices(path):
    rows, names = _load_csv(path, INVOICE_COLUMNS)
    _need(names, ("id", "customer", "amount", "currency"), "請求")
    return rows


def load_payments(path):
    rows, names = _load_csv(path, PAYMENT_COLUMNS)
    _need(names, ("payer", "amount", "currency"), "入金")
    for i, r in enumerate(rows, 2):
        r["id"] = r["id"] or "line-{}".format(i)  # the line number stands in for a missing id
    return rows


def _prepare_invoices(rows, skipped):
    out, seen = [], set()
    for r in rows:
        amount = to_amount(r.get("amount"))
        key = _clean_id(r.get("id"))
        cur = str(r.get("currency") or "").strip().upper()
        if str(r.get("status") or "").strip().casefold() in CLOSED or not store.in_scope(r.get("org")):
            continue
        if not key or not cur or amount is None or amount <= 0 or key in seen:
            skipped.append({"type": "invoice", "id": r.get("id"), "reason": "番号・金額・通貨のどれかを読めないか、番号が重複しています"})
            continue
        seen.add(key)
        out.append({**r, "_key": key, "_amount": amount, "_cur": cur, "_issued": _day(r.get("issued")), "_due": _day(r.get("due"))})
    return out


def _prepare_payments(rows, skipped):
    out, seen = [], set()
    for r in rows:
        amount = to_amount(r.get("amount"))
        key = str(r.get("id") or "").strip()
        cur = str(r.get("currency") or "").strip().upper()
        if not key or not cur or amount is None or key in seen:
            skipped.append({"type": "payment", "id": r.get("id"), "reason": "番号・金額・通貨のどれかを読めないか、番号が重複しています"})
        elif amount > 0:  # a statement also lists money going out, which is not a payment received
            seen.add(key)
            out.append({**r, "_amount": amount, "_cur": cur, "_date": _day(r.get("date"))})
    return sorted(out, key=lambda p: (p["_date"] or date.max, str(p.get("id"))))


def _item(kind, reason, payment=None, invoice=None, candidates=()):
    src = invoice or {}
    pay = payment or {}
    return {"kind": kind, "label": LABELS[kind], "reason": reason,
            "invoice_id": src.get("id"), "customer": src.get("customer"), "invoice_amount": str(src["_amount"]) if invoice else None,
            "payment_id": pay.get("id"), "payer": pay.get("payer"), "payment_amount": str(pay["_amount"]) if payment else None,
            "currency": (src.get("_cur") or pay.get("_cur") or ""), "due": src.get("due") or None, "paid_on": pay.get("date") or None,
            "candidates": [c["id"] for c in candidates]}


def reconcile(invoices, payments, same_party=None, today=None):
    """Returns {"items": [...], "counts": {...}, "skipped": [...]}. Pure: reads and writes nothing."""
    same = same_party or (lambda a, b: contacts.name_key(a) == contacts.name_key(b) != "")
    today = today or paths.now().date()
    skipped = []
    open_invoices = _prepare_invoices(invoices, skipped)
    pays = _prepare_payments(payments, skipped)
    used, settled, items, rest = set(), set(), [], []

    # 1. the invoice number written on the payment
    for p in pays:
        text = " ".join((str(p.get("memo") or ""), str(p.get("payer") or "")))
        hits = [i for i in open_invoices if i["_cur"] == p["_cur"] and _mentions(text, i["_key"])]
        if not hits:
            rest.append(p)
        elif len(hits) > 1:
            items.append(_item("review", "複数の請求番号が書かれています。まとめ払いかもしれません", p, candidates=hits))
        elif hits[0]["_key"] in used:
            items.append(_item("review", "同じ請求に2回目の入金です。二重入金かもしれません", p, hits[0]))
        elif hits[0]["_amount"] == p["_amount"]:
            used.add(hits[0]["_key"])
            settled.add(hits[0]["_key"])
            items.append(_item("matched", "請求番号と金額が一致", p, hits[0]))
        else:
            used.add(hits[0]["_key"])
            items.append(_item("diff", "請求番号は一致、金額が違います", p, hits[0]))

    # 2. the same customer: a match only if one payment and one invoice point at each other
    def party_invoices(p):
        return [i for i in open_invoices if i["_key"] not in used and i["_cur"] == p["_cur"] and same(i.get("customer"), p.get("payer"))
                and not (i["_issued"] and p["_date"] and p["_date"] < i["_issued"])]

    claims = {}
    for p in rest:
        for i in party_invoices(p):
            if i["_amount"] == p["_amount"]:
                claims.setdefault(i["_key"], []).append(p)

    claimed = {}
    for p in rest:
        cands = party_invoices(p)
        exact = [i for i in cands if i["_amount"] == p["_amount"]]
        if len(exact) == 1 and len(claims[exact[0]["_key"]]) == 1:
            claimed[p["id"]] = exact[0]

    for p in rest:
        cands = party_invoices(p)
        exact = [i for i in cands if i["_amount"] == p["_amount"]]
        total = sum((i["_amount"] for i in cands), Decimal(0))
        hit = claimed.get(p["id"])
        if hit:
            used.add(hit["_key"])
            settled.add(hit["_key"])
            items.append(_item("matched", "取引先と金額が一致し、ほかに候補がありません", p, hit))
        elif exact:
            items.append(_item("review", "同じ取引先に同じ金額の候補が複数あります", p, candidates=exact))
        elif len(cands) > 1 and total == p["_amount"]:
            items.append(_item("review", "取引先の請求の合計と一致。まとめ払いかもしれません", p, candidates=cands))
        elif len(cands) == 1:
            items.append(_item("diff", "取引先は一致、金額が違います", p, cands[0]))
        elif cands:
            items.append(_item("review", "取引先は一致、どの請求か分かりません", p, candidates=cands))
        else:
            same_amount = [i for i in open_invoices if i["_key"] not in settled and i["_cur"] == p["_cur"] and i["_amount"] == p["_amount"]]
            if same_amount:
                items.append(_item("review", "金額だけ一致。振込名義を確かめてください", p, candidates=same_amount))
            else:
                items.append(_item("unknown", "当てはまる請求がありません", p))

    # 3. invoices nobody paid
    named = {c for it in items for c in it["candidates"]} | {it["invoice_id"] for it in items if it["kind"] != "matched"}
    for i in open_invoices:
        if i["_key"] in settled or i["_key"] in used:
            continue
        late = i["_due"] is not None and i["_due"] < today
        reason = "入金の候補があります" if i.get("id") in named else "入金が見つかりません"
        items.append(_item("overdue" if late else "unpaid", reason, invoice=i))

    counts = {k: sum(1 for it in items if it["kind"] == k) for k in LABELS}
    return {"items": items, "counts": counts, "skipped": skipped}


def save(result):
    """Keeps the newest result, for the brief and for proposing what to apply."""
    store.write_json("reconcile.json", {"at": paths.now().isoformat(timespec="seconds"), **result})


def latest():
    return store.read_json("reconcile.json", None)
