"""A line for the morning brief. Counts only: no names or amounts."""
from lifeos.lib.tools import load_tool


def lines(day):
    store = load_tool("13-brain", "store")
    if store.is_stopped():
        return []
    approvals = load_tool("13-brain", "approvals")
    reconcile = load_tool("13-brain", "reconcile")
    out = []
    waiting = sum(1 for r in approvals.open_requests() if r["status"] == "pending")
    unsure = sum(1 for r in approvals.open_requests() if r["status"] in ("failed", "executing"))
    if waiting:
        out.append("お金の承認待ち：{}件".format(waiting))
    if unsure:
        out.append("お金の操作で結果を確かめたいもの：{}件".format(unsure))
    last = reconcile.latest()
    if last:
        c = last["counts"]
        look = c["review"] + c["diff"] + c["unknown"]
        if look:
            out.append("入金の照合で見てほしいもの：{}件".format(look))
        if c["overdue"]:
            out.append("期限を過ぎた未入金：{}件".format(c["overdue"]))
    return out
