# 統合のときに入れる変更

## Desk の画面を5秒以内に更新する（本人の判断が必要）
今の Mac 版は、タスクを30秒ごとか、タブに戻ったときに読み直します。開いたまま見ていると、追加から表示まで最大30秒かかります。
仕様書の「5秒以内」を画面でも満たすには、`desk/index.html` の `setInterval(loadApiMemos,30000);` の直後に次を足します。未検証です。

```js
{let last="";setInterval(async()=>{if(document.hidden)return;try{const t=await api("GET","/api/tasks"),s=JSON.stringify(t);if(s!==last){if(last)S.tasks=t.filter(x=>!x.deleted),render();last=s}}catch(e){}},5000)}
```

オンライン版への反映は毎時同期なので、最大1時間かかります。5秒にはなりません。

## ライブラリ
`core.py` は、Desk が一覧に出しているだけで未保存のメール・Google タスクを完了にするため、`tasks._put` を呼んでいます。
`lifeos/lib/tasks.py` に公開の `put(rec)` を足すなら、呼び先を置き換えてください。
