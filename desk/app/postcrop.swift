// Tatsuki's Desk — the pictures for Post Studio, made on this Mac with Vision, ImageIO and AppKit. Nothing is sent anywhere.
// Usage:
//   postcrop focus <image>                       prints {"w":..,"h":..,"x":..,"y":..} — the middle of what stands out (0..1, top-left origin)
//   postcrop crop <image> <out.jpg> <W> <H> <x> <y> [text]
//                                                cuts the largest W:H frame around (x, y), scales it to W×H, and, when text is given,
//                                                writes it in the lower safe zone on a soft dark band (for stories)
//   postcrop clip-html <file.html> <file.txt>    puts formatted text on the clipboard, with the plain text beside it (for note)
//   postcrop pdf <in.pdf> <outdir> <max>         each page (up to max) as outdir/page-N.jpg on white, the text as outdir/text.txt;
//                                                prints {"pages":n,"total":N}
//   postcrop video-info <in>                     prints {"w":..,"h":..,"sec":..} (upright size)
//   postcrop video-frames <in> <outdir> <n>      n frames spread over the video, outdir/frame-N.jpg (for the drafts and the focus)
//   postcrop video-crop <in> <out.mp4> <W> <H> <x> <y> [maxSec]
//                                                the largest W:H frame around (x, y), scaled to W×H, H.264 with its sound
// Exit: 2 usage, 4 unreadable, 5 locked PDF, 6 write failed.
import AppKit
import AVFoundation
import Foundation
import PDFKit
import ImageIO
import UniformTypeIdentifiers
import Vision

let args = CommandLine.arguments
guard args.count >= 2 else { exit(2) }

func load(_ path: String) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else { return nil }
    // the camera's own rotation is applied, so a phone photo is upright
    let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceCreateThumbnailWithTransform: true,
                                 kCGImageSourceThumbnailMaxPixelSize: 4096]
    return CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary)
}

func focus(_ img: CGImage) -> (Double, Double) {
    // faces first (a poster session photo is about the people), then what the eye goes to
    var boxes: [CGRect] = []
    let faces = VNDetectFaceRectanglesRequest()
    let salient = VNGenerateAttentionBasedSaliencyImageRequest()
    let handler = VNImageRequestHandler(cgImage: img, options: [:])
    try? handler.perform([faces, salient])
    boxes = (faces.results ?? []).map { $0.boundingBox }
    if boxes.isEmpty, let obs = salient.results?.first as? VNSaliencyImageObservation {
        boxes = (obs.salientObjects ?? []).map { $0.boundingBox }
    }
    guard let first = boxes.first else { return (0.5, 0.45) }
    let all = boxes.dropFirst().reduce(first) { $0.union($1) }
    // Vision's origin is bottom-left
    return (Double(all.midX), Double(1 - all.midY))
}

// taken with a camera (its maker is in the file): a photo, which fills the frame. Slides, screenshots and PDF pages have none
func fromCamera(_ path: String) -> Bool {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any],
          let tiff = props[kCGImagePropertyTIFFDictionary] as? [CFString: Any] else { return false }
    return !((tiff[kCGImagePropertyTIFFMake] as? String) ?? "").isEmpty
}

// the average colour of the picture's outer edge, to fill the space around a whole picture
func edgeColor(_ img: CGImage) -> CGColor {
    let iw = img.width, ih = img.height, b = max(2, min(iw, ih) / 40)
    var r = 0.0, g = 0.0, bl = 0.0, n = 0.0
    for rect in [CGRect(x: 0, y: 0, width: iw, height: b), CGRect(x: 0, y: ih - b, width: iw, height: b),
                 CGRect(x: 0, y: 0, width: b, height: ih), CGRect(x: iw - b, y: 0, width: b, height: ih)] {
        guard let part = img.cropping(to: rect) else { continue }
        var px = [UInt8](repeating: 0, count: 4)
        guard let c = CGContext(data: &px, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
                                space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { continue }
        c.interpolationQuality = .medium
        c.draw(part, in: CGRect(x: 0, y: 0, width: 1, height: 1))
        r += Double(px[0]); g += Double(px[1]); bl += Double(px[2]); n += 1
    }
    if n == 0 { return CGColor(gray: 0.1, alpha: 1) }
    return CGColor(srgbRed: r / n / 255, green: g / n / 255, blue: bl / n / 255, alpha: 1)
}

func crop(_ img: CGImage, w: Int, h: Int, x: Double, y: Double, text: String?, fit: Bool = false) -> CGImage? {
    let iw = Double(img.width), ih = Double(img.height), want = Double(w) / Double(h)
    guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
    ctx.interpolationQuality = .high
    var bg: CGColor? = nil
    if fit {
        // the whole picture, nothing cut: it sits in the frame and the edge colour fills the rest
        bg = edgeColor(img)
        ctx.setFillColor(bg!)
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        // with story words, the picture stays above them and below the top 14% the app covers
        let room = (text ?? "").isEmpty ? CGRect(x: 0, y: 0, width: Double(w), height: Double(h))
                                        : CGRect(x: Double(w) * 0.03, y: Double(h) * 0.47, width: Double(w) * 0.94, height: Double(h) * 0.39)
        let s = min(room.width / iw, room.height / ih), dw = iw * s, dh = ih * s
        ctx.draw(img, in: CGRect(x: (room.midX - dw / 2).rounded(), y: (room.midY - dh / 2).rounded(), width: dw.rounded(), height: dh.rounded()))
    } else {
        var cw = iw, ch = iw / want
        if ch > ih { ch = ih; cw = ih * want }
        let cx = min(max(x * iw - cw / 2, 0), iw - cw), cy = min(max(y * ih - ch / 2, 0), ih - ch)
        guard let part = img.cropping(to: CGRect(x: cx.rounded(), y: cy.rounded(), width: cw.rounded(), height: ch.rounded())) else { return nil }
        ctx.draw(part, in: CGRect(x: 0, y: 0, width: w, height: h))
    }
    if let text = text, !text.isEmpty {
        // stories cover the top 14% and the bottom 20% with buttons, so the words sit above that
        let band = CGRect(x: 0, y: Double(h) * 0.20, width: Double(w), height: Double(h) * 0.26)
        let fade = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB)!,
                              colors: [CGColor(gray: 0, alpha: 0), CGColor(gray: 0, alpha: 0.62)] as CFArray, locations: [0, 1])!
        if bg == nil { ctx.drawLinearGradient(fade, start: CGPoint(x: 0, y: band.maxY), end: CGPoint(x: 0, y: band.minY + band.height * 0.35), options: [.drawsAfterEndLocation]) }
        let g = NSGraphicsContext(cgContext: ctx, flipped: false)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = g
        let para = NSMutableParagraphStyle()
        para.alignment = .left
        para.lineBreakMode = .byWordWrapping
        para.lineSpacing = 6
        var size = Double(w) * 0.062
        let box = CGRect(x: Double(w) * 0.07, y: band.minY + 24, width: Double(w) * 0.86, height: band.height - 48)
        // white on the dark fade; on a plain edge colour, black or white, whichever reads
        var ink = NSColor.white
        if let c = bg?.converted(to: CGColorSpace(name: CGColorSpace.sRGB)!, intent: .defaultIntent, options: nil)?.components, c.count >= 3,
           0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 0.55 { ink = NSColor(white: 0.08, alpha: 1) }
        var attr: [NSAttributedString.Key: Any] = [:]
        repeat { // smaller until it fits the band
            attr = [.font: NSFont.systemFont(ofSize: size, weight: .bold), .foregroundColor: ink, .paragraphStyle: para]
            let need = (text as NSString).boundingRect(with: box.size, options: [.usesLineFragmentOrigin], attributes: attr)
            if need.height <= box.height { break }
            size -= 4
        } while size > 28
        let need = (text as NSString).boundingRect(with: box.size, options: [.usesLineFragmentOrigin], attributes: attr)
        (text as NSString).draw(with: CGRect(x: box.minX, y: box.minY, width: box.width, height: ceil(need.height)), options: [.usesLineFragmentOrigin], attributes: attr)
        NSGraphicsContext.restoreGraphicsState()
    }
    return ctx.makeImage()
}

func writeJPEG(_ img: CGImage, _ path: String, _ q: Double = 0.9) -> Bool {
    guard let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else { return false }
    CGImageDestinationAddImage(dest, img, [kCGImageDestinationLossyCompressionQuality: q] as CFDictionary)
    return CGImageDestinationFinalize(dest)
}

func pdfPages(_ path: String, _ outdir: String, _ maxPages: Int) {
    guard let doc = PDFDocument(url: URL(fileURLWithPath: path)) else { exit(4) }
    if doc.isLocked { exit(5) }
    try? (doc.string ?? "").write(toFile: outdir + "/text.txt", atomically: true, encoding: .utf8)
    let n = min(doc.pageCount, maxPages)
    for i in 0..<n {
        guard let page = doc.page(at: i) else { continue }
        let box = page.bounds(for: .mediaBox)
        let rot = page.rotation % 180 != 0
        let pw = rot ? box.height : box.width, ph = rot ? box.width : box.height
        let scale = 2160.0 / max(pw, ph)
        let w = Int(pw * scale), h = Int(ph * scale)
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { exit(6) }
        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        ctx.scaleBy(x: scale, y: scale)
        page.draw(with: .mediaBox, to: ctx)
        guard let img = ctx.makeImage(), writeJPEG(img, outdir + "/page-\(i).jpg") else { exit(6) }
    }
    print("{\"pages\":\(n),\"total\":\(doc.pageCount)}")
}

func upright(_ track: AVAssetTrack) -> (CGAffineTransform, CGSize) {
    let r = CGRect(origin: .zero, size: track.naturalSize).applying(track.preferredTransform)
    return (track.preferredTransform.concatenating(CGAffineTransform(translationX: -r.minX, y: -r.minY)), CGSize(width: abs(r.width), height: abs(r.height)))
}

func videoTrack(_ path: String) -> (AVURLAsset, AVAssetTrack) {
    let asset = AVURLAsset(url: URL(fileURLWithPath: path))
    guard let track = asset.tracks(withMediaType: .video).first else { exit(4) }
    return (asset, track)
}

switch args[1] {
case "pdf":
    guard args.count == 5, let m = Int(args[4]) else { exit(2) }
    pdfPages(args[2], args[3], m)
case "video-info":
    guard args.count == 3 else { exit(2) }
    let (asset, track) = videoTrack(args[2])
    let (_, size) = upright(track)
    print(String(format: "{\"w\":%d,\"h\":%d,\"sec\":%.2f}", Int(size.width), Int(size.height), CMTimeGetSeconds(asset.duration)))
case "video-frames":
    guard args.count == 5, let n = Int(args[4]), n > 0 else { exit(2) }
    let (asset, _) = videoTrack(args[2])
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true
    gen.maximumSize = CGSize(width: 1600, height: 1600)
    gen.requestedTimeToleranceBefore = CMTime(seconds: 0.5, preferredTimescale: 600)
    gen.requestedTimeToleranceAfter = CMTime(seconds: 0.5, preferredTimescale: 600)
    let sec = CMTimeGetSeconds(asset.duration)
    for i in 0..<n {
        let t = CMTime(seconds: sec * (Double(i) + 0.5) / Double(n), preferredTimescale: 600)
        guard let img = try? gen.copyCGImage(at: t, actualTime: nil), writeJPEG(img, args[3] + "/frame-\(i).jpg", 0.85) else { exit(6) }
    }
case "video-crop":
    guard args.count >= 8, let w = Double(args[4]), let h = Double(args[5]), let x = Double(args[6]), let y = Double(args[7]), w > 0, h > 0 else { exit(2) }
    let (asset, track) = videoTrack(args[2])
    let (t0, size) = upright(track)
    let s = max(w / size.width, h / size.height)
    let ox = min(max(x * size.width * s - w / 2, 0), size.width * s - w)
    let oy = min(max(y * size.height * s - h / 2, 0), size.height * s - h)
    var range = CMTimeRange(start: .zero, duration: asset.duration)
    if args.count > 8, let m = Double(args[8]), m > 0, CMTimeGetSeconds(asset.duration) > m { range = CMTimeRange(start: .zero, duration: CMTime(seconds: m, preferredTimescale: 600)) }
    let layer = AVMutableVideoCompositionLayerInstruction(assetTrack: track)
    layer.setTransform(t0.concatenating(CGAffineTransform(scaleX: s, y: s)).concatenating(CGAffineTransform(translationX: -ox, y: -oy)), at: .zero)
    let ins = AVMutableVideoCompositionInstruction()
    ins.timeRange = range
    ins.layerInstructions = [layer]
    let comp = AVMutableVideoComposition()
    comp.renderSize = CGSize(width: w, height: h)
    let fps = track.nominalFrameRate > 0 ? min(track.nominalFrameRate, 60) : 30
    comp.frameDuration = CMTime(value: 1, timescale: CMTimeScale(fps.rounded()))
    comp.instructions = [ins]
    try? FileManager.default.removeItem(atPath: args[3])
    guard let ex = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetHighestQuality) else { exit(6) }
    ex.outputURL = URL(fileURLWithPath: args[3])
    ex.outputFileType = .mp4
    ex.videoComposition = comp
    ex.timeRange = range
    ex.shouldOptimizeForNetworkUse = true
    let done = DispatchSemaphore(value: 0)
    ex.exportAsynchronously { done.signal() }
    done.wait()
    if ex.status != .completed { FileHandle.standardError.write((ex.error?.localizedDescription ?? "export failed").data(using: .utf8)!); exit(6) }
case "focus":
    guard args.count == 3 else { exit(2) }
    guard let img = load(args[2]) else { exit(4) }
    let (x, y) = focus(img)
    print(String(format: "{\"w\":%d,\"h\":%d,\"x\":%.4f,\"y\":%.4f,\"cam\":%@}", img.width, img.height, x, y, fromCamera(args[2]) ? "true" : "false"))
case "crop":
    guard args.count >= 8, let w = Int(args[4]), let h = Int(args[5]), let x = Double(args[6]), let y = Double(args[7]), w > 0, h > 0 else { exit(2) }
    guard let img = load(args[2]) else { exit(4) }
    // args[8]: the story's words ("" for none), args[9]: "fit" keeps the whole picture
    guard let out = crop(img, w: w, h: h, x: x, y: y, text: args.count > 8 ? args[8] : nil, fit: args.count > 9 && args[9] == "fit"),
          let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: args[3]) as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else { exit(6) }
    CGImageDestinationAddImage(dest, out, [kCGImageDestinationLossyCompressionQuality: 0.9] as CFDictionary)
    guard CGImageDestinationFinalize(dest) else { exit(6) }
case "clip-html":
    guard args.count == 4, let html = try? String(contentsOfFile: args[2], encoding: .utf8),
          let plain = try? String(contentsOfFile: args[3], encoding: .utf8) else { exit(2) }
    let pb = NSPasteboard.general
    pb.clearContents()
    pb.setString(html, forType: .html)
    pb.setString(plain, forType: .string)
default:
    exit(2)
}
