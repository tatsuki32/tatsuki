// Tatsuki's Desk — the pictures for Post Studio, made on this Mac with Vision, ImageIO and AppKit. Nothing is sent anywhere.
// Usage:
//   postcrop focus <image>                       prints {"w":..,"h":..,"x":..,"y":..} — the middle of what stands out (0..1, top-left origin)
//   postcrop crop <image> <out.jpg> <W> <H> <x> <y> [text]
//                                                cuts the largest W:H frame around (x, y), scales it to W×H, and, when text is given,
//                                                writes it in the lower safe zone on a soft dark band (for stories)
//   postcrop clip-html <file.html> <file.txt>    puts formatted text on the clipboard, with the plain text beside it (for note)
// Exit: 2 usage, 4 unreadable, 6 write failed.
import AppKit
import Foundation
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

func crop(_ img: CGImage, w: Int, h: Int, x: Double, y: Double, text: String?) -> CGImage? {
    let iw = Double(img.width), ih = Double(img.height), want = Double(w) / Double(h)
    var cw = iw, ch = iw / want
    if ch > ih { ch = ih; cw = ih * want }
    let cx = min(max(x * iw - cw / 2, 0), iw - cw), cy = min(max(y * ih - ch / 2, 0), ih - ch)
    guard let part = img.cropping(to: CGRect(x: cx.rounded(), y: cy.rounded(), width: cw.rounded(), height: ch.rounded())),
          let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
    ctx.interpolationQuality = .high
    ctx.draw(part, in: CGRect(x: 0, y: 0, width: w, height: h))
    if let text = text, !text.isEmpty {
        // stories cover the top 14% and the bottom 20% with buttons, so the words sit above that
        let band = CGRect(x: 0, y: Double(h) * 0.20, width: Double(w), height: Double(h) * 0.26)
        let fade = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB)!,
                              colors: [CGColor(gray: 0, alpha: 0), CGColor(gray: 0, alpha: 0.62)] as CFArray, locations: [0, 1])!
        ctx.drawLinearGradient(fade, start: CGPoint(x: 0, y: band.maxY), end: CGPoint(x: 0, y: band.minY + band.height * 0.35), options: [.drawsAfterEndLocation])
        let g = NSGraphicsContext(cgContext: ctx, flipped: false)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = g
        let para = NSMutableParagraphStyle()
        para.alignment = .left
        para.lineBreakMode = .byWordWrapping
        para.lineSpacing = 6
        var size = Double(w) * 0.062
        let box = CGRect(x: Double(w) * 0.07, y: band.minY + 24, width: Double(w) * 0.86, height: band.height - 48)
        var attr: [NSAttributedString.Key: Any] = [:]
        repeat { // smaller until it fits the band
            attr = [.font: NSFont.systemFont(ofSize: size, weight: .bold), .foregroundColor: NSColor.white, .paragraphStyle: para]
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

switch args[1] {
case "focus":
    guard args.count == 3 else { exit(2) }
    guard let img = load(args[2]) else { exit(4) }
    let (x, y) = focus(img)
    print(String(format: "{\"w\":%d,\"h\":%d,\"x\":%.4f,\"y\":%.4f}", img.width, img.height, x, y))
case "crop":
    guard args.count >= 8, let w = Int(args[4]), let h = Int(args[5]), let x = Double(args[6]), let y = Double(args[7]), w > 0, h > 0 else { exit(2) }
    guard let img = load(args[2]) else { exit(4) }
    guard let out = crop(img, w: w, h: h, x: x, y: y, text: args.count > 8 ? args[8] : nil),
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
