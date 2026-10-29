import CoreGraphics

@main
enum GeometryChecks {
    static func main() {
        let source = CGSize(width: 480, height: 1040)
        for size in [CGSize(width: 414, height: 896), CGSize(width: 375, height: 667),
                     CGSize(width: 430, height: 932), CGSize(width: 393, height: 852),
                     CGSize(width: 896, height: 414)] {
            let bounds = CGRect(origin: .zero, size: size)
            let rect = RemoteGeometry.fillRect(source: source, in: bounds)
            assert(rect.width + 0.001 >= size.width && rect.height + 0.001 >= size.height, "Letterboxing")
            assert(abs(rect.width/source.width - rect.height/source.height) < 0.00001, "Stretched image")
            let center = RemoteGeometry.sourcePoint(CGPoint(x: bounds.midX, y: bounds.midY), source: source, bounds: bounds)
            assert(abs(center.x - 240) < 0.001 && abs(center.y - 520) < 0.001, "Wrong center tap")
            for point in [CGPoint.zero, CGPoint(x: size.width, y: size.height), CGPoint(x: 80, y: 120)] {
                let mapped = RemoteGeometry.sourcePoint(point, source: source, bounds: bounds)
                assert(mapped.x >= 0 && mapped.x < source.width && mapped.y >= 0 && mapped.y < source.height)
                // Verify the inverse mapping against the actual visible video crop.
                let expectedX = min(source.width-1,max(0,(point.x-rect.minX)/rect.width*source.width))
                let expectedY = min(source.height-1,max(0,(point.y-rect.minY)/rect.height*source.height))
                assert(abs(mapped.x-expectedX)<0.001 && abs(mapped.y-expectedY)<0.001)
            }
        }
        assert(RemoteGeometry.fillRect(source: .zero, in: .zero) == .zero)
        print("PASS: fullscreen crop, undistorted video and corresponding touch coordinates")
    }
}
