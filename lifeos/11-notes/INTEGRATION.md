# 11 メモ自動連携：共有ファイルの残り

## 2. ノートの一覧を分ける：`desk/index.html`（なくても動きます）

メモが増えると、ノートの一覧で自分のノートが埋もれます。`notesView` の次の3行を置き換えると、このツールのノートを折りたたみに分けられます。
使い捨てのコピーで、折りたたみの外に自分のノートだけが並ぶこと、中のノートが開くことを確かめました。画面の変更なので、入れるときは `.claude/rules/design-ui.md` の手順で見てください。

```js
  const list=noteList().sort((a,b)=>(b.updatedAt||0)-(a.updatedAt||0));
  return `<header class="head"><div><p class="eyebrow">ノート</p><h1>ノート</h1></div><button class="btn primary" id="newNote">＋ 新しいノート</button></header>
  ${list.length?`<div class="ngrid">${list.map(x=>`<button class="card ncard" data-nopen="${esc(x.id)}"><b>${esc(x.title)}</b><span>${esc(String(x.body||"").replace(/[\[\]#]/g,"").slice(0,90))}</span></button>`).join("")}</div>`
```

```js
  const sorted=noteList().sort((a,b)=>(b.updatedAt||0)-(a.updatedAt||0));
  const list=sorted.filter(x=>!String(x.id).startsWith("ln-")), auto=sorted.filter(x=>String(x.id).startsWith("ln-"));
  const cards=l=>`<div class="ngrid">${l.map(x=>`<button class="card ncard" data-nopen="${esc(x.id)}"><b>${esc(x.title)}</b><span>${esc(String(x.body||"").replace(/[\[\]#]/g,"").slice(0,90))}</span></button>`).join("")}</div>`;
  return `<header class="head"><div><p class="eyebrow">ノート</p><h1>ノート</h1></div><button class="btn primary" id="newNote">＋ 新しいノート</button></header>
  ${auto.length?`<details class="card"><summary>メモ自動連携のノート <span class="count">${auto.length}</span></summary>${cards(auto)}</details>`:""}
  ${list.length?cards(list)
```

## 3. ライブラリ：`lifeos/lib/tasks.py`

`desk_notes.py` が `tasks._call` と `tasks._server_up` を使っています。名前に `_` が付いたままなので、変えるときは一緒に直してください。

## 4. `lifeos/README.md` の表

`briefs/` の11番のファイルは `briefs/<日付か週か年>-memo.md` です。01番が `briefs/YYYY-MM-DD.md` を丸ごと保存するので、同じファイルに書くと消し合います。表の説明を直すと分かりやすくなります。

## 5. 写真の受け口

ボットは文字と音声だけを受けます。Telegram の写真をメモにするなら、`lifeos/bot/run.py` で `notes/` に保存する処理が要ります。今は手で `notes/` に置きます。
