// Tatsuki's Desk — starts screentime_sync.py and nothing else. Full Disk Access goes to this one program, not to all of Python.
// macOS counts the Python it starts as this program. Rebuilding changes its identity, so setup-mac.sh builds it only once.
import Foundation

let p = Process()
p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
p.arguments = [NSHomeDirectory() + "/TatsukiDesk/desk/screentime_sync.py"]
do {
    try p.run()
    p.waitUntilExit()
    exit(p.terminationStatus)
} catch {
    print(error)
    exit(1)
}
