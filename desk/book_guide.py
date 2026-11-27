"""The meeting guide guests get with a booking on tany.im/book ("How to meet with Tatsuki", Created by Tany LLC).

One PDF per language, built from the text below with the Mac's HTML-to-PDF tool (wise_report.to_pdf).
The rules come from the owner's own doc "会議の考え方 — しない会議・やる会議の決め方" (Drive, 2026-11-21), cut down
to what a guest needs: when to write instead of meeting, what to send first, how each kind of meeting runs, what to cut.

    python3 desk/book_guide.py [ja ...]     -> desk/data/book/guide/<lang>.pdf (and .html beside it, to look at)
"""
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.environ.get("DESK_BOOK_DIR") or os.path.join(HERE, "data", "book"), "guide")
BOOK_URL = "https://tany.im/book"

# The text. Each kind of meeting matches a purpose on tany.im/book (id, minutes).
T = {
    "ja": {
        "lang": "ja",
        "title": "Tatsuki とのミーティングの進め方",
        "sub": "短く、決めて終わるための3分ガイド",
        "lead": [
            "ご予約ありがとうございます。限られた時間を、話すことと決めることに使うためのガイドです。",
            "考え方は3つです。<b>文章で済むことは文章で。</b><b>資料は会う前に。</b><b>会うなら1つ決めて、時間が余っても終える。</b>",
        ],
        "h_when": "会議にするか、文章にするか",
        "meet_h": "会議が向いていること",
        "meet": ["はじめましての顔合わせ・アイスブレイク", "気軽な雑談", "その場のやりとりが要る相談", "意見が分かれていて、決めたいこと"],
        "text_h": "先に文章が向いていること",
        "text": ["資料の共有", "事業やサービスの説明", "コラボや提案の最初のご連絡", "日程・条件・金額の確認", "進み具合の報告"],
        "text_note": "文章が向いている内容は、tany.im/book の「会議の代わりに書いて送る」からお送りください。読んで、必要なら会議をご提案します。多くの場合、会議より早く進みます。",
        "h_kinds": "目的ごとの進め方",
        "cols": ["目的と長さ", "先に送っていただくもの", "当日の流れ", "ここを削ると短くなる"],
        "kinds": [
            ("ひとつ聞きたい", 5, "聞きたいことを1〜2行。関係する資料のリンク", "質問 → 答え → 次の一歩", "前置きと自己紹介。必要なら文章で"),
            ("ざっくばらんに", 10, "話したい話題があれば（なくても大丈夫です）", "近況 → 話題を1つ", "議題を詰め込むこと。時間で終えます"),
            ("はじめまして", 14, "一言の自己紹介・話してみたいこと・知ったきっかけ", "互いに2分で紹介 → 共通点 → 次にやることを1つ", "会社や事業の詳しい説明。資料で先にいただければ読んでおきます"),
            ("相談したい", 19, "背景・困っていること・聞きたいこと（問いの形で）", "問いの確認 → 意見 → 次に試すこと", "背景を口で説明する時間。読んでから伺います"),
            ("決めたいことがある", 24, "決めること1文（「〜するか」「AかBか」）・選択肢・おすすめの案と理由・資料", "決めることの読み上げ → 論点 → 決定 → 誰が・何を・いつまでに", "「〜について」の議題、スライドの読み上げ"),
        ],
        "min": "分",
        "h_memo": "事前のメモの書き方",
        "memo": [
            ("決めることは「〜するか」の形で", "「新サービスについて」ではなく「新サービスを3月に始めるか決めたい」。「〜について」で終わる議題は、決めることがまだ見えていない合図です。"),
            ("結論の案を先に、理由はあとに", "「A案がよいと考えています。理由は2つで…」。答えを1つ、それを支える理由を2〜4個。箇条書きやスライドだけより、短い文章の方が伝わります。"),
            ("資料はリンクで、会う前に", "当日に画面で説明するより、先に読んでおく方が短く済みます。読む時間がない場合は、最初の数分を一緒に読む時間にします。"),
        ],
        "h_day": "当日のこと",
        "day": [
            "時間どおりに始め、決まったら予定より早くても終えます。",
            "最初に「今日決めること」を1文で確かめます。",
            "記録を取るときは、最初にお伝えして同意をいただきます。",
            "最後の2分で、決まったことと「やること」（誰が・何を・いつまでに）を読み上げます。3つのどれかが欠けたものは、まだ提案として扱います。",
        ],
        "h_after": "終わったあと",
        "after": [
            "決まったことと、やることのメモをお送りします。",
            "約束した資料は、なるべく1週間以内にお送りします。",
            "続きが文章で決められるときは、次の会議を入れずに文章で進めます。",
        ],
        "h_check": "会議を短くするチェックリスト",
        "check": ["文章で済まないか考えた", "決めることを1文で書いた", "資料のリンクを先に送った", "参加者は決めるのに要る人だけにした", "決まったら、時間が余っても終える"],
        "src": "参考：Amazon（会議の前に文書を読む）、GitLab（まず会議なしでできないか考える）、キーエンス（決めることを先に決め、持ち越さない）、Apple（やることには責任者1人）など、公開されている記事や本の紹介。各社の正式な規則とは限りません。",
        "book": "予約・会議の代わりに書いて送る：",
    },
}


def esc(s):
    return html.escape(str(s), quote=True)


LOGO = '<svg class="logo" role="img" aria-label="Tany" viewBox="0.0 -735.0 4042.0 735.0"><path fill="currentColor" d="M297 0V-529H57V-700H742V-529H495V0Z M754.0 0 1061.0 -700H1228.0L1533.0 0H1334.0L1184.0 -349Q1174.0 -375 1165.5 -396.5Q1157.0 -418 1150.0 -439.0Q1143.0 -460 1136.5 -481.0Q1130.0 -502 1124.0 -525L1160.0 -526Q1154.0 -501 1147.5 -479.5Q1141.0 -458 1133.5 -438.0Q1126.0 -418 1117.0 -396.5Q1108.0 -375 1098.0 -349L948.0 0ZM901.0 -112 962.0 -252H1324.0L1368.0 -112Z M1655.0 0V-700H1836.0L2232.0 -234L2195.0 -244Q2189.0 -283 2185.5 -315.0Q2182.0 -347 2179.5 -375.0Q2177.0 -403 2176.0 -430.0Q2175.0 -457 2175.0 -487.0Q2175.0 -517 2175.0 -553V-700H2366.0V0H2183.0L1761.0 -500L1826.0 -483Q1829.0 -453 1832.5 -428.5Q1836.0 -404 1838.5 -381.0Q1841.0 -358 1842.5 -333.5Q1844.0 -309 1845.0 -278.0Q1846.0 -247 1846.0 -208V0Z M2793.0 0V-335L2805.0 -274L2485.0 -700H2715.0L2954.0 -373L2838.0 -375L3061.0 -700H3281.0L2982.0 -279L2993.0 -343V0Z"/></svg>'

CSS = """
@page{size:A4;margin:16mm 16mm 18mm}
*{box-sizing:border-box}
body{margin:0;font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;color:#1B1B18;font-size:10.5pt;line-height:1.7;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.top{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #1B1B18;padding-bottom:8px}
.logo{height:22px;width:auto}.top .by{font-size:8.5pt;color:#6d6d67}
h1{font-size:20pt;line-height:1.35;margin:22px 0 2px}
.sub{color:#2C7A4B;font-weight:700;margin:0 0 14px}
.lead p{margin:0 0 6px}
h2{font-size:13pt;margin:22px 0 8px;padding-left:10px;border-left:4px solid #2C7A4B;break-after:avoid}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.box{border:1px solid #e3e3de;border-radius:8px;padding:10px 14px;break-inside:avoid}
.box h3{font-size:10.5pt;margin:0 0 4px}.box.meet{background:#eef6f1}.box.text{background:#f6f5f0}
.box ul{margin:0;padding-left:1.2em}
.note{font-size:9.5pt;background:#fff8e6;border-radius:8px;padding:8px 12px;margin:10px 0 0}
table{width:100%;border-collapse:collapse;font-size:9pt;line-height:1.55}
th{background:#1B1B18;color:#fff;text-align:left;padding:6px 8px;font-weight:700}
td{border-bottom:1px solid #e3e3de;padding:7px 8px;vertical-align:top}
tr{break-inside:avoid}
td.k b{display:block;font-size:10pt}td.k span{display:inline-block;margin-top:3px;background:#2C7A4B;color:#fff;border-radius:999px;padding:0 8px;font-weight:700;font-size:8.5pt}
td.cut{color:#8a3a2f}
.memo{display:grid;gap:8px}.memo div{break-inside:avoid}.memo b{display:block}
ol,ul.plain{margin:0;padding-left:1.3em}
.check{list-style:none;padding:0;margin:0;columns:2;column-gap:16px}.check li{padding-left:1.5em;position:relative;margin:0 0 4px;break-inside:avoid}
.check li::before{content:"";position:absolute;left:0;top:.35em;width:.95em;height:.95em;border:1.5px solid #1B1B18;border-radius:3px}
.foot{margin-top:22px;padding-top:10px;border-top:1px solid #e3e3de;font-size:8pt;color:#6d6d67}
.foot .made{display:flex;justify-content:space-between;align-items:center;margin-top:8px;color:#1B1B18;font-size:9pt}
.foot a{color:#2C7A4B;text-decoration:none;font-weight:700}
"""


def page(t):
    kinds = "".join(
        f'<tr><td class="k"><b>{esc(n)}</b><span>{m} {esc(t["min"])}</span></td><td>{esc(a)}</td><td>{esc(b)}</td><td class="cut">{esc(c)}</td></tr>'
        for n, m, a, b, c in t["kinds"])
    li = lambda xs: "".join(f"<li>{esc(x)}</li>" for x in xs)
    return f"""<!doctype html><html lang="{t['lang']}"><head><meta charset="utf-8"><title>{esc(t['title'])}</title><style>{CSS}</style></head><body>
<div class="top">{LOGO}<span class="by">Created by Tany LLC</span></div>
<h1>{esc(t['title'])}</h1><p class="sub">{esc(t['sub'])}</p>
<div class="lead">{''.join(f'<p>{x}</p>' for x in t['lead'])}</div>
<h2>{esc(t['h_when'])}</h2>
<div class="two"><div class="box meet"><h3>{esc(t['meet_h'])}</h3><ul>{li(t['meet'])}</ul></div>
<div class="box text"><h3>{esc(t['text_h'])}</h3><ul>{li(t['text'])}</ul></div></div>
<p class="note">{esc(t['text_note'])}</p>
<h2>{esc(t['h_kinds'])}</h2>
<table><thead><tr>{''.join(f'<th>{esc(c)}</th>' for c in t['cols'])}</tr></thead><tbody>{kinds}</tbody></table>
<h2>{esc(t['h_memo'])}</h2>
<div class="memo">{''.join(f'<div><b>{esc(a)}</b>{esc(b)}</div>' for a, b in t['memo'])}</div>
<h2>{esc(t['h_day'])}</h2><ol>{li(t['day'])}</ol>
<h2>{esc(t['h_after'])}</h2><ul class="plain">{li(t['after'])}</ul>
<h2>{esc(t['h_check'])}</h2><ul class="check">{li(t['check'])}</ul>
<div class="foot">{esc(t['src'])}
<div class="made"><span>{esc(t['book'])}<a href="{BOOK_URL}">tany.im/book</a></span><b>Created by Tany LLC</b></div></div>
</body></html>"""


def build(lang):
    sys.path.insert(0, HERE)
    import wise_report
    os.makedirs(OUT, exist_ok=True)
    doc = page(T[lang])
    with open(os.path.join(OUT, lang + ".html"), "w", encoding="utf-8") as f:
        f.write(doc)
    pdf = wise_report.to_pdf(doc)
    if not pdf:
        raise SystemExit("the PDF could not be made (desk/app/htmlpdf.swift)")
    path = os.path.join(OUT, lang + ".pdf")
    with open(path, "wb") as f:
        f.write(pdf)
    return path


if __name__ == "__main__":
    for lang in sys.argv[1:] or list(T):
        print(build(lang))
