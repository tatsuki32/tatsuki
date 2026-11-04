"""Daily summary, weekly review and yearly round-up, picked out of the memos by rules.

link(memo) decides how a memo is named: its plain title in briefs/, a [link] in Desk.
Days without memos get nothing, and nothing here compares or scolds.
"""
from collections import Counter
from datetime import date, timedelta

MAX_DAY = 10


def week_of(day):
    y, w, _ = date.fromisoformat(day).isocalendar()
    return "{}-W{:02d}".format(y, w)


def week_range(label):
    y, w = label.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def _top_tags(memos, n):
    c = Counter(t for m in memos for t in m["all_tags"] if t != m["kind"])  # the kinds have their own line
    return [t for t, _ in sorted(c.items(), key=lambda x: (-x[1], x[0]))[:n]]


def _most_linked(memos, n):
    linked = [m for m in memos if m["related"]]
    return sorted(linked, key=lambda m: (-len(m["related"]), m["at"]))[:n]


def _head(title, memos):
    out = ["# " + title, "", "{}件".format(len(memos))]
    c = Counter(m["kind"] for m in memos if m["kind"])
    if c:
        out.append("種類：" + "、".join("{} {}件".format(k, v) for k, v in sorted(c.items(), key=lambda x: (-x[1], x[0]))))
    return out


def _section(title, lines):
    return ["", "# " + title] + lines if lines else []


def _end(lines, memos):
    n = sum(1 for m in memos if m["unread"])
    if n:
        lines += ["", "文字をまだ読めていない写真：{}枚".format(n)]
    return "\n".join(lines) + "\n"


def daily(day, memos, link):
    out = _head("メモの要約 " + day, memos)
    tags = _top_tags(memos, 3)
    if tags:
        out.append("多かったタグ：" + "、".join(tags))
    out += _section("メモ", ["- " + link(m) for m in memos[:MAX_DAY]])
    if len(memos) > MAX_DAY:
        out.append("ほか{}件".format(len(memos) - MAX_DAY))
    return _end(out, memos)


def weekly(label, memos, earlier, link):
    """earlier: the memos before this week, to tell which tags are new."""
    start, end = week_range(label)
    out = _head("週の振り返り {}（{} から {}）".format(label, start, end), memos)
    days = Counter(m["day"] for m in memos)
    out += _section("書いた日", ["- {}：{}件".format(d, days[d]) for d in sorted(days)])
    out += _section("多かったタグ", ["- " + t for t in _top_tags(memos, 5)])
    seen = {t for m in earlier for t in m["all_tags"]}
    fresh = [t for t in _top_tags(memos, 100) if t not in seen] if earlier else []
    out += _section("この週に初めて出たタグ", ["- " + t for t in fresh[:5]])
    out += _section("つながりが多かったメモ", ["- " + link(m) for m in _most_linked(memos, 3)])
    ideas = [m for m in memos if m["kind"] == "アイデア"]
    out += _section("アイデア", ["- " + link(m) for m in ideas[:5]])
    return _end(out, memos)


def yearly(year, memos, link):
    out = _head("年のまとめ " + year, memos)
    months = {}
    for m in memos:
        months.setdefault(m["day"][:7], []).append(m)
    lines = []
    for k in sorted(months):
        tags = _top_tags(months[k], 2)
        lines.append("- {}：{}件{}".format(k, len(months[k]), "（" + "、".join(tags) + "）" if tags else ""))
    out += _section("月ごと", lines)
    out += _section("多かったタグ", ["- " + t for t in _top_tags(memos, 10)])
    out += _section("つながりが多かったメモ", ["- " + link(m) for m in _most_linked(memos, 5)])
    return _end(out, memos)
