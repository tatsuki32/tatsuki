// Tatsuki's Desk — records the other side of a meeting: the sound that Zoom, Meet (in a browser) or Teams plays on this Mac.
// desk/transcribe/server.py starts it with `open` so macOS asks for Screen Recording for this app alone, not for Python.
// Rebuilding changes its identity and loses that permission, so setup.sh builds it only once.
//
//   TatsukiAudioTap record <folder>
//     appends 16 kHz mono 16-bit PCM to <folder>/them.pcm and writes <folder>/tap.json about once a second
//     ({state, pid, started, level, apps, error}). `started` is the wall-clock time of the first sample in them.pcm.
//     Started again on the same folder, it appends and pads the gap with silence.
//     Stops on SIGTERM, when <folder>/stop appears, or when <folder>/alive has not been touched for 60 seconds.
// It takes no picture of the screen (the video it must ask for is 2x2 pixels and thrown away) and sends nothing anywhere.
import Foundation
import ScreenCaptureKit
import CoreMedia
import CoreGraphics

let rate = 16000.0
// Meeting apps and the browsers Meet runs in. Prefixes, so Chrome's helpers and web-app shims count too.
let meetingApps = ["us.zoom.", "com.google.Chrome", "com.brave.Browser", "com.apple.Safari", "com.microsoft.teams",
                   "com.microsoft.edgemac", "company.thebrowser.", "org.mozilla.firefox", "com.apple.FaceTime"]

guard CommandLine.arguments.count == 3, CommandLine.arguments[1] == "record" else {
    FileHandle.standardError.write("usage: TatsukiAudioTap record <folder>\n".data(using: .utf8)!)
    exit(2)
}
let folder = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
let pcmURL = folder.appendingPathComponent("them.pcm")
let statusURL = folder.appendingPathComponent("tap.json")
let stopURL = folder.appendingPathComponent("stop")
let aliveURL = folder.appendingPathComponent("alive")

final class Tap: NSObject, SCStreamOutput, SCStreamDelegate {
    var stream: SCStream?
    var file: FileHandle?
    var written: Int64 = 0          // samples in them.pcm
    var firstPTS: Double?
    var started: Double?
    var resumeFrom: Double?         // restarted mid-meeting: keep the first run's clock so the two sides stay in step
    var peak: Float = 0
    var state = "starting"
    var error: String?
    var apps: [String] = []
    let queue = DispatchQueue(label: "tap.audio")

    func status() {
        var obj: [String: Any] = ["state": state, "pid": Int(getpid()), "level": Double(peak), "apps": apps,
                                  "updated": Date().timeIntervalSince1970, "seconds": Double(written) / rate]
        if let s = started { obj["started"] = s }
        if let e = error { obj["error"] = e }
        peak = 0
        if let data = try? JSONSerialization.data(withJSONObject: obj) {
            let tmp = folder.appendingPathComponent("tap.json.tmp")
            try? data.write(to: tmp)
            _ = try? FileManager.default.replaceItemAt(statusURL, withItemAt: tmp)
        }
    }

    func fail(_ why: String) {
        state = "error"
        error = why
        status()
        exit(1)
    }

    func start() async {
        if !CGPreflightScreenCaptureAccess() {
            _ = CGRequestScreenCaptureAccess()  // shows macOS's own prompt once
            fail("permission")
        }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let display = content.displays.first else { return fail("No display found") }
            let chosen = content.applications.filter { app in meetingApps.contains { app.bundleIdentifier.hasPrefix($0) } }
            apps = Array(Set(chosen.map { $0.applicationName })).sorted()
            let filter = SCContentFilter(display: display, including: chosen, exceptingWindows: [])
            let config = SCStreamConfiguration()
            config.capturesAudio = true
            config.excludesCurrentProcessAudio = true
            config.sampleRate = Int(rate)
            config.channelCount = 1
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 10, timescale: 1)
            config.queueDepth = 3
            if FileManager.default.fileExists(atPath: pcmURL.path) {
                if let data = try? Data(contentsOf: statusURL),
                   let old = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                   let s = old["started"] as? Double {
                    resumeFrom = s
                    let bytes = (try? FileManager.default.attributesOfItem(atPath: pcmURL.path)[.size] as? Int64) ?? 0
                    written = bytes / 2
                }
            } else {
                FileManager.default.createFile(atPath: pcmURL.path, contents: nil)
            }
            file = try FileHandle(forWritingTo: pcmURL)
            file?.seekToEndOfFile()
            let s = SCStream(filter: filter, configuration: config, delegate: self)
            try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
            try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: DispatchQueue(label: "tap.video"))
            try await s.startCapture()
            stream = s
            state = "recording"
            status()
        } catch {
            fail(error.localizedDescription)
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, buffer.isValid, let file = file else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(buffer).seconds
        if firstPTS == nil {
            let now = Date().timeIntervalSince1970
            started = resumeFrom ?? now
            firstPTS = pts - (now - started!)
        }
        var list = AudioBufferList()
        var block: CMBlockBuffer?
        let ok = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            buffer, bufferListSizeNeededOut: nil, bufferListOut: &list, bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: &block)
        guard ok == noErr, let raw = list.mBuffers.mData else { return }
        let count = Int(list.mBuffers.mDataByteSize) / MemoryLayout<Float>.size
        let floats = raw.bindMemory(to: Float.self, capacity: count)
        // Keep the file in step with the clock: if macOS skipped a stretch (nothing playing), fill it with silence.
        let expected = Int64(((pts - firstPTS!) * rate).rounded())
        var out = Data()
        if expected > written + Int64(rate / 10) {
            out.append(Data(count: Int(expected - written) * 2))
            written = expected
        }
        out.reserveCapacity(out.count + count * 2)
        for i in 0..<count {
            let v = max(-1, min(1, floats[i]))
            peak = max(peak, abs(v))
            var s = Int16(v < 0 ? v * 32768 : v * 32767).littleEndian
            withUnsafeBytes(of: &s) { out.append(contentsOf: $0) }
        }
        written += Int64(count)
        file.write(out)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail(error.localizedDescription)
    }

    func finish() {
        let done = DispatchSemaphore(value: 0)
        if let s = stream {
            s.stopCapture { _ in done.signal() }
            _ = done.wait(timeout: .now() + 3)
        }
        queue.sync {}
        try? file?.synchronize()
        try? file?.close()
        state = "stopped"
        status()
        exit(0)
    }
}

let tap = Tap()
signal(SIGTERM, SIG_IGN)
let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
term.setEventHandler { tap.finish() }
term.resume()

Task { await tap.start() }

let launched = Date()
Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
    let fm = FileManager.default
    if fm.fileExists(atPath: stopURL.path) { tap.finish() }
    let alive = (try? fm.attributesOfItem(atPath: aliveURL.path)[.modificationDate] as? Date) ?? launched
    if Date().timeIntervalSince(max(alive, launched)) > 60 { tap.finish() }  // the recorder is gone: don't record forever
    if tap.state == "recording" { tap.queue.async { tap.status() } }
}
RunLoop.main.run()
