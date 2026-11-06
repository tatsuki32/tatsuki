// Tatsuki's Desk — makes a PDF smaller with macOS's own Quartz. Images are re-encoded; text stays text.
// Usage: pdfshrink <input> <output> <high|standard|small>. Exit: 2 usage, 3 protected, 4 unreadable, 5 changed content, 6 write failed.
import Foundation
import PDFKit
import Quartz

let args = CommandLine.arguments
let levels: [String: (quality: Double, dpi: Double)] = ["high": (0.8, 200), "standard": (0.6, 150), "small": (0.4, 100)]
guard args.count == 4, let level = levels[args[3]] else { exit(2) }

guard let source = PDFDocument(url: URL(fileURLWithPath: args[1])), source.pageCount > 0 else {
    // a password-protected file also opens as nil on some versions
    exit(4)
}
if source.isLocked || source.isEncrypted { exit(3) } // never drop someone's protection on the way

let scale: [String: Any] = ["ImageScaleFactor": level.dpi / 300, "ImageScaleInterpolate": true, "ImageSizeMax": Int(level.dpi * 11.7), "ImageSizeMin": 64]
let images: [String: Any] = ["Compression Quality": level.quality, "ImageCompression": "ImageJPEGCompress", "ImageScaleSettings": scale]
let filter = QuartzFilter(properties: [
    "Domains": ["Applications": true, "Printing": true], "FilterType": 1, "Name": "Desk",
    "FilterData": ["ColorSettings": ["ImageSettings": images]],
])
let out = URL(fileURLWithPath: args[2])
guard source.write(to: out, withOptions: [PDFDocumentWriteOption(rawValue: "QuartzFilter"): filter as Any]) else { exit(6) }

// the smaller file must say the same thing: same pages, same text, same links
func letters(_ d: PDFDocument) -> Int { (d.string ?? "").filter { !$0.isWhitespace }.count }
func marks(_ d: PDFDocument) -> Int { (0..<d.pageCount).reduce(0) { $0 + (d.page(at: $1)?.annotations.count ?? 0) } }
guard let result = PDFDocument(url: out), result.pageCount == source.pageCount,
      letters(result) == letters(source), marks(result) == marks(source) else {
    try? FileManager.default.removeItem(at: out)
    exit(5)
}
print("{\"pages\":\(source.pageCount)}")
