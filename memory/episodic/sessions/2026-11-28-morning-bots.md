# 2026-11-28 朝の記録ボット（Telegram ×2 → スプレッドシート → Desk）

## ひとこと
- いま：朝4時に聞くボットを作った
- 次：本人がボットと表を準備
- 待ち：ボット作成と鍵の入力

## やったこと
- `lifeos/22-morning-bots/Code.js`：Google Apps Script。1分ごとの trigger で両ボットを getUpdates（webhook は Apps Script の 302 で二重配信になりうるので使わない）。4:00〜12:00（`TZ` の時計）に未送信なら1回聞く。持ち主は最初の `/start`（private chat のみ）。
- Claude は raw HTTP（Apps Script に SDK がない）で `output_config.format` の json_schema。Haiku 5.5。nullable は `anyOf` で書いた。
- シートの文字列は先頭に `'` を付けて入れる（日付・時刻が Sheets の日付に化けるのと、`=` で始まる文の数式化を防ぐ）。行 id は先頭を英字に（16進が数字や指数に化けるため）。
- Meals の Undo の callback_data は64バイトまで → 1つの案は食事の種類ごとに1行（最大4行）にまとめた。
- 時間帯：Mac の `/etc/localtime` を Desk が毎日 `op=tz` で送る。位置の送信・`/tz` は、Mac の時間帯が変わるまで勝つ（`MAC_TZ` と比べる）。
- `desk/lifebots.py`：取り込みは `lifelog.check` を通すので範囲外は review に回る。id は `mb-<行id>`。
- 試験：`node --test lifeos/22-morning-bots/test/*.mjs`（vm に偽の Apps Script を作って Code.js を走らせる）11件、`desk.tests.test_lifebots` 5件。

## 決めたこと
- 本人の「もう伝えることない」を受け、質問なしで要件定義（https://claude.ai/artifact/7M9jcYFCYoaw5Wro11ps4t ）→ OK → 実装。
- lifeos のテストの既存の失敗4件（11・14・replywin・base の一部）はこの変更の前からある。

## 導入（同じ日の午後）
- ボット2つを BotFather で作成、スプレッドシートに Apps Script を貼って setup・ウェブアプリのデプロイ（アクセス：全員）、Mac で `lifebots.py setup`（setup true）。
- 鍵3つと Desk key は本人が貼った。Apps Script のコードは GitHub の raw から Monaco に流し込むと速い。
- 最初の /start は setup の前に送ると拾われなかった。setup のあとに送り直すと両方とも返事が来た。
- 残り：朝4時の問いと、翌朝 5:00 以降の Desk への取り込み。
