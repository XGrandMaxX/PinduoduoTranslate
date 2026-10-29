import CoreGraphics

enum RemoteGeometry {
    // The display layer and input mapping must share this exact crop.
    static func fillRect(source: CGSize, in bounds: CGRect) -> CGRect {
        guard source.width > 0, source.height > 0, bounds.width > 0, bounds.height > 0 else { return .zero }
        let scale = max(bounds.width / source.width, bounds.height / source.height)
        let size = CGSize(width: source.width * scale, height: source.height * scale)
        return CGRect(x: bounds.midX - size.width / 2, y: bounds.midY - size.height / 2,
                      width: size.width, height: size.height)
    }

    static func sourcePoint(_ point: CGPoint, source: CGSize, bounds: CGRect) -> CGPoint {
        let rect = fillRect(source: source, in: bounds)
        guard rect.width > 0, rect.height > 0 else { return .zero }
        return CGPoint(x: max(0, min(source.width - 1, (point.x - rect.minX) / rect.width * source.width)),
                       y: max(0, min(source.height - 1, (point.y - rect.minY) / rect.height * source.height)))
    }
}
