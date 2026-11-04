"""The gate in front of anything that moves money.

  request -> pending -> (owner approves) approved -> execute() -> executing -> done / failed
                     -> rejected                    expired, if nobody acted in time

- A money function is declared with @money_action(kind). Called any other way, it refuses.
- Approving means typing the amount. What was approved is fixed by a digest: a changed amount,
  target or payload cancels the approval.
- An approval runs once. A failure or a crash is never retried by itself, because the money may
  have moved: a retry is a new request that needs a new approval.
- Every step is appended to logs/brain-money.jsonl.

This stops mistakes and stray automation, not someone who can edit these files on purpose.
"""
import functools
import hashlib
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal

from lifeos.lib import logs, paths
from lifeos.lib.tools import load_tool

store = load_tool("13-brain", "store")
reconcile = load_tool("13-brain", "reconcile")
connectors = load_tool("13-brain", "connectors")

FILE = "approvals.json"
LOG = "brain-money"
TTL_HOURS = 72
KINDS = {"accounting.apply_payment": "入金を請求に消し込む", "accounting.write": "会計に書き込む", "payment.send": "送金する"}
STATUS_JA = {"pending": "承認待ち", "approved": "承認済み", "rejected": "却下", "executing": "実行中か結果不明", "done": "実行済み", "failed": "失敗", "expired": "期限切れ", "retried": "やり直し済み"}
OPEN = ("pending", "approved", "executing", "failed")

_executors = {}
_live = {}  # tickets that exist only while execute() is calling the money function


class Refused(Exception):
    """The action was not allowed. Nothing moved."""


class Ticket:
    def __init__(self, approval_id, kind, digest):
        self.approval_id, self.kind, self.digest, self.nonce = approval_id, kind, digest, os.urandom(8).hex()


def money_action(kind):
    """Declares a function that moves money. It runs only with a ticket from execute()."""
    if kind not in KINDS:
        raise ValueError("知らない種類です: " + kind)

    def deco(fn):
        @functools.wraps(fn)
        def guarded(action, ticket=None):
            live = _live.get(ticket.nonce) if isinstance(ticket, Ticket) else None
            if live is None or live.kind != kind or live.digest != _digest(action):
                raise Refused("承認なしでは実行できません")
            return fn(action)
        _executors[kind] = guarded
        return guarded
    return deco


def disconnect(kind):
    _executors.pop(kind, None)


def is_connected(kind):
    return kind in _executors


def _digest(action):
    fixed = {k: action.get(k) for k in ("kind", "amount", "currency", "target", "payload")}
    return hashlib.sha256(json.dumps(fixed, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _load():
    v = store.read_json(FILE, [])
    if not isinstance(v, list):
        raise store.StoreError(FILE + " の形が違います")
    return v


def _log(rec, event, **extra):
    logs.append(LOG, {"id": rec["id"], "kind": rec["kind"], "event": event, "amount": rec["amount"], "currency": rec["currency"], **extra})


def _is_expired(rec):
    return rec["status"] in ("pending", "approved") and paths.now() > datetime.fromisoformat(rec["expires"])


def _view(rec):
    return {**rec, "status": "expired"} if _is_expired(rec) else rec


def _find(items, approval_id):
    for r in items:
        if r["id"] == approval_id:
            return r
    raise Refused("この承認番号はありません: " + str(approval_id))


def _check_intact(rec):
    if _digest(rec) != rec["digest"]:
        raise Refused("承認の中身が変わっています。実行しません")


def all_requests():
    return [_view(r) for r in _load()]


def open_requests():
    return [r for r in all_requests() if r["status"] in OPEN]


def get(approval_id):
    return next((r for r in all_requests() if r["id"] == approval_id), None)


def request(kind, amount, currency, target, payload=None, summary="", key="", retry_of=None):
    """Asks for approval and returns the request. Nothing moves here.

    key names the real-world action. Asking again with the same key returns the earlier
    request, so one payment cannot be applied twice by asking twice.
    """
    store.require_running()
    if kind not in KINDS:
        raise Refused("知らない種類です: " + str(kind))
    value = reconcile.to_amount(amount)
    if value is None or value <= 0:
        raise Refused("金額が正しくありません")
    currency = str(currency or "").strip().upper()
    if not currency or not str(target or "").strip():
        raise Refused("通貨と相手が必要です")
    now = paths.now()
    rec = {"id": "m" + os.urandom(3).hex(), "kind": kind, "amount": str(value), "currency": currency, "target": str(target).strip(),
           "payload": payload or {}, "summary": " ".join(str(summary).split())[:200], "key": key, "status": "pending", "retry_of": retry_of,
           "created": now.isoformat(timespec="seconds"), "expires": (now + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds")}
    rec["digest"] = _digest(rec)
    with store.locked("approvals"):
        items = _load()
        if key:
            for old in items:
                if old["key"] != key or old["id"] == retry_of or _view(old)["status"] in ("rejected", "expired", "retried"):
                    continue
                if old["status"] in ("failed", "executing"):
                    raise Refused("前回の結果が不明です。確かめてから retry してください: " + old["id"])
                return old
        if retry_of:
            _find(items, retry_of)["status"] = "retried"
        items.append(rec)
        store.write_json(FILE, items)
    _log(rec, "requested", retry_of=retry_of)
    return rec


def retry(approval_id):
    """A new request for a failed or unfinished one. The owner checks first whether money moved."""
    old = get(approval_id)
    if not old or old["status"] not in ("failed", "executing"):
        raise Refused("やり直せるのは失敗か結果不明のものだけです")
    return request(old["kind"], old["amount"], old["currency"], old["target"], old["payload"], old["summary"], old["key"], retry_of=approval_id)


def approve(approval_id, typed_amount):
    """The owner's yes. typed_amount has to equal the amount, so nobody approves without reading it."""
    store.require_running()
    with store.locked("approvals"):
        items = _load()
        rec = _find(items, approval_id)
        if _is_expired(rec):
            raise Refused("承認の期限が切れています")
        if rec["status"] != "pending":
            raise Refused("承認待ちではありません：" + STATUS_JA.get(rec["status"], rec["status"]))
        _check_intact(rec)
        typed = reconcile.to_amount(typed_amount)
        if typed is None or typed != Decimal(rec["amount"]):
            raise Refused("金額が一致しません。承認していません")
        rec["status"] = "approved"
        rec["approved_at"] = paths.now().isoformat(timespec="seconds")
        store.write_json(FILE, items)
    _log(rec, "approved")
    return rec


def reject(approval_id, reason=""):
    with store.locked("approvals"):
        items = _load()
        rec = _find(items, approval_id)
        if rec["status"] not in ("pending", "approved"):
            raise Refused("却下できません：" + STATUS_JA.get(rec["status"], rec["status"]))
        rec["status"] = "rejected"
        store.write_json(FILE, items)
    _log(rec, "rejected", reason=" ".join(str(reason).split())[:200])
    return rec


def execute(approval_id):
    """Runs an approved action once. The status is taken before the call, so nothing can run it twice."""
    store.require_running()
    with store.locked("approvals"):
        items = _load()
        rec = _find(items, approval_id)
        if _is_expired(rec):
            raise Refused("承認の期限が切れています")
        if rec["status"] != "approved":
            raise Refused("承認されていません：" + STATUS_JA.get(rec["status"], rec["status"]))
        _check_intact(rec)
        fn = _executors.get(rec["kind"])
        if fn is None:  # the approval stays valid until it expires
            raise connectors.NotConnected(KINDS[rec["kind"]] + " は未接続です。お金は動いていません")
        rec["status"] = "executing"
        store.write_json(FILE, items)
    _log(rec, "executing")
    ticket = Ticket(rec["id"], rec["kind"], rec["digest"])
    _live[ticket.nonce] = ticket
    try:
        result = fn(dict(rec), ticket)
    except Exception as e:
        _finish(rec["id"], "failed", error=type(e).__name__ + ": " + str(e)[:200])
        raise
    finally:
        _live.pop(ticket.nonce, None)
    return _finish(rec["id"], "done", result=str(result)[:200])


def _finish(approval_id, status, **fields):
    with store.locked("approvals"):
        items = _load()
        rec = _find(items, approval_id)
        rec.update(status=status, finished_at=paths.now().isoformat(timespec="seconds"), **fields)
        store.write_json(FILE, items)
    _log(rec, status, **fields)
    return rec


def propose_from(result):
    """Turns each match in a reconcile result into a request. Doubtful items are not proposed."""
    out = []
    for it in result["items"]:
        if it["kind"] != "matched":
            continue
        out.append(request("accounting.apply_payment", it["payment_amount"], it["currency"], it["customer"],
                           {"invoice_id": it["invoice_id"], "payment_id": it["payment_id"]},
                           summary="請求 {} に入金 {} を消し込む（{}）".format(it["invoice_id"], it["payment_id"], it["reason"]),
                           key="apply:{}:{}".format(it["invoice_id"], it["payment_id"])))
    return out
