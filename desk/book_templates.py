"""Meeting templates for guests of tany.im/book, as Google Docs (Created by Tany LLC).

Built from the owner's rules ("会議の考え方 — しない会議・やる会議の決め方", Drive), the four questions of Should we meet?
(desk/meetcheck.py) and the review items of Minutes (desk/minutes_insight.py). One Google Doc per template and language (en, ja, ru, vi), each language in its own Drive folder.

    python3 desk/book_templates.py   -> desk/data/book/templates/<lang>/<id>.html (uploaded to Drive as Google Docs by Claude)
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

# (id, title, body) in Russian and Vietnamese, the same fields as above.
RU = [
    ("decision", "Встреча для решения: материалы заранее", """
> Отправьте вместе с приглашением. Если заполнить не получается, встречаться ещё рано. Первые 3 минуты встречи — чтение этого документа вместе.
# Что решаем (одно предложение)
Делаем ли ___ ? / A или B: ___
# Кто решает (один человек)
___
# Предлагаемый ответ
___
# Почему (2–4 причины)
- ___
- ___
# Другие варианты и почему не они
| Вариант | Плюсы | Минусы |
| ___ | ___ | ___ |
# Кто участвует
Обязательно (только те, кто нужен для решения): ___
По желанию (получат протокол): ___
# Время и стоимость
Длительность: 15 / 25 / 50 минут (не больше 60)
Стоимость: ставка в час × люди × часы = ___  Стоит ли ответ этого? ___
# Ход встречи
- 0–3 мин: читаем документ вместе
- Зачитать решение: «Сегодня решаем ___. Решает ___.»
- Обсуждаем только разногласия и решаем
- Последние 2 мин: зачитать решения и задачи
# Протокол (в конце)
Решено: ___
| Задача | Ответственный (один) | Срок | Готово, когда |
| ___ | ___ | ___ | ___ |
Не решено — кто и к какому сроку решит: ___
"""),
    ("advice", "Нужен совет: заметка до встречи", """
> Отправьте до встречи. Контекст прочитаю заранее, и время уйдёт на ваши вопросы.
# Контекст (3 строки)
___
# В чём сложность
___
# Что уже пробовали
- ___
# Вопросы (в форме вопросов)
- Стоит ли мне ___ ?
- Что бы вы сделали с ___ ?
# Какой ответ поможет
После встречи я смогу ___ .
# Материалы (ссылки)
___
"""),
    ("first", "Первая встреча: знакомство", """
> Первая встреча — чтобы узнать друг друга, а не продавать. Каждый рассказывает о себе примерно 2 минуты.
# Пара слов о себе
___
# О чём хочу поговорить
- ___
# Как я о вас узнал(а)
___
# Что хочу спросить у вас
- ___
# Один возможный следующий шаг
___
# После встречи
[] Сообщение с благодарностью в течение 24 часов
[] Обещанные материалы в течение недели
[] Если подходит — одна конкретная идея о сотрудничестве в течение месяца
"""),
    ("proposal", "Предложение письмом (вместо встречи)", """
> Поделиться материалами, рассказать о бизнесе или предложить сотрудничество обычно быстрее письмом. Используйте этот шаблон или форму «Написать вместо встречи» на tany.im/book.
# Главное / предложение (одно предложение)
___
# Почему (2–4 причины)
- ___
- ___
# Что это даст вам
___
# Чего я жду от вас
Ответ / решение (да или нет) / мнение / знакомство с человеком / короткую встречу позже
# К какому сроку
___
# Материалы (ссылки)
___
# Если встреча нужна
Одно, что встреча должна решить: ___
"""),
    ("minutes", "Протокол встречи", """
> Отправляется сразу после встречи. Задача — это задача, только если есть ответственный, суть и срок. Без любого из трёх это ещё предложение.
# Итог (одна строка)
___
# Решено
- ___ (решил(а) ___, потому что ___)
# Задачи
| Задача | Ответственный (один) | Срок | Готово, когда |
| ___ | ___ | ___ | ___ |
# Не решено
- ___: ___ решит до ___
# Встреча
Дата: ___  Длительность: ___  Участники: ___
# Ход разговора (под протоколом, не в нём)
___
"""),
    ("review", "Разбор встречи: оценочный лист", """
> Заполните в течение дня после встречи. Оцените каждую строку от 1 (совсем нет) до 5 (полностью).
# До встречи
| Пункт | Оценка 1–5 | Заметка |
| Проверили, нельзя ли решить письменно | ___ | ___ |
| Решение было одним предложением («делаем ли…», а не «о…») | ___ | ___ |
| Решал один человек | ___ | ___ |
| Материалы ушли вместе с приглашением | ___ | ___ |
| Были только те, кто нужен | ___ | ___ |
| Стоимость (ставка × люди × время) оправдалась | ___ | ___ |
# Во время встречи
| Пункт | Оценка 1–5 | Заметка |
| Начали вовремя и зачитали решение | ___ | ___ |
| О записи договорились в начале | ___ | ___ |
| Закончили, когда решили, даже если время осталось | ___ | ___ |
| Говорили все; никто не говорил слишком долго | ___ | ___ |
# После встречи
| Пункт | Оценка 1–5 | Заметка |
| У каждой задачи есть ответственный, суть и срок | ___ | ___ |
| Протокол ушёл в течение дня | ___ | ___ |
| Задачи перенесены в список задач | ___ | ___ |
# Как я говорил(а)
Моя доля разговора: ___ %   Самый длинный монолог: ___ мин   Мои вопросы: ___   Слова-паразиты: ___
# Три строки
Можно было короче за счёт: ___
Получилось хорошо: ___
Попробовать в следующий раз: ___
"""),
]
VI = [
    ("decision", "Buổi họp cần quyết định: tài liệu đọc trước", """
> Gửi kèm lời mời. Nếu chưa điền được, có nghĩa là còn quá sớm để họp. 3 phút đầu buổi họp dùng để cùng đọc tài liệu này.
# Điều cần quyết định (một câu)
Có nên ___ không? / A hay B: ___
# Người quyết định (một người)
___
# Đề xuất
___
# Lý do (2–4 ý)
- ___
- ___
# Phương án khác và lý do không chọn
| Phương án | Điểm tốt | Điểm chưa tốt |
| ___ | ___ | ___ |
# Ai cần tham dự
Bắt buộc (chỉ người cần cho quyết định): ___
Không bắt buộc (sẽ nhận biên bản): ___
# Thời gian và chi phí
Thời lượng: 15 / 25 / 50 phút (tối đa 60)
Chi phí: lương theo giờ × số người × số giờ = ___  Câu trả lời có đáng không? ___
# Diễn biến
- 0–3 phút: cùng đọc tài liệu
- Đọc điều cần quyết định: "Hôm nay quyết định ___. Người quyết định là ___."
- Chỉ bàn những điểm khác nhau, rồi quyết định
- 2 phút cuối: đọc lại điều đã quyết định và việc cần làm
# Biên bản (điền cuối buổi)
Đã quyết định: ___
| Việc cần làm | Người phụ trách (một người) | Hạn | Xong khi |
| ___ | ___ | ___ | ___ |
Chưa quyết định — ai quyết định trước ngày nào: ___
"""),
    ("advice", "Xin tư vấn: ghi chú trước buổi gặp", """
> Gửi trước buổi gặp. Bối cảnh sẽ được đọc trước, để thời gian dành cho câu hỏi của bạn.
# Bối cảnh (3 dòng)
___
# Điều đang vướng
___
# Đã thử những gì
- ___
# Câu hỏi (viết dạng câu hỏi)
- Tôi có nên ___ không?
- Nếu là bạn, bạn sẽ làm gì với ___ ?
# Câu trả lời tốt trông như thế nào
Sau buổi gặp, tôi sẽ có thể ___ .
# Tài liệu (link)
___
"""),
    ("first", "Lần đầu gặp: làm quen", """
> Lần gặp đầu để hiểu nhau, không phải để bán hàng. Mỗi bên tự giới thiệu khoảng 2 phút.
# Một dòng về tôi
___
# Điều tôi muốn nói
- ___
# Tôi biết đến bạn qua
___
# Điều tôi muốn hỏi bạn
- ___
# Một bước tiếp theo có thể
___
# Sau buổi gặp
[] Lời cảm ơn trong vòng 24 giờ
[] Tài liệu đã hứa trong vòng một tuần
[] Nếu phù hợp, một ý tưởng hợp tác cụ thể trong vòng một tháng
"""),
    ("proposal", "Đề xuất bằng văn bản (thay vì họp)", """
> Chia sẻ tài liệu, giới thiệu doanh nghiệp hay đề xuất hợp tác thường nhanh hơn khi viết. Dùng mẫu này, hoặc mục "Viết thay vì gặp" trên tany.im/book.
# Ý chính / đề xuất (một câu)
___
# Lý do (2–4 ý)
- ___
- ___
# Lợi ích cho bạn
___
# Điều tôi mong bạn làm
Trả lời / quyết định (có hoặc không) / góp ý / giới thiệu người / một buổi gặp ngắn sau
# Trước ngày nào
___
# Tài liệu (link)
___
# Nếu cần họp
Một điều buổi họp cần quyết định: ___
"""),
    ("minutes", "Biên bản buổi họp", """
> Gửi ngay sau buổi họp. Một việc chỉ là việc cần làm khi có người phụ trách, nội dung và hạn. Thiếu một trong ba thì vẫn là đề xuất.
# Kết quả (một dòng)
___
# Đã quyết định
- ___ (người quyết định ___, lý do ___)
# Việc cần làm
| Việc cần làm | Người phụ trách (một người) | Hạn | Xong khi |
| ___ | ___ | ___ | ___ |
# Chưa quyết định
- ___: ___ quyết định trước ___
# Thông tin buổi họp
Ngày: ___  Thời lượng: ___  Người tham dự: ___
# Diễn biến cuộc trao đổi (đặt dưới biên bản)
___
"""),
    ("review", "Nhìn lại buổi họp: phiếu đánh giá", """
> Điền trong vòng một ngày sau buổi họp. Chấm mỗi dòng từ 1 (chưa làm được) đến 5 (làm tốt).
# Trước buổi họp
| Mục | Điểm 1–5 | Ghi chú |
| Đã xem liệu viết có giải quyết được không | ___ | ___ |
| Điều cần quyết định là một câu ("có nên…", không phải "về…") | ___ | ___ |
| Một người quyết định | ___ | ___ |
| Tài liệu được gửi kèm lời mời | ___ | ___ |
| Chỉ người cần thiết tham dự | ___ | ___ |
| Chi phí (lương × người × thời gian) là xứng đáng | ___ | ___ |
# Trong buổi họp
| Mục | Điểm 1–5 | Ghi chú |
| Bắt đầu đúng giờ và đọc điều cần quyết định | ___ | ___ |
| Đã xin đồng ý ghi âm từ đầu | ___ | ___ |
| Kết thúc khi đã quyết định, kể cả còn thời gian | ___ | ___ |
| Ai cũng được nói; không ai nói quá lâu | ___ | ___ |
# Sau buổi họp
| Mục | Điểm 1–5 | Ghi chú |
| Mỗi việc có người phụ trách, nội dung và hạn | ___ | ___ |
| Biên bản được gửi trong một ngày | ___ | ___ |
| Việc cần làm đã chuyển vào danh sách công việc | ___ | ___ |
# Cách tôi nói
Phần tôi nói: ___ %   Lần nói dài nhất: ___ phút   Số câu tôi hỏi: ___   Từ đệm: ___
# Ba dòng
Có thể ngắn hơn nhờ: ___
Điều làm tốt: ___
Lần sau thử: ___
"""),
]
HOWTO = {"en": "To use it: File → Make a copy", "ja": "使い方：ファイル → コピーを作成", "ru": "Как использовать: Файл → Создать копию", "vi": "Cách dùng: Tệp → Tạo bản sao"}
BASED = {"en": "Based on public write-ups of how Amazon, GitLab, Keyence, Apple and others run meetings.",
         "ja": "Amazon・GitLab・キーエンス・Apple などの会議の進め方を紹介した公開の記事をもとにしています。",
         "ru": "По открытым материалам о том, как проводят встречи Amazon, GitLab, Keyence, Apple и другие.",
         "vi": "Dựa trên các bài viết công khai về cách Amazon, GitLab, Keyence, Apple và các công ty khác tổ chức họp."}
FOLDER = {"en": "English", "ja": "日本語", "ru": "Русский", "vi": "Tiếng Việt"}


def by_lang():
    """{lang: [(id, title, body)]}"""
    return {"en": [(i, et, eb) for i, et, jt, eb, jb in T], "ja": [(i, jt, jb) for i, et, jt, eb, jb in T], "ru": RU, "vi": VI}


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


def page(lang, title, body):
    return (f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            f"<p class='by'>Created by Tany LLC · tany.im/book · {html.escape(HOWTO[lang])}</p>"
            f"<h1>{html.escape(title)}</h1>{body_html(body)}"
            f"<hr><p class='by'>Created by Tany LLC — {html.escape(BASED[lang])}</p></body></html>")


def build():
    out = []
    for lang, items in by_lang().items():
        d = os.path.join(OUT, lang)
        os.makedirs(d, exist_ok=True)
        for tid, title, body in items:
            p = os.path.join(d, tid + ".html")
            with open(p, "w", encoding="utf-8") as f:
                f.write(page(lang, title, body))
            out.append((lang, tid, title + " · Tany", p))
    return out


if __name__ == "__main__":
    for lang, tid, title, p in build():
        print(lang, tid, title, p)
