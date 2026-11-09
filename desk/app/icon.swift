// Draws the app icon: a gold "T" on black. Usage: swift icon.swift out.png
import Cocoa

let size = 1024
let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
    hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let inset = NSRect(x: 100, y: 100, width: 824, height: 824)
NSColor(red: 0.05, green: 0.05, blue: 0.05, alpha: 1).setFill()
NSBezierPath(roundedRect: inset, xRadius: 185, yRadius: 185).fill()
let gold = NSColor(red: 0.83, green: 0.69, blue: 0.22, alpha: 1)
gold.setFill()
NSBezierPath(rect: NSRect(x: 292, y: 660, width: 440, height: 84)).fill()
NSBezierPath(rect: NSRect(x: 470, y: 280, width: 84, height: 400)).fill()
NSGraphicsContext.current = nil
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
