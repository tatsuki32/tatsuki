// Draws the Cards app icon: two stacked cards, gold on charcoal. Usage: swift icon.swift out.png [size]
import Cocoa

let size = CommandLine.arguments.count > 2 ? Int(CommandLine.arguments[2]) ?? 512 : 512
let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
    hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let scale = NSAffineTransform(); scale.scale(by: CGFloat(size) / 512); scale.concat()
NSColor(red: 0.11, green: 0.114, blue: 0.129, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: 512, height: 512), xRadius: 112, yRadius: 112).fill()
let gold = NSColor(red: 0.886, green: 0.722, blue: 0.4, alpha: 1)
let back = NSBezierPath(roundedRect: NSRect(x: 150, y: 120, width: 240, height: 300), xRadius: 28, yRadius: 28)
let t = NSAffineTransform(); t.translateX(by: 270, yBy: 270); t.rotate(byDegrees: -12); t.translateX(by: -270, yBy: -270)
back.transform(using: t as AffineTransform)
NSColor(red: 0.886, green: 0.722, blue: 0.4, alpha: 0.45).setFill(); back.fill()
gold.setFill()
NSBezierPath(roundedRect: NSRect(x: 150, y: 100, width: 240, height: 300), xRadius: 28, yRadius: 28).fill()
NSColor(red: 0.11, green: 0.114, blue: 0.129, alpha: 1).setFill()
NSBezierPath(rect: NSRect(x: 190, y: 230, width: 160, height: 22)).fill()
NSGraphicsContext.current = nil
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
