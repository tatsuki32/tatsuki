// Tatsuki's Desk — launchd, every minute. If Chrome or Brave is in front, adds one line with the site's name to
// desk/data/screentime/tabs/<date>.log, so one line is one minute. Never the page address, never a private window.
import Cocoa

let browsers = ["com.google.Chrome": "chrome", "com.brave.Browser": "brave"]
let dir = NSHomeDirectory() + "/TatsukiDesk/desk/data/screentime/tabs"

func report(_ text: String) {
    try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
    try? text.write(toFile: dir + "/status.txt", atomically: true, encoding: .utf8)
}

let session = CGSessionCopyCurrentDictionary() as? [String: Any]
if session?["CGSSessionScreenIsLocked"] as? Bool == true || CGDisplayIsAsleep(CGMainDisplayID()) != 0 { exit(0) }
guard let id = NSWorkspace.shared.frontmostApplication?.bundleIdentifier, let name = browsers[id] else { exit(0) }

let source = """
tell application id "\(id)"
  if (count of windows) is 0 then return ""
  if mode of front window is "incognito" then return ""
  return URL of active tab of front window
end tell
"""
var failure: NSDictionary?
guard let answer = NSAppleScript(source: source)?.executeAndReturnError(&failure) else {
    let code = failure?[NSAppleScript.errorNumber] as? Int ?? 0
    report(code == -1743 ? "denied" : "error \(code)")
    exit(1)
}
report("ok")

guard let parts = URLComponents(string: answer.stringValue ?? ""), ["http", "https"].contains(parts.scheme ?? ""),
      var host = parts.host?.lowercased() else { exit(0) }
if host.hasPrefix("www.") { host.removeFirst(4) }

let day = DateFormatter()
day.locale = Locale(identifier: "en_US_POSIX")
day.timeZone = TimeZone(identifier: "Asia/Ho_Chi_Minh")
day.dateFormat = "yyyy-MM-dd"
let path = dir + "/" + day.string(from: Date()) + ".log"
if !FileManager.default.fileExists(atPath: path) { FileManager.default.createFile(atPath: path, contents: nil) }
if let file = FileHandle(forWritingAtPath: path) {
    file.seekToEndOfFile()
    file.write("\(name) \(host)\n".data(using: .utf8)!)
    file.closeFile()
}
