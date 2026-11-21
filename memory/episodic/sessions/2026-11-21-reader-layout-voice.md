# 2026-11-21 Reader の本を広く、声を良く

## ひとこと
- いま：本を大きく、右の欄を短くした
- 次：Premium の声を Mac に入れる
- 待ち：なし（エスペラントは8回で決まり）

## やったこと
- 本のページを単行本の形（128:188、幅800pxまで）にして左に寄せた。全画面の 960px の枠（`#view.wrap`）を、本を開いている間だけ外す（`.wrap:has(> .rdread)`）。
- 右の欄：数字は1行、言葉のカードは日本語と短い英語だけ（品詞と例文を外した）、一覧は1語1行で同じ語は ×N にまとめた。
- 声：おもちゃの声を外し、Premium → Enhanced → ふつう → Eloquence の順に並べた。保存の鍵を `desk-rd-voice2-` に変え、前に選んだ Grandma から一度いちばん良い声に戻す。「Get a better voice…」で入れ方を出す。

## 調べたこと
- この Mac には英語の Premium / Enhanced の声が入っていない（`say -v '?'`）。入れると `say` と Web Speech の両方で使える。
- 自然さで一番は Google Cloud TTS の Chirp 3: HD（月100万字まで無料、セルビア語もある）。本の文を1文ずつ Google に送ることになるので、使うかは本人に聞く。Kokoro-82M は英語向きで Python 3.10 以上が要る。

## 気づき
- 声の大きさのエミュレーションでは、ブラウザ操作のクリックの位置が本文の `caretRangeFromPoint` とずれる。試すときはページの中から単語の位置にクリックを送る。

## 追記：Google の声
- 本人が「Yes, add Google」。`desk/gtts.py` を足した（Chirp 3: HD、1文ずつ、音声は手元に残して二度送らない、月100万字で止めて Mac の声へ）。
- 鍵はまだない（`tatsuki-desk-gtts`・`tatsuki-desk-gtranslate` とも無し）。鍵ができるまでは今までどおり Mac の声。
- 速さを受けない声には、速さを外してもう一度頼む。

## 追記：Google の課金は見送り、Mac の Premium とエスペラントの OmniVoice
- Google Cloud は Text-to-Speech に支払いの登録が要ったので、本人が Mac の声にすると決めた（Google の部品は鍵があれば動くまま残す）。本人が Premium／Enhanced の声を入れた。`reader.voices()` は10分ごとに読み直す。
- エスペラントは Mac・Google・Azure・Polly・ElevenLabs のどれにもない。OmniVoice（k2-fsa、CC-BY-NC）を `desk/omnivoice/engine/` に入れた（約5GB）。M 系 Mac で num_step 32 は 4.5秒の音声に13秒、8 は3.6秒。8 にして、本番の Desk で2文目から1文約2.8秒。
- 本番の `~/TatsukiDesk` に直接ファイルを書くと、5分ごとの git pull が止まる。試すときは worktree のコードを本番の venv で動かす。
