"""/lowenergy: a draft Claude wrote, and one reaction from Tatsuki. OK, fix, or stop.

Nothing happens until the reaction is OK, and OK counts only on a draft waiting in `pending`,
so it can act once and never on a text Tatsuki has not seen. Anything unclear is a fix, never
an approval. Mail goes no further than a Gmail draft: this file has no way to send.
"""
import json
import os
import re
import tempfile
import unicodedata

from lifeos.lib import md, paths, tasks

KINDS = ("note", "task", "mail")
OK_WORDS = {"ok", "okay", "おk", "オッケー", "おっけー", "はい", "うん", "いいよ", "それで", "これで"}
STOP_WORDS = {"やめる", "やめ", "やめます", "今日はやめる", "休む", "パス"}
FIX_HEADS = ("直す", "なおす", "直して", "なおして", "修正")
_ID = re.compile(r"^d[0-9a-f]+$")
_EDGE = " \t\r\n!?.,。、！？〜~"


class DraftError(Exception):
    """The draft cannot take this step. Nothing changed."""


def _path(draft_id):
    if not _ID.match(str(draft_id)):
        raise DraftError("下書きの番号が違います")
    return paths.data_path("tools", "16-where", "drafts", draft_id + ".json")


def _save(d):
    p = _path(d["id"])
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".draft", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return d


def get(draft_id):
    try:
        with open(_path(draft_id), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        raise DraftError("その下書きはありません") from None


def create(kind, body, project="", title="", to=None, subject="", reply_to=""):
    if kind not in KINDS:
        raise DraftError("kind は " + " / ".join(KINDS) + " のどれか")
    if not str(body or "").strip():
        raise DraftError("下書きが空です")
    if kind == "task" and not str(title or "").strip():
        raise DraftError("タスクには title が要ります")
    if kind == "mail" and not (to or reply_to):
        raise DraftError("メールには宛先（to か reply_to）が要ります")
    return _save({"id": "d" + format(paths.now_ms(), "x") + os.urandom(2).hex(), "status": "pending", "rev": 1, "kind": kind,
                  "project": project, "title": title, "body": body, "to": to or [], "subject": subject, "reply_to": reply_to})


def redraft(draft_id, body):
    """A new text after 直す. It goes back to waiting, so the next OK is about this text."""
    d = get(draft_id)
    if d["status"] not in ("pending", "revising"):
        raise DraftError("この下書きはもう閉じています")
    if not str(body or "").strip():
        raise DraftError("下書きが空です")
    return _save({**d, "body": body, "status": "pending", "rev": d["rev"] + 1, "feedback": ""})


def parse(text):
    """("ok" | "stop" | "fix", feedback). Only an exact word approves: 「OKじゃない」 is a fix."""
    raw = " ".join(unicodedata.normalize("NFKC", str(text or "")).split())
    word = raw.strip(_EDGE).casefold()
    if not word:
        raise DraftError("反応が空です")
    if word in OK_WORDS:
        return "ok", ""
    if word in STOP_WORDS:
        return "stop", ""
    for head in FIX_HEADS:
        if word.startswith(head):
            return "fix", raw.strip(_EDGE)[len(head):].strip(_EDGE)
    return "fix", raw


def _apply(d):
    if d["kind"] == "task":
        title = " ".join(d["title"].split())
        same = next((t for t in tasks.open_tasks() if t.get("title") == title), None)
        if same:
            return {"do": "none", "task_id": same["id"], "added": False}
        note = ("案件:%s\n" % d["project"] if d["project"] else "") + d["body"]
        return {"do": "none", "task_id": tasks.add(title, note=note, source="lifeos-16")["id"], "added": True}
    saved = md.save("tools/16-where/approved/%s.md" % d["id"], d["body"])
    if d["kind"] == "note":
        return {"do": "none", "saved": saved}
    return {"do": "gmail_create_draft", "saved": saved, "to": d["to"], "subject": d["subject"], "body": d["body"],
            "replyToMessageId": d["reply_to"]}


def react(draft_id, text):
    """Applies Tatsuki's reaction and says what comes next. The step happens before the status
    changes, so a failed step leaves the draft waiting."""
    d = get(draft_id)
    action, feedback = parse(text)
    if action == "stop":
        if d["status"] not in ("pending", "revising"):
            raise DraftError("この下書きはもう閉じています")
        _save({**d, "status": "stopped"})
        return {"result": "stop", "do": "none"}
    if d["status"] != "pending":
        raise DraftError("直した文面をまだ見てもらっていません" if d["status"] == "revising" else "この下書きはもう閉じています")
    if action == "fix":
        _save({**d, "status": "revising", "feedback": feedback})
        return {"result": "fix", "do": "redraft", "feedback": feedback}
    out = _apply(d)
    _save({**d, "status": "approved"})
    return {"result": "ok", **out}


def drafted(draft_id):
    """Records that the approved mail is now in Gmail drafts."""
    d = get(draft_id)
    if d["kind"] != "mail" or d["status"] != "approved":
        raise DraftError("承認済みのメールではありません")
    return _save({**d, "status": "gmail_drafted"})
