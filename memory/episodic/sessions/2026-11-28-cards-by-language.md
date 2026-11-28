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
