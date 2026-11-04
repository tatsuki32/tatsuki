"""The connection to the billing mails. This file holds no mail and never sends anything.

Gmail is only reachable from a Claude session (a connector), not from plain Python, so the
/subs command reads the mails and pipes them to `subs.py import -`. Anything else that can
return the same list can replace fetch():

    mail = load_tool("18-subs", "mail")
    mail.fetch = my_function     # my_function(scope) -> list of messages

A message is a dict:
    id       the mail's id (a repeat with the same id is ignored)
    date     "YYYY-MM-DD"
    from     'Name <billing@example.com>'
    subject, snippet   text, only used to read the amount; never stored
Claude may add what it read itself; these win over what the code would work out:
    service, amount, currency, interval ("monthly" / "quarterly" / "yearly")
"""
import json
from datetime import timedelta

from lifeos.lib import paths

SUBJECT_WORDS = ("領収", "請求", "ご利用料金", "定期購入", "更新のお知らせ", "サブスクリプション",
                 "receipt", "invoice", "subscription", "renewal", "payment")


class NotConnected(Exception):
    """No connection is plugged in. Use /subs from a Claude session."""


def fetch(scope):
    raise NotConnected("メールの読み取りは /subs から行います（Gmail のコネクタが要ります）")


def queries(scope):
    """The Gmail search that matches the decided scope, as text. Reads nothing."""
    since = (paths.now() - timedelta(days=int(scope["months"]) * 31)).strftime("%Y/%m/%d")
    words = list(SUBJECT_WORDS) + list(scope.get("keywords") or [])
    q = "after:{} subject:({})".format(since, " OR ".join(words))
    for sender in scope.get("exclude_senders") or []:
        q += " -from:" + sender
    return [q]


def from_json(text):
    """Parses what the /subs command piped in. Raises ValueError unless it is a list of dicts."""
    v = json.loads(text)
    if not isinstance(v, list) or not all(isinstance(m, dict) for m in v):
        raise ValueError("メールの一覧（配列）を渡してください")
    return v
