// Tatsuki's Desk — the eyes and the one mouse button of the LINE check. desk/line_sync.py decides; this only does.
// launchd starts `run`, which starts line_sync.py, so macOS counts that Python and its calls back into this program as this
// one program: Screen Recording and Accessibility go here and not to all of Python. Rebuilding changes its identity and loses
// them, so setup-mac.sh builds it only once.
// It has no way to press a key, and it clicks only in the chat list at the left of the window: it cannot type or send.
import AppKit

let bundleId = "jp.naver.line.mac"
let listWidth = 420.0  // points from the window's left edge; the message box is further right

func say(_ value: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}
func fail(_ why: String) -> Never { say(["error": why]); exit(1) }

func lineApp() -> NSRunningApplication? { NSRunningApplication.runningApplications(withBundleIdentifier: bundleId).first }

func lineWindow() -> (id: CGWindowID, rect: CGRect)? {
    guard let pid = lineApp()?.processIdentifier,
          let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return nil }
    var best: (id: CGWindowID, rect: CGRect)?
    for w in list where (w[kCGWindowOwnerPID as String] as? Int32) == pid && (w[kCGWindowLayer as String] as? Int) == 0 {
        guard let b = w[kCGWindowBounds as String] as? NSDictionary, let r = CGRect(dictionaryRepresentation: b),
              let id = w[kCGWindowNumber as String] as? CGWindowID, r.width > 200, r.height > 200 else { continue }
        if best == nil || r.width * r.height > best!.rect.width * best!.rect.height { best = (id, r) }
    }
    return best
}

func isLocked() -> Bool {
    guard let s = CGSessionCopyCurrentDictionary() as? [String: Any] else { return true }  // no session on screen: nobody can be seen
    return (s["CGSSessionScreenIsLocked"] as? Bool ?? false) || (s[kCGSessionOnConsoleKey as String] as? Bool == false)
}

// This program's own clicks count here too; line_sync.py allows for them.
func idleSeconds() -> Double {
    let kinds: [CGEventType] = [.mouseMoved, .leftMouseDown, .rightMouseDown, .keyDown, .scrollWheel, .flagsChanged]
    return kinds.map { CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: $0) }.min() ?? 0
}

func spin(_ seconds: Double) { RunLoop.current.run(until: Date(timeIntervalSinceNow: seconds)) }

// Refuses unless LINE is really in front: a click must never land in whatever else is on the screen.
func pointInList(_ a: [String]) -> CGPoint {
    guard a.count >= 2, let x = Double(a[0]), let y = Double(a[1]), let win = lineWindow() else { fail("no window") }
    guard NSWorkspace.shared.frontmostApplication?.bundleIdentifier == bundleId else { fail("not in front") }
    guard x >= 0, x <= listWidth, x < win.rect.width, y >= 0, y < win.rect.height else { fail("outside the list") }
    return CGPoint(x: win.rect.minX + x, y: win.rect.minY + y)
}

var args = Array(CommandLine.arguments.dropFirst())
let command = args.isEmpty ? "" : args.removeFirst()

switch command {
case "run":
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    p.arguments = [NSHomeDirectory() + "/TatsukiDesk/desk/line_sync.py"] + (args.isEmpty ? ["tick"] : args)
    do { try p.run(); p.waitUntilExit(); exit(p.terminationStatus) } catch { print(error); exit(1) }

case "status":
    say(["locked": isLocked(), "idle": idleSeconds(), "screen": CGPreflightScreenCaptureAccess(), "access": AXIsProcessTrusted(),
         "running": lineApp() != nil, "front": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0])

case "ask":  // shows macOS's own permission prompts; the person answers them
    let screen = CGRequestScreenCaptureAccess()
    let access = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
    say(["screen": screen, "access": access])

case "open":
    let previous = NSWorkspace.shared.frontmostApplication
    let wasRunning = lineApp() != nil
    guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleId) else { fail("no LINE") }
    let config = NSWorkspace.OpenConfiguration()
    config.activates = true
    NSWorkspace.shared.openApplication(at: url, configuration: config) { _, _ in }
    let until = Date(timeIntervalSinceNow: 40)
    while Date() < until {
        spin(0.5)
        if lineWindow() != nil && NSWorkspace.shared.frontmostApplication?.bundleIdentifier == bundleId { break }
    }
    guard let win = lineWindow() else { fail("no window") }
    spin(wasRunning ? 1 : 8)  // a fresh start fetches what came while it was closed
    say(["launched": !wasRunning, "previous": previous?.processIdentifier ?? 0, "w": win.rect.width, "h": win.rect.height,
         "front": NSWorkspace.shared.frontmostApplication?.bundleIdentifier == bundleId])

case "shot":
    guard args.count == 1 else { fail("usage") }
    guard let win = lineWindow() else { fail("no window") }
    guard let image = CGWindowListCreateImage(.null, .optionIncludingWindow, win.id, [.boundsIgnoreFraming, .bestResolution]) else { fail("no image") }
    guard let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else { fail("no png") }
    do { try png.write(to: URL(fileURLWithPath: args[0]), options: .atomic) } catch { fail("cannot write") }
    say(["w": win.rect.width, "h": win.rect.height, "pxw": image.width, "pxh": image.height])

case "click":
    let p = pointInList(args)
    let home = CGEvent(source: nil)?.location
    for type in [CGEventType.mouseMoved, .leftMouseDown, .leftMouseUp] {
        CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
        spin(0.08)
    }
    if let home = home { CGWarpMouseCursorPosition(home) }
    say(["ok": true])

case "scroll":  // x y lines; positive goes up, negative goes down. Anywhere in the window: a wheel opens and sends nothing.
    guard args.count == 3, let x = Double(args[0]), let y = Double(args[1]), let lines = Int32(args[2]), let win = lineWindow() else { fail("usage") }
    guard NSWorkspace.shared.frontmostApplication?.bundleIdentifier == bundleId else { fail("not in front") }
    let p = CGPoint(x: win.rect.minX + x, y: win.rect.minY + y)
    let home = CGEvent(source: nil)?.location
    CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
    spin(0.1)
    CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 1, wheel1: lines, wheel2: 0, wheel3: 0)?.post(tap: .cghidEventTap)
    spin(0.5)
    if let home = home { CGWarpMouseCursorPosition(home) }
    say(["ok": true])

case "restore":  // launched(0|1) previousPid: closes LINE if the check opened it, and gives the screen back
    guard args.count == 2 else { fail("usage") }
    if args[0] == "1" { lineApp()?.terminate() }
    if let pid = Int32(args[1]), pid > 0 { NSRunningApplication(processIdentifier: pid)?.activate(options: []) }
    say(["ok": true])

default:
    fail("usage: run|status|ask|open|shot|click|scroll|restore")
}
