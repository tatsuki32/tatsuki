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

## 変更の要らないもの

- `desk/server.py`・`desk/index.html`：今のままで新しいツールが Life OS 画面に出ます。
- `desk/sync.py`：今のままで `lifeos_tools()` を通ってオンラインに届きます（Mac からの動作は未確認）。
