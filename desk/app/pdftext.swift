// Prints the text of a PDF, one page after another, pages apart with a form feed (\f). Built once by desk/reader.py:
//   swiftc -O -framework Cocoa -framework PDFKit pdftext.swift -o pdftext
// Exit 2: the PDF is locked with a password. Exit 1: it cannot be opened. Nothing is written anywhere.
//
// Some PDFs (Chrome / Skia prints, for example calibre's "print to PDF" of a Blinkist book) make PDFKit place every letter
// point-mirrored, outside the page. PDFKit then reads the page from the bottom right to the top left, and the whole page
// comes out backwards ("tsiknilB yb edaM"). When most letters sit outside the page that way, the page string is reversed,
// and the page starts with \u{01} so desk/reader.py can mend what reversing breaks (ligatures, lost spaces; see fix_mirrored).
import Foundation
import PDFKit

func mirrored(_ page: PDFPage, _ s: String) -> Bool {
    let ns = s as NSString
    let box = page.bounds(for: .mediaBox)
    var inside = 0, flipped = 0, i = 0
    let step = max(1, ns.length / 400)
    while i < ns.length {
        let ch = ns.character(at: i)
        if ch > 32 {
            let b = page.characterBounds(at: i)
            if b.width > 0 || b.height > 0 {
                let x = b.midX, y = b.midY
                if box.insetBy(dx: -2, dy: -2).contains(CGPoint(x: x, y: y)) { inside += 1 }
                else if x < box.minX && y < box.minY { flipped += 1 }
            }
        }
        i += step
    }
    return flipped > inside * 2 && flipped >= 5
}

let args = CommandLine.arguments
guard args.count > 1, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else { exit(1) }
if doc.isLocked { exit(2) }
var out = ""
for i in 0..<doc.pageCount {
    if let page = doc.page(at: i), let s = page.string {
        out += mirrored(page, s) ? "\u{01}" + String(s.reversed()) : s
    }
    out += "\u{0C}"
}
FileHandle.standardOutput.write(out.data(using: .utf8) ?? Data())
