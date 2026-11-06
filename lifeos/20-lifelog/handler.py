"""Typed records: 「体重65.2」「昼 カレー大盛り」「ベンチ60kg 10回3セット」 go the same way as a photo (desk/lifelog.py).

match() only looks at the words, so an idea or a question never costs an API call. A message that Claude finds records
nothing is passed on to the other tools, so it is not lost.
"""
import os
import re
import sys

PRIORITY = 25  # before 07-habits: 「筋トレ30分」 has numbers worth keeping. 「筋トレした」 has none and stays a habit

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SLUG = "20-lifelog"
_PREFIX = re.compile(r"^\s*(記録|ログ|食事|睡眠|体重|歩数|筋トレ|運動|学習|支出|血糖値?)\s*[:：]")
_FOOD = re.compile(r"朝食|昼食|夕食|夜食|間食|朝ご?飯|昼ご?飯|夜ご?飯|晩ご?飯|朝ごはん|昼ごはん|夜ごはん|晩ごはん|ランチ|食べた|飲んだ|\d\s*kcal", re.I)
_MEASURED = re.compile(r"寝た|睡眠|昼寝|就寝|起床|起きた|体重|体脂肪|BMI|歩数|歩いた|筋トレ|ベンチ|スクワット|デッドリフト|懸垂|腕立て|腹筋|ランニング|ジョギング|ジム|プレス|カール|ロウ|レイズ|レッグ|ラット|血糖|スクリーンタイム", re.I)
_NUMBER = re.compile(r"\d")
_SETS = re.compile(r"\d\s*[x×]\s*\d")
_NOT_A_RECORD = re.compile(r"[?？]\s*$|したい|しよう|する予定|アイデア|どう(すれば|したら)|タスク")


def _is_plan(text):
    """「明日12時にランチ」 is a plan for the calendar, not a meal. Tool 04 decides what counts."""
    from lifeos.lib import tools
    try:
        return tools.load_file(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "04-voice"), "sort").looks_like_plan(text)
    except (OSError, ImportError, AttributeError):
        return False


def match(text):
    t = (text or "").strip()
    if not t or t.startswith("/") or len(t) > 500:
        return False
    if _PREFIX.match(t):
        return True
    if _NOT_A_RECORD.search(t) or _is_plan(t):
        return False
    return bool(_FOOD.search(t) or _SETS.search(t) or (_MEASURED.search(t) and _NUMBER.search(t)))


def _core():
    desk = os.path.join(_REPO, "desk")
    if desk not in sys.path:
        sys.path.insert(0, desk)
    import lifelog
    return lifelog


def handle(text, ctx):
    from lifeos.bot import router
    core = _core()
    entry = core.add_text(_PREFIX.sub("", text, count=1).strip() or text, ctx.source)
    report = core.process()
    mine = [r for r in report["read"] if r["id"].startswith(entry["id"][:16])]
    state = core.read_json(core.lp("queue.json"), {}).get(entry["id"], {}).get("status")
    if state == "empty" and not _PREFIX.match(text):
        # not a record after all: the other tools decide, and the words are kept the way they always were
        others = [r for r in router.discover().routes if r.slug != _SLUG]
        return router.dispatch(text, ctx, others)
    return core.reply_text({**report, "read": mine}) if mine or report["stopped"] or report["failed"] else core.reply_text(report)
