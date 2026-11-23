// Tatsuki's Desk — Glance's eyes: one photo from the built-in camera and one picture of the screen, when asked.
// desk/glance.py starts it with `open` so macOS asks for Camera and Screen Recording for this app alone, not for Python.
// Rebuilding changes its identity and loses those permissions, so glance.py builds it only once.
//
//   TatsukiGlance status <folder>     writes <folder>/result.json: {camera: allowed|denied|ask, screen: true|false}
//   TatsukiGlance setup <folder>      lets macOS ask for both permissions (its own prompts), then the same as status
//   TatsukiGlance shoot <folder> [--skip id,id] [--no-face] [--allow-mic] [--face 640] [--screen 1568]
//     writes <folder>/screen.jpg, <folder>/face.jpg (when there is one) and <folder>/result.json
//
// What it refuses, whoever starts it and with whatever arguments:
// - <folder> must be a ".shot-…" folder inside a folder named "glance" (the Desk's own work folder): it writes nowhere else.
// - Nothing is taken while the screen is locked, while a never-capture app is in front, while a private browser window
//   is on screen, or while another app is using a camera or a microphone (a call or a recording: other people would be
//   in the picture). All of it is checked before the pictures and again after them, before anything is written.
// - The never-capture apps in `alwaysSkip` cannot be switched off from outside; --skip only adds to them. Their windows
//   are left out of the picture even when they sit behind.
// Pictures are made small before they are written. It sends nothing anywhere and keeps nothing.
// What it cannot do: tell apart the programs of this Mac's own user. Any of them could start it, as with every app that
// holds a permission; the rules above are what such a program would still get.
import AppKit
import AVFoundation
import CoreAudio
import ScreenCaptureKit

let args = CommandLine.arguments
let folder = URL(fileURLWithPath: args[2], isDirectory: true).standardizedFileURL  // read only after main() has checked the arguments

func option(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}
// LINE, password managers, Keychain Access, System Settings. The same list as SKIP in glance.py; a prefix covers versions and helpers.
let alwaysSkip = ["jp.naver.line.mac", "com.1password", "com.agilebits.onepassword", "com.apple.Passwords", "com.apple.keychainaccess",
                  "com.bitwarden.desktop", "com.apple.systempreferences"]
let skip = alwaysSkip + (option("--skip") ?? "").split(separator: ",").map(String.init).filter { !$0.isEmpty }
let wantFace = !args.contains("--no-face")
let micCounts = !args.contains("--allow-mic")
let faceMax = min(max(Int(option("--face") ?? "") ?? 640, 160), 640)
let screenMax = min(max(Int(option("--screen") ?? "") ?? 1568, 320), 1568)
let privateMarks = ["Incognito", "シークレット", "Private Browsing", "プライベートブラウズ", "InPrivate"]

// Goes up whenever a rule in this file changes. glance.py (APP_VERSION) refuses an app that answers with a lower number,
// because an app built from an older copy of this file would be missing the newer rules.
let version = 2

func finish(_ value: [String: Any], code: Int32 = 0) -> Never {
    // written last and read by glance.py only after this program has ended, so a plain write is enough
    let data = try! JSONSerialization.data(withJSONObject: value.merging(["v": version]) { a, _ in a }, options: [.sortedKeys])
    try? data.write(to: folder.appendingPathComponent("result.json"))
    exit(code)
}

func cameraState() -> String {
    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized: return "allowed"
    case .denied, .restricted: return "denied"
    default: return "ask"
    }
}
func permissions() -> [String: Any] { ["camera": cameraState(), "screen": CGPreflightScreenCaptureAccess()] }

func isLocked() -> Bool {
    guard let s = CGSessionCopyCurrentDictionary() as? [String: Any] else { return true }  // no session on screen: nobody can be seen
    return (s["CGSSessionScreenIsLocked"] as? Bool ?? false) || (s[kCGSessionOnConsoleKey as String] as? Bool == false)
}

// "com.agilebits.onepassword" also covers "com.agilebits.onepassword7" and an app's helpers
func skipped(_ bundle: String?) -> Bool {
    guard let b = bundle, !b.isEmpty else { return false }
    return skip.contains { b == $0 || b.hasPrefix($0) }
}

// The Mac's own camera only: never an iPhone used as a camera, never an external one.
func builtInCamera() -> AVCaptureDevice? {
    let found = AVCaptureDevice.DiscoverySession(deviceTypes: [.builtInWideAngleCamera], mediaType: .video, position: .unspecified).devices
    return found.first { !$0.isContinuityCamera }
}

// Any camera at all, in use by another app: built-in, external, or an iPhone.
func cameraBusy() -> Bool {
    let kinds: [AVCaptureDevice.DeviceType] = [.builtInWideAngleCamera, .external, .continuityCamera, .deskViewCamera]
    return AVCaptureDevice.DiscoverySession(deviceTypes: kinds, mediaType: .video, position: .unspecified).devices
        .contains { $0.isInUseByAnotherApplication }
}

// Core Audio, read only. Each returns nil when Core Audio will not answer; the callers count that as "in use".
func audioNumber(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32? {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var value = UInt32(0), size = UInt32(MemoryLayout<UInt32>.size)
    return AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &value) == noErr ? value : nil
}
func audioObjects(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> [AudioObjectID]? {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(0)
    guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr else { return nil }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    if ids.isEmpty { return [] }
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &ids) == noErr else { return nil }
    return ids
}

// The programs listening to a microphone right now, other than this one; nil when Core Audio will not say.
// macOS 14.2 and later tells it per program, whatever kind of device the sound comes in through. Before that, any device
// that can take sound in and is running counts (a headset playing music too: better to skip one than to take a call).
func microphoneUsers() -> [String]? {
    let system = AudioObjectID(kAudioObjectSystemObject)
    if #available(macOS 14.2, *) {
        guard let programs = audioObjects(system, kAudioHardwarePropertyProcessObjectList) else { return nil }
        var users: [String] = []
        for p in programs {
            guard let listening = audioNumber(p, kAudioProcessPropertyIsRunningInput) else { return nil }
            if listening == 0 { continue }
            guard let pid = audioNumber(p, kAudioProcessPropertyPID) else { return nil }
            if pid_t(bitPattern: pid) == getpid() { continue }
            users.append(NSRunningApplication(processIdentifier: pid_t(bitPattern: pid))?.localizedName ?? "another program")
        }
        return users
    }
    guard let devices = audioObjects(system, kAudioHardwarePropertyDevices) else { return nil }
    for d in devices {
        guard let inputs = audioObjects(d, kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput) else { return nil }
        if inputs.isEmpty { continue }
        guard let running = audioNumber(d, kAudioDevicePropertyDeviceIsRunningSomewhere) else { return nil }
        if running != 0 { return ["a microphone"] }
    }
    return []
}

// Who makes this a call or a recording: [] when nobody does.
func callers() -> [String] {
    var who: [String] = cameraBusy() ? ["a camera in use"] : []
    if micCounts {
        if let users = microphoneUsers() { who += users } else { who.append("microphone state unknown") }
    }
    return who
}
func onCall() -> Bool { !callers().isEmpty }

// A private browser window anywhere on screen, in front or behind. Browsers that do not say so in the window's title
// (Safari) cannot be told apart.
func privateWindow(in content: SCShareableContent) -> Bool {
    content.windows.contains { w in w.isOnScreen && w.windowLayer == 0 && privateMarks.contains { (w.title ?? "").contains($0) } }
}

func jpeg(_ image: CGImage, maxSide: Int, quality: Double) -> Data? {
    let scale = min(1.0, Double(maxSide) / Double(max(image.width, image.height)))
    let w = max(1, Int(Double(image.width) * scale)), h = max(1, Int(Double(image.height) * scale))
    guard let space = CGColorSpace(name: CGColorSpace.sRGB),
          let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: space,
                              bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
    ctx.interpolationQuality = .high
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    guard let out = ctx.makeImage() else { return nil }
    return NSBitmapImageRep(cgImage: out).representation(using: .jpeg, properties: [.compressionFactor: quality])
}

final class Shutter: NSObject, AVCapturePhotoCaptureDelegate {
    var done: ((Data?) -> Void)?
    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        done?(error == nil ? photo.fileDataRepresentation() : nil)
        done = nil
    }
}
let shutter = Shutter()

// One still. The camera's light is on for about two seconds: macOS shows it, and nothing here tries to hide it.
func takeFace(_ device: AVCaptureDevice) async -> Data? {
    let session = AVCaptureSession()
    if session.canSetSessionPreset(.vga640x480) { session.sessionPreset = .vga640x480 }
    guard let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input) else { return nil }
    session.addInput(input)
    let output = AVCapturePhotoOutput()
    guard session.canAddOutput(output) else { return nil }
    session.addOutput(output)
    session.startRunning()
    defer { session.stopRunning() }
    try? await Task.sleep(nanoseconds: 1_500_000_000)  // the first frames are dark until the exposure settles
    let raw: Data? = await withCheckedContinuation { c in
        shutter.done = { c.resume(returning: $0) }
        output.capturePhoto(with: AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg]), delegate: shutter)
    }
    guard let raw = raw, let image = NSBitmapImageRep(data: raw)?.cgImage else { return nil }
    return jpeg(image, maxSide: faceMax, quality: 0.7)
}

// The display the window in front is on; the main display when that cannot be told.
func display(for front: NSRunningApplication?, in content: SCShareableContent) -> SCDisplay? {
    if let pid = front?.processIdentifier,
       let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]],
       let top = list.first(where: { ($0[kCGWindowOwnerPID as String] as? Int32) == pid && ($0[kCGWindowLayer as String] as? Int) == 0 }),
       let b = top[kCGWindowBounds as String] as? NSDictionary, let r = CGRect(dictionaryRepresentation: b),
       let d = content.displays.first(where: { $0.frame.contains(CGPoint(x: r.midX, y: r.midY)) }) {
        return d
    }
    return content.displays.first { $0.displayID == CGMainDisplayID() } ?? content.displays.first
}

func shoot() async -> Never {
    if isLocked() { finish(["skipped": "locked"]) }
    let front = NSWorkspace.shared.frontmostApplication
    let frontInfo: [String: Any] = ["bundle": front?.bundleIdentifier ?? "", "name": front?.localizedName ?? ""]
    if skipped(front?.bundleIdentifier) { finish(["skipped": "app", "front": frontInfo]) }
    let who = callers()
    if !who.isEmpty { finish(["skipped": "call", "front": frontInfo, "by": who]) }
    guard CGPreflightScreenCaptureAccess() else { finish(["error": "screen permission", "permissions": permissions()], code: 1) }

    let content: SCShareableContent
    do { content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true) }
    catch { finish(["error": "screen list", "permissions": permissions()], code: 1) }
    if privateWindow(in: content) { finish(["skipped": "private", "front": frontInfo]) }
    guard let disp = display(for: front, in: content) else { finish(["error": "no display"], code: 1) }
    let hide = content.applications.filter { skipped($0.bundleIdentifier) }
    let hidden = Set(content.windows.filter { $0.isOnScreen && $0.windowLayer == 0 && skipped($0.owningApplication?.bundleIdentifier) }
        .compactMap { $0.owningApplication?.applicationName })
    let config = SCStreamConfiguration()
    let mode = CGDisplayCopyDisplayMode(disp.displayID)
    let pw = Double(mode?.pixelWidth ?? disp.width * 2), ph = Double(mode?.pixelHeight ?? disp.height * 2)
    let scale = min(1.0, Double(screenMax) / max(pw, ph))
    config.width = max(1, Int(pw * scale))
    config.height = max(1, Int(ph * scale))
    config.showsCursor = false
    let filter = SCContentFilter(display: disp, excludingApplications: hide, exceptingWindows: [])
    let screen: CGImage
    do { screen = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config) }
    catch { finish(["error": "screen capture", "permissions": permissions()], code: 1) }
    guard let screenData = jpeg(screen, maxSide: screenMax, quality: 0.6) else { finish(["error": "screen encode"], code: 1) }

    var result: [String: Any] = ["ok": true, "front": frontInfo, "screen": "screen.jpg", "hidden": hidden.sorted(), "permissions": permissions()]
    var faceData: Data?
    let cam = builtInCamera()
    if !wantFace { result["faceWhy"] = "off" }
    else if cameraState() != "allowed" { result["faceWhy"] = "permission" }
    else if cam == nil { result["faceWhy"] = "no camera" }  // lid closed, or a Mac without one
    else {
        faceData = await takeFace(cam!)
        if faceData == nil { result["faceWhy"] = "failed" }
    }
    // The screen may have changed while the pictures were taken (a call began, a private window opened, another app came
    // to the front): look at everything once more, and write nothing if any of it is true now.
    if isLocked() { finish(["skipped": "locked"]) }
    if skipped(NSWorkspace.shared.frontmostApplication?.bundleIdentifier) { finish(["skipped": "app", "front": frontInfo]) }
    let whoNow = callers()
    if !whoNow.isEmpty { finish(["skipped": "call", "front": frontInfo, "by": whoNow]) }
    guard let after = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true) else {
        finish(["error": "screen list", "permissions": permissions()], code: 1)
    }
    if privateWindow(in: after) { finish(["skipped": "private", "front": frontInfo]) }
    do {
        try screenData.write(to: folder.appendingPathComponent("screen.jpg"))
        if let f = faceData {
            try f.write(to: folder.appendingPathComponent("face.jpg"))
            result["face"] = "face.jpg"
        }
    } catch { finish(["error": "write"], code: 1) }
    finish(result)
}

func setup() async -> Never {
    if cameraState() == "ask" { _ = await AVCaptureDevice.requestAccess(for: .video) }
    if !CGPreflightScreenCaptureAccess() { _ = CGRequestScreenCaptureAccess() }  // macOS shows its prompt; the switch is in System Settings
    finish(permissions())
}

@main struct Main {
    static func main() async {
        guard args.count >= 3, ["status", "setup", "shoot"].contains(args[1]) else {
            FileHandle.standardError.write("usage: TatsukiGlance status|setup|shoot <folder> [--skip id,id] [--no-face] [--allow-mic]\n".data(using: .utf8)!)
            exit(2)
        }
        // only the Desk's own work folder: <…>/glance/.shot-<something>
        guard folder.lastPathComponent.hasPrefix(".shot-"), folder.deletingLastPathComponent().lastPathComponent == "glance" else {
            FileHandle.standardError.write("TatsukiGlance writes only into a .shot- folder inside a folder named glance\n".data(using: .utf8)!)
            exit(2)
        }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        // never hang: glance.py waits for this program, and a camera that does not answer must not hold it
        let limit: Double = args[1] == "setup" ? 110 : 25
        DispatchQueue.global().asyncAfter(deadline: .now() + limit) { finish(["error": "timeout", "permissions": permissions()], code: 1) }
        switch args[1] {
        case "status": finish(permissions().merging(["call": callers()]) { a, _ in a })  // "call": who is using a camera or microphone right now
        case "setup": await setup()
        default: await shoot()
        }
    }
}
