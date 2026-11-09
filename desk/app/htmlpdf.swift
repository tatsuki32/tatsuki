// Turns one local HTML file into a paged A4 PDF with the Mac's own WebKit. Nothing leaves this Mac.
// Usage: htmlpdf input.html output.pdf
import Cocoa
import WebKit

final class Maker: NSObject, WKNavigationDelegate {
    let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 595, height: 842))
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 595, height: 842), styleMask: [.borderless], backing: .buffered, defer: false)
    let output: URL

    init(input: URL, output: URL) {
        self.output = output
        super.init()
        window.contentView = web
        web.navigationDelegate = self
        web.loadFileURL(input, allowingReadAccessTo: input.deletingLastPathComponent())
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let info = NSPrintInfo()
        info.paperSize = NSSize(width: 595, height: 842)
        info.topMargin = 36; info.bottomMargin = 36; info.leftMargin = 36; info.rightMargin = 36
        info.isHorizontallyCentered = false
        info.isVerticallyCentered = false
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.jobDisposition = .save
        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = output
        let job = webView.printOperation(with: info)
        job.showsPrintPanel = false
        job.showsProgressPanel = false
        job.view?.frame = NSRect(x: 0, y: 0, width: 523, height: 770)
        job.runModal(for: window, delegate: self, didRun: #selector(done), contextInfo: nil)
    }

    @objc func done(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        exit(success && FileManager.default.fileExists(atPath: output.path) ? 0 : 1)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { exit(2) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { exit(2) }
}

guard CommandLine.arguments.count == 3 else { exit(64) }
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let maker = Maker(input: URL(fileURLWithPath: CommandLine.arguments[1]), output: URL(fileURLWithPath: CommandLine.arguments[2]))
DispatchQueue.main.asyncAfter(deadline: .now() + 60) { exit(3) }
app.run()
