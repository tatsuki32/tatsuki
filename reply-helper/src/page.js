// 画面（1枚の HTML）。外部ライブラリ・Web フォントなし。
export const PAGE = `<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>返信のたたき台</title>
<style>
  :root {
    --bg: #0d0d0d;
    --card: #171717;
    --line: #2a2a2a;
    --text: #e8e6e1;
    --sub: #9a968e;
    --gold: #d4af5f;
    --gold-dark: #3a2f17;
    --err: #e07a6a;
    --s1: 4px; --s2: 8px; --s3: 16px; --s4: 24px; --s5: 40px;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--text); }
  body {
    font-family: -apple-system, "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", Meiryo, sans-serif;
    font-size: 14px; line-height: 1.6; font-weight: 400;
    -webkit-text-size-adjust: 100%;
  }
  main { max-width: 40em; margin: 0 auto; padding: var(--s5) var(--s3); }
  h1 { font-size: 20px; font-weight: 700; margin: 0 0 var(--s1); }
  .lead { color: var(--sub); font-size: 12px; margin: 0 0 var(--s5); }
  label { display: block; font-size: 12px; color: var(--sub); margin-bottom: var(--s2); }
  input, textarea {
    width: 100%; background: var(--card); color: var(--text);
    border: 1px solid var(--line); border-radius: 8px;
    font: inherit; font-size: 16px; padding: var(--s3);
  }
  textarea { min-height: 160px; resize: vertical; line-height: 1.6; }
  input:focus, textarea:focus { outline: none; border-color: var(--gold); }
  .row { display: flex; gap: var(--s3); align-items: center; margin-top: var(--s3); }
  button {
    font: inherit; font-size: 14px; font-weight: 700; cursor: pointer;
    border-radius: 8px; padding: 12px var(--s4); border: 1px solid transparent;
  }
  .primary { background: var(--gold); color: #141006; }
  .primary:disabled { opacity: .5; cursor: default; }
  .plain { background: none; color: var(--sub); border: none; padding: 12px 0; font-weight: 400; }
  .plain:hover { color: var(--text); }
  .msg { font-size: 12px; color: var(--err); margin: var(--s2) 0 0; min-height: 1.6em; }
  .busy { font-size: 12px; color: var(--sub); }
  #result { margin-top: var(--s5); }
  .summary { font-size: 12px; color: var(--sub); margin: 0 0 var(--s4); }
  .summary b { color: var(--text); font-weight: 700; }
  .card {
    background: var(--card); border-radius: 8px; padding: var(--s3);
    margin-bottom: var(--s3);
  }
  .card h2 { font-size: 12px; font-weight: 700; color: var(--gold); margin: 0 0 var(--s2); }
  .en { font-size: 16px; line-height: 1.6; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
  .ja { font-size: 12px; color: var(--sub); margin: var(--s2) 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
  .copy {
    margin-top: var(--s3); background: none; color: var(--text);
    border: 1px solid var(--line); padding: var(--s2) var(--s3);
  }
  .copy.done { border-color: var(--gold); color: var(--gold); }
  .foot { font-size: 12px; color: var(--sub); margin-top: var(--s5); }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<main>
  <h1>返信のたたき台</h1>
  <p class="lead">相手のメッセージを貼ると、丁寧で短い英語の返信案が3つ出ます。</p>

  <section id="login">
    <form id="loginForm">
      <label for="pw">パスワード</label>
      <input id="pw" type="password" autocomplete="current-password" required>
      <div class="row"><button class="primary" type="submit">入る</button></div>
      <p class="msg" id="loginMsg" role="alert"></p>
    </form>
  </section>

  <section id="app" hidden>
    <form id="replyForm">
      <label for="text">相手のメッセージ</label>
      <textarea id="text" placeholder="ここに貼る" required></textarea>
      <div class="row">
        <button class="primary" id="go" type="submit">返信案を作る</button>
        <button class="plain" id="clear" type="button">消す</button>
        <span class="busy" id="busy" hidden>考えています…</span>
      </div>
      <p class="msg" id="replyMsg" role="alert"></p>
    </form>
    <div id="result" hidden>
      <p class="summary">相手の言いたいこと：<b id="summary"></b></p>
      <div id="cards"></div>
    </div>
    <p class="foot">貼った文章は保存しません。送る前に、自分の言葉で少し直してもかまいません。</p>
  </section>
</main>

<script>
(function () {
  var TONE = { soft: "やわらかく断る", firm: "はっきり線を引く", short: "短く終える" };
  var password = "";
  try { password = sessionStorage.getItem("pw") || ""; } catch (e) {}

  var $ = function (id) { return document.getElementById(id); };

  function show(loggedIn) {
    $("login").hidden = loggedIn;
    $("app").hidden = !loggedIn;
    if (loggedIn) $("text").focus(); else $("pw").focus();
  }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { d._status = r.status; return d; });
    });
  }

  $("loginForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var pw = $("pw").value;
    $("loginMsg").textContent = "";
    post("/api/login", { password: pw }).then(function (d) {
      if (d.ok) {
        password = pw;
        try { sessionStorage.setItem("pw", pw); } catch (e) {}
        show(true);
      } else {
        $("loginMsg").textContent = d.error || "入れませんでした。";
      }
    }).catch(function () { $("loginMsg").textContent = "通信できませんでした。"; });
  });

  function copyText(text, btn) {
    function done() {
      btn.textContent = "コピーしました";
      btn.classList.add("done");
      setTimeout(function () { btn.textContent = "コピー"; btn.classList.remove("done"); }, 1600);
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(text); done(); });
    } else { fallback(text); done(); }
  }
  function fallback(text) {
    var t = document.createElement("textarea");
    t.value = text; t.setAttribute("readonly", "");
    t.style.position = "fixed"; t.style.opacity = "0";
    document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(t);
  }

  function render(d) {
    $("summary").textContent = d.summary_ja || "";
    var box = $("cards");
    box.textContent = "";
    (d.replies || []).forEach(function (r) {
      var card = document.createElement("div"); card.className = "card";
      var h = document.createElement("h2"); h.textContent = TONE[r.tone] || r.tone;
      var en = document.createElement("p"); en.className = "en"; en.textContent = r.en; en.lang = "en";
      var ja = document.createElement("p"); ja.className = "ja"; ja.textContent = r.ja;
      var b = document.createElement("button"); b.className = "copy"; b.type = "button"; b.textContent = "コピー";
      b.addEventListener("click", function () { copyText(r.en, b); });
      card.appendChild(h); card.appendChild(en); card.appendChild(ja); card.appendChild(b);
      box.appendChild(card);
    });
    $("result").hidden = false;
  }

  $("replyForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var text = $("text").value.trim();
    if (!text) return;
    $("replyMsg").textContent = "";
    $("go").disabled = true; $("busy").hidden = false;
    post("/api/reply", { password: password, message: text }).then(function (d) {
      if (d._status === 401) {
        try { sessionStorage.removeItem("pw"); } catch (e) {}
        show(false);
        $("loginMsg").textContent = "もう一度パスワードを入れてください。";
        return;
      }
      if (d.error) { $("replyMsg").textContent = d.error; return; }
      render(d);
    }).catch(function () {
      $("replyMsg").textContent = "通信できませんでした。";
    }).then(function () { $("go").disabled = false; $("busy").hidden = true; });
  });

  $("clear").addEventListener("click", function () {
    $("text").value = ""; $("result").hidden = true; $("replyMsg").textContent = ""; $("text").focus();
  });

  show(!!password);
})();
</script>
</body>
</html>`;
