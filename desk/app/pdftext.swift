// Prints the text of a PDF, one page after another, pages apart with a form feed (\f). Built once by desk/reader.py:
//   swiftc -O -framework Cocoa -framework PDFKit pdftext.swift -o pdftext
// Exit 2: the PDF is locked with a password. Exit 1: it cannot be opened. Nothing is written anywhere.
import Foundation
import PDFKit

let args = CommandLine.arguments
guard args.count > 1, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else { exit(1) }
if doc.isLocked { exit(2) }
var out = ""
for i in 0..<doc.pageCount {
    if let page = doc.page(at: i), let s = page.string { out += s }
    out += "\u{0C}"
}
FileHandle.standardOutput.write(out.data(using: .utf8) ?? Data())
