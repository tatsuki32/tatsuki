// Tatsuki's Desk — asks one running app to quit, or forces it. desk/pc_monitor.py and desk/startup_clean.py start this.
// A normal quit lets the app show its own save prompt, and macOS asks for no permission. Exit 2 means no such app.
//   TatsukiAppCtl quit|force <pid>
//   TatsukiAppCtl list      one line per app in the Dock: pid, bundle id, path, name, launch time (epoch), 1 if frontmost; tab-separated
import AppKit

let args = CommandLine.arguments
if args.count == 2 && args[1] == "list" {
    for app in NSWorkspace.shared.runningApplications where app.activationPolicy == .regular {
        let clean = { (s: String) in s.replacingOccurrences(of: "\t", with: " ").replacingOccurrences(of: "\n", with: " ") }
        let fields = [
            String(app.processIdentifier),
            clean(app.bundleIdentifier ?? ""),
            clean(app.bundleURL?.path ?? ""),
            clean(app.localizedName ?? ""),
            String(Int(app.launchDate?.timeIntervalSince1970 ?? 0)),
            app.isActive ? "1" : "0",
        ]
        print(fields.joined(separator: "\t"))
    }
    exit(0)
}
guard args.count == 3, ["quit", "force"].contains(args[1]), let pid = Int32(args[2]),
      let app = NSRunningApplication(processIdentifier: pid) else { exit(2) }
exit((args[1] == "force" ? app.forceTerminate() : app.terminate()) ? 0 : 1)
