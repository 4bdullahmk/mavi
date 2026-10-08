import AppKit
import Foundation

let outputFolder = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "./icons"
try FileManager.default.createDirectory(atPath: outputFolder, withIntermediateDirectories: true)

let background = NSColor(red: 0.09, green: 0.09, blue: 0.09, alpha: 1)
let letterColor = NSColor(red: 0.93, green: 0.92, blue: 0.90, alpha: 1)

let sizes = [16, 32, 128, 256, 512]
let multipliers = [1, 2]

for size in sizes {
    for multiplier in multipliers {
        let p = CGFloat(size * multiplier)
        
        let bitmapRep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: Int(p),
            pixelsHigh: Int(p),
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        )!
        
        let context = NSGraphicsContext(bitmapImageRep: bitmapRep)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        
        background.set()
        NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: p, height: p), xRadius:p * 0.22, yRadius:p * 0.22).fill()
        
        let transform = NSAffineTransform()
        transform.scale(by: p / 100)
        transform.concat()
        letterColor.setStroke()
        func stroke(width: CGFloat, _ draw: (NSBezierPath) -> Void) {
            let line = NSBezierPath()
            line.lineWidth = width
            line.lineCapStyle = .round
            line.lineJoinStyle = .round
            draw(line)
            line.stroke()
        }
        // Draw the antlers first so their round joins tuck under the M stems.
        stroke(width: 6.5) { line in
            line.move(to: NSPoint(x: 30, y: 64))
            line.curve(to: NSPoint(x: 18, y: 80),
                       controlPoint1: NSPoint(x: 24, y: 63),
                       controlPoint2: NSPoint(x: 18, y: 72))
        }

        stroke(width: 6.5) { line in
            line.move(to: NSPoint(x: 70, y: 64))
            line.curve(to: NSPoint(x: 82, y: 80),
                       controlPoint1: NSPoint(x: 76, y: 63),
                       controlPoint2: NSPoint(x: 82, y: 72))
        }

        // A generous, continuous M keeps the antler hint secondary while its
        // rounded shoulders and deeper center dip keep the letter legible.
        stroke(width: 9.6) { line in
            line.move(to: NSPoint(x: 30, y: 24))
            line.line(to: NSPoint(x: 30, y: 68))
            line.curve(to: NSPoint(x: 50, y: 37),
                       controlPoint1: NSPoint(x: 30, y: 72),
                       controlPoint2: NSPoint(x: 44, y: 37))
            line.curve(to: NSPoint(x: 70, y: 68),
                       controlPoint1: NSPoint(x: 56, y: 37),
                       controlPoint2: NSPoint(x: 70, y: 72))
            line.line(to: NSPoint(x: 70, y: 24))
        }

        let data = bitmapRep.representation(using: .png, properties: [:])!
        let fileName = "icon_\(size)x\(size)\(multiplier == 2 ? "@2x" : "").png"
        let path = "\(outputFolder)/\(fileName)"
        NSGraphicsContext.restoreGraphicsState()
        try data.write(to: URL(fileURLWithPath: path))
    }
}
