// Draws the app icon (a dark dot on a light rounded square — the README's "ken ●")
// into an .iconset folder. Usage: gen-icon <out.iconset>
import AppKit

let out = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

func render(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
        samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
        bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px)
    let full = NSRect(x: 0, y: 0, width: s, height: s)
    NSColor.clear.setFill()
    full.fill(using: .copy)
    // macOS icons sit inside ~10% of transparent margin
    let inset = s * 0.10
    let bg = NSBezierPath(roundedRect: full.insetBy(dx: inset, dy: inset), xRadius: s * 0.18, yRadius: s * 0.18)
    NSColor(calibratedWhite: 0.96, alpha: 1).setFill()
    bg.fill()
    let d = s * 0.38
    let dot = NSBezierPath(ovalIn: NSRect(x: (s - d) / 2, y: (s - d) / 2, width: d, height: d))
    NSColor(calibratedWhite: 0.12, alpha: 1).setFill()
    dot.fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for base in [16, 32, 128, 256, 512] {
    try render(base).write(to: out.appendingPathComponent("icon_\(base)x\(base).png"))
    try render(base * 2).write(to: out.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
