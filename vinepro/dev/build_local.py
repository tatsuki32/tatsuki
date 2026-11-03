# index.html（claude.ai の Artifact 用の本体）を、ローカルで開ける dev/local.html に包む。
# 使い方:  python3 dev/build_local.py && python3 -m http.server 8000  → http://localhost:8000/dev/local.html
import os
HERE = os.path.dirname(os.path.abspath(__file__))
page = open(os.path.join(HERE, "..", "index.html"), encoding="utf-8").read()
head = ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<style>:root{color-scheme:light}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>'
        '<script src="mock-claude.js"></script></head><body>')
open(os.path.join(HERE, "local.html"), "w", encoding="utf-8").write(head + page + "</body></html>")
print("wrote dev/local.html")
