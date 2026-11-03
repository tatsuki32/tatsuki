# VINEPRO ボード（自前サーバー版）

claude.ai の Artifact で動いていた VINEPRO ボードを、スタッフがメールアドレスとパスワードでログインして使えるようにした版です。
ボード本体（`index.html`）は claude.ai 版と同じファイルで、`static/shim.js` が `window.claude` の代わりにこのサーバーへつなぎます。

## できること

| 機能 | 中身 |
|---|---|
| ログイン | メールアドレス＋パスワード。アカウントは管理者（たっちゃん）だけが発行する。10回続けて失敗すると15分止まる |
| 権限 | **管理者**（1人）／**編集できる**／**見るだけ**／**停止中**。管理者だけが変更できる。変えた瞬間に相手の画面が切り替わる |
| メンバー管理 | 追加（仮パスワードを自動発行して、送る文面をコピーできる）・権限変更・停止／再開・パスワード再発行・強制ログアウト・削除 |
| 状態 | オンライン中かどうか（今ボードを開いているか）・最終アクセス・最終ログイン・編集回数・ログイン記録（日時・IP・端末） |
| 編集履歴 | 誰が・いつ・どのボードの・何を（文字・位置・色…）変えたか。変更前→変更後を表示。同じ人が同じ箱を90秒以内に続けて直したら1行にまとめる |
| 元に戻す | 管理者は履歴の1行ごとに「この変更を元に戻す」ができる（戻したことも履歴に残る） |
| 同時編集 | 他の人の変更とカーソルがすぐ反映される（Server-Sent Events） |
| 自動翻訳 | 書いた文字を日本語・ベトナム語・英語に翻訳（`ANTHROPIC_API_KEY` を設定したときだけ） |
| バックアップ | 管理画面の「データ」から JSON で書き出し・読み込み |

画面: ボード右上の自分のアイコン → 編集履歴 / メンバー管理 / パスワード変更 / ログアウト。管理画面は `/admin`。

## 手元で動かす（Mac）

```bash
cd vinepro
mkdir -p data && cp ~/Downloads/vinepro-board/data/all.json data/   # 今のボードの中身（初回だけ自動で読み込む）
python3 server.py adduser --email あなたのメール --name たっちゃん --role owner
python3 server.py
```

→ http://localhost:4848 を開いてログイン。データは `vinepro/var/board.db`（SQLite）に入ります。

- `data/` と `var/` は `.gitignore` 済み。**社内データなのでコミットしないこと**（このリポジトリは公開）。
- 自動翻訳を使うとき: `pip3 install anthropic` して、`ANTHROPIC_API_KEY=... python3 server.py`。

## スタッフ（ベトナムなど）からアクセスできるようにする

サーバーをインターネットに出す必要があります。どちらかを選んでください。

### A. いちばん早い：Mac ＋ Cloudflare Tunnel（無料）

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:4848
```

表示された `https://〜.trycloudflare.com` をスタッフに送れば使えます。
ただし **Mac が起きている間だけ** 使え、URL は起動するたびに変わります。試運転向き。
URL を固定したいときは Cloudflare に自分のドメインを登録して「名前付きトンネル」にします。

### B. 本番向け：クラウドに置く（24時間動く）

`Dockerfile` があるので、ディスク（ボリューム）を付けられるホスティングならどこでも動きます（例: Fly.io、Railway、Render の有料プラン、さくらの VPS）。
大事なのは **`/data` を永続ディスクにする**こと（ここに DB が入る。消えるとボードも消える）。

設定する環境変数:

| 変数 | 値 |
|---|---|
| `VB_OWNER_EMAIL` / `VB_OWNER_PASSWORD` / `VB_OWNER_NAME` | 初回起動時に管理者アカウントを作る（作った後は消してよい） |
| `ANTHROPIC_API_KEY` | 自動翻訳を使うとき |
| `VB_SECURE` | `1`（HTTPS 前提。Dockerfile で設定済み） |

最初に起動したら、管理画面の「データ → データを読み込む」で `data/all.json` を入れてください（GitHub に置かずに済みます）。

例: Fly.io

```bash
cd vinepro
fly launch --no-deploy          # アプリ名とリージョン（Singapore: sin が近い）を選ぶ
fly volumes create vb_data --size 1 --region sin
```

`fly.toml` に次を足してから:

```toml
[mounts]
  source = "vb_data"
  destination = "/data"
```

```bash
fly secrets set VB_OWNER_EMAIL=あなたのメール VB_OWNER_PASSWORD=8文字以上 VB_OWNER_NAME=たっちゃん
fly deploy
```

## コマンド

```bash
python3 server.py                                   # 起動（PORT か VB_PORT、既定 4848）
python3 server.py adduser --email x --name y --role owner|editor|viewer
python3 server.py passwd --email x                  # パスワードを忘れたとき（停止も解除される）
python3 server.py users                             # アカウント一覧と最終ログイン
python3 server.py import data/all.json              # ボードの中身を丸ごと置き換え
python3 server.py export backup.json                # バックアップ
```

## 仕組み

- `server.py` — Python 標準ライブラリだけの HTTP サーバー。SQLite に `users` / `sessions` / `docs`（ボードの中身）/ `history` / `logins`。
  - パスワードは PBKDF2（24万回）でハッシュ化。セッションは HttpOnly クッキー（30日）、DB にはトークンのハッシュだけを保存。
  - 書き込み API は `X-VB: 1` ヘッダー必須（他サイトから送らせる攻撃を防ぐ）。「見るだけ」の人の書き込みはサーバーで拒否。
- `static/shim.js` — `window.claude.use("db" | "user" | "room" | "sample")` をこのサーバーの API で再現するアダプター。右上のアカウントメニューもここ。
- `static/login.html` / `static/admin.html` — ログイン画面と管理画面（日本語・ベトナム語・英語）。
- `index.html` — ボード本体。claude.ai 版と同じファイルを変更せずに使っている（スマホ幅のはみ出しだけ shim.js の CSS で直している）。

API: `POST /api/login` `POST /api/logout` `GET /api/me` `GET /api/profiles` `GET /api/db/<col>` `PUT|PATCH|DELETE /api/db/<col>/<id>` `GET /api/stream`（SSE）`POST /api/presence` `POST /api/sample` `GET /api/history` `GET /api/history/<id>` `POST /api/history/<id>/revert` `POST /api/me/password`、管理者だけ `/api/admin/users`（GET/POST）`/api/admin/users/<id>`（PATCH/DELETE）`/api/admin/users/<id>/password` `/api/admin/users/<id>/logout` `GET /api/admin/logins` `GET /api/admin/export` `POST /api/admin/import`。

## claude.ai 版との関係

claude.ai 版のボードは別のデータで動き続けます。こちらに移るときは、claude.ai 版で最新のデータを書き出して「データを読み込む」で入れ、スタッフにこちらの URL を案内してから claude.ai 版を使うのをやめてください（両方で編集すると内容が分かれます）。
