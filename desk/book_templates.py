"""Meeting templates for guests of tany.im/book, as Google Docs (Created by Tany LLC).

Built from the owner's rules ("会議の考え方 — しない会議・やる会議の決め方", Drive), the four questions of Should we meet?
(desk/meetcheck.py) and the review items of Minutes (desk/minutes_insight.py). Each template is English first, then Japanese.

    python3 desk/book_templates.py   -> desk/data/book/templates/<id>.html (uploaded to Drive as Google Docs by hand or by Claude)
"""
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.environ.get("DESK_BOOK_DIR") or os.path.join(HERE, "data", "book"), "templates")

# (id, English title, Japanese title, English body, Japanese body). Body lines: "# heading", "- item", "[] check",
# "| a | b |" table rows (first row is the header), "> note", "" blank, anything else a paragraph. "___" marks a blank to fill.
T = [
    ("decision", "Decision meeting: pre-read", "決めたい会議：事前資料", """
> Send this with the invitation. If it cannot be filled in, it is too early to meet. The first 3 minutes of the meeting are for reading it together.
# The decision (one sentence)
Shall we ___ ? / A or B: ___
# Who decides (one person)
___
# Proposed answer
___
# Why (2 to 4 reasons)
- ___
- ___
# Other options and why not
| Option | Good | Not so good |
| ___ | ___ | ___ |
# Who needs to be there
Required (only people needed to decide): ___
Optional (they get the record): ___
# Time and cost
Length: 15 / 25 / 50 minutes (60 at most)
Cost: hourly rate × people × hours = ___  Is the answer worth it? ___
# How it runs
- 0–3 min: read this together
- Read the decision aloud: "Today we decide ___. ___ decides."
- Points of difference only, then decide
- Last 2 min: read back what was decided and the actions
# Record (filled in at the end)
Decided: ___
| Action | Owner (one person) | Due | Done when |
| ___ | ___ | ___ | ___ |
Not decided yet, who decides by when: ___
""", """
> 招待と一緒に送ってください。埋められないときは、会議にはまだ早いという合図です。会議の最初の3分は、これを一緒に読む時間にします。
# 決めること（1文）
〜するか：___ ／ A か B か：___
# 決める人（1人）
___
# 結論の案
___
# 理由（2〜4個）
- ___
- ___
# ほかの案と、選ばない理由
| 案 | よい点 | 気になる点 |
| ___ | ___ | ___ |
# 参加する人
必須（決めるのに要る人だけ）：___
任意（記録をお送りします）：___
# 時間と費用
長さ：15／25／50分（上限60分）
費用：時給 × 人数 × 時間 ＝ ___　その答えに見合うか：___
# 進め方
- 0〜3分：この資料を一緒に読む
- 決めることを読み上げる：「今日は〜を決めます。決めるのは〜です」
- 意見の違う点だけ話し、決める
- 最後の2分：決まったことと、やることを読み上げる
# 記録（最後に埋める）
決まったこと：___
| やること | 責任者（1人） | 期限 | 完了の印 |
| ___ | ___ | ___ | ___ |
決まらなかったこと（誰がいつまでに決めるか）：___
"""),
    ("advice", "Advice: pre-meeting note", "相談したい：事前メモ", """
> Send this before the meeting. The background is read beforehand, so the time goes to your questions.
# Background (3 lines)
___
# What I am stuck on
___
# What I already tried
- ___
# Questions (written as questions)
- Should I ___ ?
- What would you do about ___ ?
# What a good answer looks like
After the meeting I will be able to ___ .
# Materials (links)
___
""", """
> 会議の前にお送りください。背景は先に読んでおくので、当日は質問に時間を使えます。
# 背景（3行）
___
# 困っていること
___
# もう試したこと
- ___
# 聞きたいこと（問いの形で）
- 〜した方がよいでしょうか？
- 〜について、あなたならどうしますか？
# よい答えのイメージ
会議のあと、___ ができるようになっている。
# 資料（リンク）
___
"""),
    ("first", "First meeting: introduction", "はじめまして：顔合わせ", """
> A first meeting is for knowing each other, not for selling. Each side introduces themselves in about 2 minutes.
# About me (one line)
___
# What I would like to talk about
- ___
# How I found you
___
# What I would like to ask you
- ___
# One possible next step
___
# After the meeting
[] Thank-you message within 24 hours
[] Promised materials within a week
[] One concrete idea to work together within a month, if it fits
""", """
> 初対面は、お互いを知るための時間です。売り込みの場にはしません。それぞれ2分ほどで自己紹介します。
# 一言自己紹介
___
# 話してみたいこと
- ___
# 知ったきっかけ
___
# 相手に聞いてみたいこと
- ___
# 次の一歩の候補（1つ）
___
# 会ったあと
[] 24時間以内にお礼
[] 約束した資料は1週間以内に
[] 合いそうなら、1か月以内に具体的な協力の提案を1つ
"""),
    ("proposal", "Proposal in writing (instead of a meeting)", "会議の代わりの提案（文章）", """
> Sharing materials, explaining a business or proposing a collaboration usually goes faster in writing. Use this, or the "Write instead of meeting" form on tany.im/book.
# Point / proposal (one sentence)
___
# Why (2 to 4 reasons)
- ___
- ___
# What it means for you
___
# What I would like from you
A reply / a decision (yes or no) / feedback / an introduction / a short meeting later
# By when
___
# Materials (links)
___
# If a meeting is needed
The one thing a meeting would decide: ___
""", """
> 資料の共有、事業の説明、コラボのご提案は、文章の方がたいてい早く進みます。この型か、tany.im/book の「会議の代わりに書いて送る」をお使いください。
# 結論・ご提案（1文）
___
# 理由（2〜4個）
- ___
- ___
# 相手にとってのよい点
___
# してほしいこと
返信 ／ 決定（はい・いいえ） ／ 意見 ／ 人の紹介 ／ 必要ならあとで短い会議
# いつまでに
___
# 資料（リンク）
___
# 会議が必要な場合
会議で決めたいこと（1つ）：___
"""),
    ("minutes", "Meeting record (minutes)", "会議の記録（議事録）", """
> Send it right after the meeting. An action is only an action when it has an owner, a task and a due date. Without one of them it is still a proposal.
# Result (one line)
___
# Decided
- ___ (decided by ___, because ___)
# Actions
| Action | Owner (one person) | Due | Done when |
| ___ | ___ | ___ | ___ |
# Not decided yet
- ___: ___ decides by ___
# Meeting
Date: ___  Length: ___  People: ___
# Notes of the conversation (below the record, not in it)
___
""", """
> 会議が終わったらすぐ送ります。やることは「誰が・何を・いつまでに」がそろって初めてやることです。どれかが欠けたものは、まだ提案です。
# 結論（1行）
___
# 決まったこと
- ___（決めた人：___、理由：___）
# やること
| やること | 責任者（1人） | 期限 | 完了の印 |
| ___ | ___ | ___ | ___ |
# 決まらなかったこと
- ___：___ が ___ までに決める
# 会議の情報
日時：___　長さ：___　参加者：___
# 話の流れ（記録の下に付ける）
___
"""),
    ("review", "Meeting review: scorecard", "会議の振り返り：評価シート", """
> Fill it in within a day of the meeting. Score each line from 1 (not at all) to 5 (fully).
# Before
| Item | Score 1–5 | Note |
| We checked that writing could not do it | ___ | ___ |
| The decision was one sentence ("shall we…", not "about…") | ___ | ___ |
| One person decided | ___ | ___ |
| The pre-read went out with the invitation | ___ | ___ |
| Only the people needed were there | ___ | ___ |
| The cost (rate × people × time) was worth it | ___ | ___ |
# During
| Item | Score 1–5 | Note |
| It started on time and read the decision aloud | ___ | ___ |
| Recording was agreed at the start | ___ | ___ |
| It ended when decided, even with time left | ___ | ___ |
| Everyone spoke; no one held the floor too long | ___ | ___ |
# After
| Item | Score 1–5 | Note |
| Every action has an owner, a task and a due date | ___ | ___ |
| The record went out within a day | ___ | ___ |
| Actions moved to the task list | ___ | ___ |
# How I spoke
My share of the talking: ___ %   Longest stretch: ___ min   Questions I asked: ___   Filler words: ___
# Three lines
Could be shorter by: ___
Went well: ___
Try next time: ___
""", """
> 会議から1日以内に書きます。各行を 1（できていない）〜 5（できた）で付けます。
# 会議の前
| 項目 | 点 1〜5 | メモ |
| 文章で済まないか考えた | ___ | ___ |
| 決めることが1文だった（「〜について」ではなく「〜するか」） | ___ | ___ |
| 決める人が1人だった | ___ | ___ |
| 事前資料を招待と一緒に送った | ___ | ___ |
| 決めるのに要る人だけが出た | ___ | ___ |
| 費用（時給×人数×時間）に見合った | ___ | ___ |
# 会議の間
| 項目 | 点 1〜5 | メモ |
| 時間どおり始め、決めることを読み上げた | ___ | ___ |
| 記録を取ることに最初に同意を得た | ___ | ___ |
| 決まったら、時間が余っても終えた | ___ | ___ |
| 全員が話し、1人が長く話し続けなかった | ___ | ___ |
# 会議のあと
| 項目 | 点 1〜5 | メモ |
| やることに責任者・中身・期限がそろっている | ___ | ___ |
| 記録を1日以内に送った | ___ | ___ |
| やることをタスクに移した | ___ | ___ |
# 自分の話し方
話した割合：___ %　いちばん長く続けて話した時間：___ 分　質問の数：___　口癖の回数：___
# 3行の振り返り
もっと短くできたところ：___
よかったこと：___
次に試すこと：___
"""),
]

CSS = ("body{font-family:Arial,'Hiragino Sans',sans-serif;font-size:11pt;color:#1B1B18}h1{font-size:20pt}h2{font-size:13pt;color:#2C7A4B;margin-top:16pt}"
       "table{border-collapse:collapse;width:100%}td,th{border:1px solid #c9c7bd;padding:4pt 6pt;vertical-align:top}th{background:#efefeb;text-align:left}"
       ".note{background:#f6f5f0;padding:6pt 10pt}.by{color:#6d6d67;font-size:9pt}")


def body_html(text):
    out, rows = [], []

    def flush():
        if rows:
            head, rest = rows[0], rows[1:]
            out.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in head) + "</tr>" +
                       "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rest) + "</table>")
            rows.clear()
    for line in text.strip().splitlines():
        line = line.rstrip()
        if line.startswith("|"):
            rows.append([html.escape(c.strip()) for c in line.strip("|").split("|")])
            continue
        flush()
        e = html.escape
        if not line:
            continue
        if line.startswith("# "):
            out.append(f"<h2>{e(line[2:])}</h2>")
        elif line.startswith("- "):
            out.append(f"<ul><li>{e(line[2:])}</li></ul>")
        elif line.startswith("[] "):
            out.append(f"<p>☐ {e(line[3:])}</p>")
        elif line.startswith("> "):
            out.append(f'<p class="note"><i>{e(line[2:])}</i></p>')
        else:
            out.append(f"<p>{e(line)}</p>")
    flush()
    return "".join(out)


def page(tid, en_t, ja_t, en, ja):
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            f"<p class='by'>Created by Tany LLC · tany.im/book · To use it: File → Make a copy（ファイル → コピーを作成）</p>"
            f"<h1>{html.escape(en_t)}</h1>{body_html(en)}<hr>"
            f"<h1>{html.escape(ja_t)}</h1>{body_html(ja)}"
            f"<hr><p class='by'>Created by Tany LLC — based on public write-ups of how Amazon, GitLab, Keyence, Apple and others run meetings.</p></body></html>")


def build():
    os.makedirs(OUT, exist_ok=True)
    paths = []
    for tid, en_t, ja_t, en, ja in T:
        p = os.path.join(OUT, tid + ".html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(page(tid, en_t, ja_t, en, ja))
        paths.append((tid, f"{en_t} / {ja_t} · Tany", p))
    return paths


if __name__ == "__main__":
    for tid, title, p in build():
        print(tid, title, p)
