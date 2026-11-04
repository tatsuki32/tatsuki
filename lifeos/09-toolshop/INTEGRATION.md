# 09 ツール工房：統括に頼むこと

## `lifeos/tests/test_base_desk.py` を直す（必須）

今のテストはツールがちょうど18個だと決めています。`/newtool` で19個目を作ると、全テストが1件落ちます。
18個が揃っていることは確かめたまま、増えたツールも同じ形か調べるようにします。

```diff
-        self.assertEqual(found, SLUGS)
+        self.assertEqual([s for s in found if s in SLUGS], SLUGS)
         commands = []
-        for slug in SLUGS:
+        for slug in found:
```

一時コピーでこの変更を当て、19個目がある状態で全テストが通ることを確かめました。

## `CLAUDE.md` の一言を直す

「決めた8つのコマンド」を「決めた9つのコマンド」にします。`/newtool` が増えたためです。

## このブランチに入っている Desk の変更

本人の依頼で、Desk の変更はこのブランチに直接入れました。ほかのブランチの Desk の変更と同じ行は触っていません。

- `desk/server.py`：`CLAUDE_CMDS` に `/newtool`。
- `desk/index.html`：「Claude に頼む」に「ツールを作る」、Life OS 画面の見出しに「作りかけ N / 3」。

## 変更の要らないもの

- `desk/sync.py`：今のままで `lifeos_tools()` を通ってオンラインに届きます（Mac からの動作は未確認）。
