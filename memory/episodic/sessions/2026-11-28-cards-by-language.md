# 2026-11-28 単語を言語ごとに・Anki のデッキを課ごとに・音声と写真

## ひとこと
- いま：単語を言語ごと課ごとに分けた
- 次：Mac で移し替えと音声を見る

## やったこと
- `desk/anki.py`：デッキを「言語::出どころ::部分」に（`Esperanto::New Express::Lesson 01`）。送ったノートは `changeDeck` で移す。音声は Google → OmniVoice → その言語の Mac の声（なければ無し）。前の版のノートは音声を作り直す（1回10枚）。ノートの型に `Image` の欄を足す。
- `desk/card_media.py`（新規）：Claude Haiku が写真で分かる語と英語の検索語を決め、Wikimedia Commons から作者とライセンスのある写真を取る。Not this・No photo・Find a photo。
- `desk/reader.py`：エスペラントなどを本の言語に（`BOOK_LANGS`）。判定に自信がないと1行で聞く（`langSure`）。言語を変えるとカードと印も移る（`server.book_relang`）。
- `desk/translate.py`：Reader の文の訳は本の言語を `from` で渡す（推測しない）。
- `desk/index.html`：Cards に言語・出どころ・課のタブと写真、Words に言語のタブ。言語の一覧にエスペラント（前は Cards で Save すると English に変わった）。エスペラントの字上符が日本語の字体で崩れるので `[lang=eo]` は標準の字体。
- `.claude/launch.json`：`desk-anki-test` に `DESK_DATA_DIR`（前は本物のカードに書けた）。
- 試験：`test_anki`（16本）、`test_card_media`（5本、新規）、`test_reader` に本の言語の3本。`test_reader.VoiceTest` の1本は前から落ちる（この Mac の声で変わる）。

## 決めたこと
- 一覧の行に ▶ は付けない（行全体がボタンで、中にボタンを入れられない）。読み上げは詳細の Say。
- 音声と写真の欄は、空か Desk が入れたものだけ書き換える（Anki で直したものは残す）。

## 中断した場所
- 途中で `git stash` がサンドボックスに止められた（`.claude/launch.json` を書けない）。`git stash pop` で戻した。比べるための stash はしない。

## 次の一手
- Mac の本番で、Anki を開いて1,089枚が課ごとのデッキに移るか、音声と写真が少しずつ入るかを見る。空になった `Desk::Esperanto` などは本人が消せる。

## 追記：本番で
- 取り込みでサーバーが立ち上げ直らなかった：Mac の checkout で直接コミットすると、`pull.sh` の前後の版が同じになり、server.py が変わったと見なされない。`launchctl kickstart -k gui/$(id -u)/com.tatsuki.desk.server` で立ち上げ直した。
- Anki の Desk のノート（1,106枚）は、移す前に Anki の画面で消されていた（デッキの削除と思われる。Desk のコードに削除の操作はない）。本人の依頼で、Desk の記録を waiting に戻して入れ直した。14:01 に全部入り、音声つきは1,105枚。
- 移すときにノートがなければ `anki.state: gone`（画面は Removed in Anki）にし、勝手に入れ直さないよう直した（ff06e97）。
- Anki の検索で `_` は1文字の記号。デッキ名に `_` があると別のデッキも拾うので、`cardsInfo` の `deckName` で正確に比べる。下線入りの空のデッキはこうして確かめてから消した。
- 写真は 14:00 時点でエスペラントの 116 語に付き、470 語は「写真なし」、526 語は判定待ち（2分ごとに40語ずつ進む）。
