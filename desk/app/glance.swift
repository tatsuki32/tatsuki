// Tatsuki's Desk — Glance's eyes: one photo from the built-in camera and one picture of the screen, when asked.
// desk/glance.py starts it with `open` so macOS asks for Camera and Screen Recording for this app alone, not for Python.
// Rebuilding changes its identity and loses those permissions, so glance.py builds it only once.
//
//   TatsukiGlance status <folder>     writes <folder>/result.json: {camera: allowed|denied|ask, screen: true|false}
//   TatsukiGlance setup <folder>      lets macOS ask for both permissions (its own prompts), then the same as status
//   TatsukiGlance shoot <folder> [--skip id,id] [--no-face] [--face 640] [--screen 1568]
//     writes <folder>/screen.jpg, <folder>/face.jpg (when there is one) and <folder>/result.json
//
// It takes nothing while the screen is locked, while one of the --skip apps is in front, while the window in front is a
// private browser window, or while another app is using the camera (a call: other people would be in the picture).
// Windows of the --skip apps are left out of the picture even when they sit behind. Pictures are made small before they
// are written. It sends nothing anywhere and keeps nothing: the folder belongs to glance.py.
import AppKit
import AVFoundation
import ScreenCaptureKit

let args = CommandLine.arguments
let folder = URL(fileURLWithPath: args[2], isDirectory: true)  // read only after main() has checked the arguments

func option(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}
let skip = (option("--skip") ?? "").split(separator: ",").map(String.init).filter { !$0.isEmpty }
let wantFace = !args.contains("--no-face")
let faceMax = Int(option("--face") ?? "") ?? 640
let screenMax = Int(option("--screen") ?? "") ?? 1568
let privateMarks = ["Incognito", "シークレット", "Private Browsing", "プライベートブラウズ", "InPrivate"]

func finish(_ value: [String: Any], code: Int32 = 0) -> Never {
    // written last and read by glance.py only after this program has ended, so a plain write is enough
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
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
    let cam = builtInCamera()
    if cam?.isInUseByAnotherApplication == true { finish(["skipped": "call", "front": frontInfo]) }
    guard CGPreflightScreenCaptureAccess() else { finish(["error": "screen permission", "permissions": permissions()], code: 1) }

    let content: SCShareableContent
    do { content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true) }
    catch { finish(["error": "screen list", "permissions": permissions()], code: 1) }
    let frontWindows = content.windows.filter { $0.owningApplication?.processID == front?.processIdentifier && $0.windowLayer == 0 && $0.isOnScreen }
    if frontWindows.contains(where: { w in privateMarks.contains { (w.title ?? "").contains($0) } }) {
        finish(["skipped": "private", "front": frontInfo])
    }
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
    if !wantFace { result["faceWhy"] = "off" }
    else if cameraState() != "allowed" { result["faceWhy"] = "permission" }
    else if cam == nil { result["faceWhy"] = "no camera" }  // lid closed, or a Mac without one
    else {
        faceData = await takeFace(cam!)
        if faceData == nil { result["faceWhy"] = "failed" }
    }
    // the app in front may have changed while the camera warmed up: look once more before anything is written
    if skipped(NSWorkspace.shared.frontmostApplication?.bundleIdentifier) || isLocked() { finish(["skipped": "app", "front": frontInfo]) }
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
            FileHandle.standardError.write("usage: TatsukiGlance status|setup|shoot <folder> [--skip id,id] [--no-face]\n".data(using: .utf8)!)
            exit(2)
        }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        // never hang: glance.py waits for this program, and a camera that does not answer must not hold it
        let limit: Double = args[1] == "setup" ? 110 : 25
        DispatchQueue.global().asyncAfter(deadline: .now() + limit) { finish(["error": "timeout", "permissions": permissions()], code: 1) }
        switch args[1] {
        case "status": finish(permissions())
        case "setup": await setup()
        default: await shoot()
        }
    }
}
