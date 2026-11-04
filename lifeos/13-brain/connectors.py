"""The outside world, as functions that can be swapped. Nothing is connected yet.

The accounting and customer-management services are the owner's decision. When one is chosen,
write its function here and register it at the bottom of this file with use().

Reading:
  gmail(since), whatsapp(since) -> interactions, in the shape contacts.ingest() takes
  invoices(), payments()        -> rows, in the shape reconcile.reconcile() takes
Writing to the customer-management service:
  crm_push(contact)
Anything that moves money is not here: it is declared with approvals.money_action(kind).
"""

NAMES = ("gmail", "whatsapp", "invoices", "payments", "crm_push")
LABELS = {"gmail": "Gmail", "whatsapp": "WhatsApp", "invoices": "会計の請求", "payments": "会計の入金", "crm_push": "顧客管理"}

_impl = {}


class NotConnected(Exception):
    """This connection does not exist yet."""


def use(name, fn):
    """Connects a function, or disconnects with None."""
    if name not in NAMES:
        raise ValueError("知らない接続先です: " + name)
    if fn is None:
        _impl.pop(name, None)
    else:
        _impl[name] = fn


def is_connected(name):
    return name in _impl


def call(name, *args):
    if name not in _impl:
        raise NotConnected(LABELS.get(name, name) + " は未接続です")
    return _impl[name](*args)


def state():
    return [(LABELS[n], is_connected(n)) for n in NAMES]
