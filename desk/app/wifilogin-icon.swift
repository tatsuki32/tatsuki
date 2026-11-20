// Draws the Wi-Fi Login icon: gold Wi-Fi arcs on black, the same tile as the Desk icon. Usage: swift wifilogin-icon.swift out.png
import Cocoa

let size = 1024
let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
    hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSColor(red: 0.05, green: 0.05, blue: 0.05, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 185, yRadius: 185).fill()
let gold = NSColor(red: 0.83, green: 0.69, blue: 0.22, alpha: 1)
gold.setStroke()
gold.setFill()
let foot = NSPoint(x: 512, y: 330)
for radius in [150, 270, 390] as [CGFloat] {
    let arc = NSBezierPath()
    arc.appendArc(withCenter: foot, radius: radius, startAngle: 45, endAngle: 135)
    arc.lineWidth = 64
    arc.lineCapStyle = .round
    arc.stroke()
}
NSBezierPath(ovalIn: NSRect(x: foot.x - 46, y: foot.y - 46, width: 92, height: 92)).fill()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
