// Tatsuki's Desk — asks one running app to quit, or forces it. desk/pc_monitor.py starts this.
// A normal quit lets the app show its own save prompt, and macOS asks for no permission. Exit 2 means no such app.
import AppKit

let args = CommandLine.arguments
guard args.count == 3, ["quit", "force"].contains(args[1]), let pid = Int32(args[2]),
      let app = NSRunningApplication(processIdentifier: pid) else { exit(2) }
exit((args[1] == "force" ? app.forceTerminate() : app.terminate()) ? 0 : 1)
