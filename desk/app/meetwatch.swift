// Tatsuki's Desk — Transcribe's ears for auto-recording: which programs are using a microphone, and one recording of the mic.
// desk/transcribe/auto.py runs it. Rebuilding changes its identity and loses the Microphone permission, so setup.sh builds it only once.
//
//   TatsukiMeetWatch who
//     prints {ok, locked, users: [{bundle, name}]}: the programs listening to a microphone right now, other than this one.
//     Needs no permission: it hears nothing. macOS 14.2 or later; before that it answers {ok: false}.
//   TatsukiMeetWatch status <folder>   writes <folder>/mic-permission.json: {microphone: allowed|denied|ask}
//   TatsukiMeetWatch setup <folder>    lets macOS ask for the microphone once (its own prompt), then the same as status
//   TatsukiMeetWatch record <folder>
//     appends 16 kHz mono 16-bit PCM to <folder>/audio.pcm and writes <folder>/mic.json about once a second
//     ({state, pid, started, level, error}). `started` is the wall-clock time of the first sample in audio.pcm.
//     Started again on the same folder, it appends and pads the gap with silence. If the microphone is unplugged it goes on
//     with the Mac's next one. Stops on SIGTERM, when <folder>/mic-stop appears, when <folder>/mic-alive has not been
//     touched for 60 seconds, and after 4 hours 10 minutes whatever happens.
//
// What it refuses, whoever starts it: <folder> must already exist and be "<…>/transcribe<…>/sessions/<32 hex>" for record,
// "<…>/transcribe<…>" for status and setup, and it only ever writes the file names above. It sends nothing anywhere.
// What it cannot do: tell apart the programs of this Mac's own user. Any of them could start it, as with every app that
// holds a permission; macOS shows its orange dot whenever it listens.
import AppKit
import AVFoundation
import CoreAudio

let rate = 16000.0
let args = CommandLine.arguments
let fm = FileManager.default

func usage() -> Never {
    FileHandle.standardError.write("usage: TatsukiMeetWatch who | status <folder> | setup <folder> | record <folder>\n".data(using: .utf8)!)
    exit(2)
}

func writeJSON(_ obj: [String: Any], _ url: URL) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) else { return }
    try? data.write(to: url, options: .atomic)
}

// ---------- who is using a microphone ----------

func audioNumber(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32? {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var value = UInt32(0), size = UInt32(MemoryLayout<UInt32>.size)
    return AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &value) == noErr ? value : nil
}

func audioText(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    let ok = withUnsafeMutablePointer(to: &value) { AudioObjectGetPropertyData(id, &addr, 0, nil, &size, $0) }
    return ok == noErr ? value?.takeRetainedValue() as String? : nil
}

func microphoneUsers() -> [[String: String]]? {
    guard #available(macOS 14.2, *) else { return nil }
    let system = AudioObjectID(kAudioObjectSystemObject)
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(0)
    guard AudioObjectGetPropertyDataSize(system, &addr, 0, nil, &size) == noErr else { return nil }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    if ids.isEmpty { return [] }
    guard AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &ids) == noErr else { return nil }
    var users: [[String: String]] = []
    for p in ids {
        // a program can end between the list and the question: then it is not listening
        guard let listening = audioNumber(p, kAudioProcessPropertyIsRunningInput), listening != 0 else { continue }
        let pid = pid_t(bitPattern: audioNumber(p, kAudioProcessPropertyPID) ?? 0)
        let bundle = audioText(p, kAudioProcessPropertyBundleID) ?? ""
        if pid == getpid() || bundle == "com.tatsuki.desk.meetwatch" { continue }
        users.append(["bundle": bundle, "name": NSRunningApplication(processIdentifier: pid)?.localizedName ?? ""])
    }
    return users
}

func isLocked() -> Bool {
    guard let s = CGSessionCopyCurrentDictionary() as? [String: Any] else { return true }
    return (s["CGSSessionScreenIsLocked"] as? Bool ?? false) || (s[kCGSessionOnConsoleKey as String] as? Bool == false)
}

// ---------- the recording ----------

final class Mic: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    let folder: URL
    var session: AVCaptureSession?
    var file: FileHandle?
    var written: Int64 = 0
    var firstPTS: Double?
    var started: Double?
    var resumeFrom: Double?
    var peak: Float = 0
    var state = "starting"
    var error: String?
    let queue = DispatchQueue(label: "mic.audio")

    init(_ folder: URL) { self.folder = folder }

    func url(_ name: String) -> URL { folder.appendingPathComponent(name) }

    func status() {
        var obj: [String: Any] = ["state": state, "pid": Int(getpid()), "level": Double(peak),
                                  "updated": Date().timeIntervalSince1970, "seconds": Double(written) / rate]
        if let s = started { obj["started"] = s }
        if let e = error { obj["error"] = e }
        peak = 0
        writeJSON(obj, url("mic.json"))
    }

    func fail(_ why: String) -> Never {
        state = "error"
        error = why
        status()
        exit(1)
    }

    func open() {
        let pcm = url("audio.pcm")
        if fm.fileExists(atPath: pcm.path) {
            var bytes = (try? fm.attributesOfItem(atPath: pcm.path)[.size] as? Int64) ?? 0
            if bytes > 0, let data = try? Data(contentsOf: url("mic.json")),
               let old = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], let s = old["started"] as? Double {
                resumeFrom = s
            }
            guard let f = try? FileHandle(forWritingTo: pcm) else { fail("The recording file could not be opened") }
            if bytes % 2 != 0 { bytes -= 1; try? f.truncate(atOffset: UInt64(bytes)) }  // cut off mid-sample last time
            written = bytes / 2
            f.seekToEndOfFile()
            file = f
        } else {
            fm.createFile(atPath: pcm.path, contents: nil, attributes: [.posixPermissions: 0o600])
            file = try? FileHandle(forWritingTo: pcm)
        }
        if file == nil { fail("The recording file could not be opened") }
    }

    func start() {
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else { fail("permission") }
        open()
        guard connect() else { fail("No microphone found") }
        NotificationCenter.default.addObserver(forName: .AVCaptureDeviceWasDisconnected, object: nil, queue: .main) { _ in self.reconnect() }
        NotificationCenter.default.addObserver(forName: .AVCaptureSessionRuntimeError, object: nil, queue: .main) { _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.reconnect() }
        }
        queue.async { self.status() }
    }

    func connect() -> Bool {
        guard let device = AVCaptureDevice.default(for: .audio), let input = try? AVCaptureDeviceInput(device: device) else { return false }
        let s = AVCaptureSession()
        let out = AVCaptureAudioDataOutput()
        out.audioSettings = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: rate, AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
                             AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsNonInterleaved: false]
        out.setSampleBufferDelegate(self, queue: queue)
        guard s.canAddInput(input), s.canAddOutput(out) else { return false }
        s.addInput(input)
        s.addOutput(out)
        s.startRunning()
        session = s
        return true
    }

    func reconnect() {
        guard state != "stopped" else { return }
        session?.stopRunning()
        session = nil
        if !connect() {  // nothing to listen with: keep looking until told to stop
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) { self.reconnect() }
        }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput buffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard buffer.isValid, let file = file, let block = CMSampleBufferGetDataBuffer(buffer) else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(buffer).seconds
        if firstPTS == nil {
            let now = Date().timeIntervalSince1970
            started = resumeFrom ?? now
            firstPTS = pts - (now - started!)
            state = "recording"
        }
        let bytes = CMBlockBufferGetDataLength(block) & ~1
        var samples = Data(count: bytes)
        let ok = samples.withUnsafeMutableBytes { CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: bytes, destination: $0.baseAddress!) }
        guard bytes > 0, ok == noErr else { return }
        // Keep the file in step with the clock, so this side and the call's sound stay together.
        let expected = Int64(((pts - firstPTS!) * rate).rounded())
        if expected > written + Int64(rate / 10) {
            var gap = expected - written
            while gap > 0 {
                let n = min(gap, Int64(rate) * 10)
                file.write(Data(count: Int(n) * 2))
                gap -= n
            }
            written = expected
        }
        samples.withUnsafeBytes { raw in
            for v in raw.bindMemory(to: Int16.self) { peak = max(peak, abs(Float(v)) / 32768) }
        }
        file.write(samples)
        written += Int64(bytes / 2)
    }

    func finish() -> Never {
        session?.stopRunning()
        queue.sync {}
        try? file?.synchronize()
        try? file?.close()
        state = "stopped"
        status()
        exit(0)
    }
}

// ---------- main ----------

guard args.count >= 2 else { usage() }
if args[1] == "who" {
    let users = microphoneUsers()
    let obj: [String: Any] = ["ok": users != nil, "locked": isLocked(), "users": users ?? []]
    FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]))
    exit(0)
}
guard args.count == 3, ["status", "setup", "record"].contains(args[1]) else { usage() }
let folder = URL(fileURLWithPath: args[2], isDirectory: true).resolvingSymlinksInPath().standardizedFileURL
var isDir: ObjCBool = false
guard fm.fileExists(atPath: folder.path, isDirectory: &isDir), isDir.boolValue else { usage() }
let parts = folder.pathComponents

if args[1] != "record" {
    guard parts.last?.hasPrefix("transcribe") == true else { usage() }
    func answer() -> Never {
        let word: String
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: word = "allowed"
        case .denied, .restricted: word = "denied"
        default: word = "ask"
        }
        writeJSON(["microphone": word, "at": Date().timeIntervalSince1970], folder.appendingPathComponent("mic-permission.json"))
        exit(0)
    }
    if args[1] == "status" { answer() }
    NSApplication.shared.setActivationPolicy(.accessory)
    NSApplication.shared.activate(ignoringOtherApps: true)
    AVCaptureDevice.requestAccess(for: .audio) { _ in answer() }
    RunLoop.main.run()
}

guard parts.count >= 3, parts[parts.count - 2] == "sessions", parts[parts.count - 3].hasPrefix("transcribe"),
      parts.last!.range(of: "^[0-9a-f]{32}$", options: .regularExpression) != nil else { usage() }
let mic = Mic(folder)
signal(SIGTERM, SIG_IGN)
let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
term.setEventHandler { mic.finish() }
term.resume()
mic.start()
let launched = Date()
Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
    if fm.fileExists(atPath: mic.url("mic-stop").path) || !fm.fileExists(atPath: folder.path) { mic.finish() }
    let alive = (try? fm.attributesOfItem(atPath: mic.url("mic-alive").path)[.modificationDate] as? Date) ?? launched
    if Date().timeIntervalSince(max(alive, launched)) > 60 { mic.finish() }  // the recorder is gone: don't record forever
    if Date().timeIntervalSince(launched) > 15000 { mic.finish() }
    mic.queue.async { mic.status() }
}
RunLoop.main.run()
