// Tatsuki's Desk — the Mac app. A window around http://localhost:4747 and nothing else:
// it starts the server when needed, and everything that is not the Desk opens in the default browser or Finder.
import Cocoa
import WebKit

let deskPort = ProcessInfo.processInfo.environment["DESK_PORT"] ?? "4747"
let deskURL = URL(string: "http://localhost:\(deskPort)/")!
let healthURL = URL(string: "http://127.0.0.1:\(deskPort)/health")!
let retryURL = URL(string: "desk-retry://again")!

// no proxy: while websites are blocked the system proxy points at FocusBlock itself
let noProxy: URLSession = {
    let c = URLSessionConfiguration.ephemeral
    c.connectionProxyDictionary = [:]
    c.timeoutIntervalForRequest = 1
    return URLSession(configuration: c)
}()

func isDesk(_ url: URL?) -> Bool {
    guard let u = url, u.scheme == "http" || u.scheme == "about" else { return false }
    if u.scheme == "about" { return true }
    if u.path.hasPrefix("/api/wise/export") { return false } // a file to save: the default browser downloads it
    return (u.host == "localhost" || u.host == "127.0.0.1") && String(u.port ?? 80) == deskPort
}

func failurePage(_ reason: String) -> String {
    return """
    <!doctype html><html lang="ja"><meta charset="utf-8"><title>Tatsuki's Desk</title>
    <style>
      body{margin:0;min-height:100vh;display:flex;align-items:center;padding:40px;box-sizing:border-box;
           background:#0d0d0d;color:#ece8df;font:14px/1.6 -apple-system,"Hiragino Sans",sans-serif}
      h1{margin:0 0 8px;font-size:20px;font-weight:700}
      p{margin:0 0 24px;color:#9a958a;max-width:40em}
      a{display:inline-block;padding:8px 16px;border-radius:8px;background:#d4af37;color:#111;text-decoration:none;font-weight:700}
    </style>
    <div><h1>Desk を起動できませんでした</h1><p>\(reason)</p><a href="\(retryURL.absoluteString)">もう一度試す</a></div></html>
    """
}

final class DeskController: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1200, height: 800),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = "Tatsuki's Desk"
        window.backgroundColor = NSColor(red: 0.05, green: 0.05, blue: 0.05, alpha: 1)
        window.minSize = NSSize(width: 375, height: 500)
        window.contentView = web
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName("TatsukiDeskMain")
        window.makeKeyAndOrderFront(nil)

        buildMenu()
        NSApp.activate(ignoringOtherApps: true)
        openDesk()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { window.makeKeyAndOrderFront(nil) }
        return true
    }

    // MARK: server

    func checkServer(_ done: @escaping (Bool) -> Void) {
        noProxy.dataTask(with: healthURL) { _, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }

    func startServer() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = ["kickstart", "gui/\(getuid())/com.tatsuki.desk.server"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
    }

    func openDesk() {
        checkServer { ok in
            if ok {
                self.web.load(URLRequest(url: deskURL))
            } else {
                self.startServer()
                self.waitForServer(tries: 12)
            }
        }
    }

    func waitForServer(tries: Int) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            self.checkServer { ok in
                if ok {
                    self.web.load(URLRequest(url: deskURL))
                } else if tries > 1 {
                    self.waitForServer(tries: tries - 1)
                } else {
                    self.web.loadHTMLString(
                        failurePage("サーバーが応答しません。ターミナルで <code>bash ~/TatsukiDesk/desk/setup-mac.sh</code> を実行すると直ることがあります。"),
                        baseURL: nil)
                }
            }
        }
    }

    // MARK: links

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        if url == retryURL {
            openDesk()
            return decisionHandler(.cancel)
        }
        if isDesk(url) { return decisionHandler(.allow) }
        NSWorkspace.shared.open(url)
        decisionHandler(.cancel)
    }

    // target="_blank" and window.open
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if isDesk(url) { webView.load(action.request) } else { NSWorkspace.shared.open(url) }
        }
        return nil
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        waitForServer(tries: 12)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        openDesk()
    }

    // MARK: menu (without an Edit menu, copy and paste do not work in the page)

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let app = NSMenu()
        app.addItem(withTitle: "Tatsuki's Desk を隠す", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(.separator())
        app.addItem(withTitle: "Tatsuki's Desk を終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = app
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "編集")
        edit.addItem(withTitle: "取り消す", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "やり直す", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "カット", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "ペースト", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "すべてを選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "表示")
        let reload = view.addItem(withTitle: "再読み込み", action: #selector(reloadDesk), keyEquivalent: "r")
        reload.target = self
        viewItem.submenu = view
        main.addItem(viewItem)

        let windowItem = NSMenuItem()
        let win = NSMenu(title: "ウインドウ")
        win.addItem(withTitle: "閉じる", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        win.addItem(withTitle: "しまう", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowItem.submenu = win
        main.addItem(windowItem)
        NSApp.windowsMenu = win

        NSApp.mainMenu = main
    }

    @objc func reloadDesk() { openDesk() }
}

let application = NSApplication.shared
let controller = DeskController()
application.delegate = controller
application.setActivationPolicy(.regular)
application.run()
