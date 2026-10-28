import SwiftUI

struct WebRegion: Codable, Equatable {
    var x = 0.0
    var y = 0.0
    var width = 1.0
    var height = 1.0
    static let whole = WebRegion()
    var dictionary: [String: Double] { ["x": x, "y": y, "width": width, "height": height] }
    var visionRectangle: CGRect { CGRect(x: x, y: 1 - y - height, width: width, height: height) }
    func rect(in size: CGSize) -> CGRect {
        CGRect(x: CGFloat(x) * size.width, y: CGFloat(y) * size.height,
               width: CGFloat(width) * size.width, height: CGFloat(height) * size.height)
    }
    static func dragged(from start: CGPoint, to end: CGPoint, size: CGSize) -> WebRegion {
        guard size.width > 0, size.height > 0 else { return .whole }
        let x1 = min(max(start.x / size.width, 0), 1), x2 = min(max(end.x / size.width, 0), 1)
        let y1 = min(max(start.y / size.height, 0), 1), y2 = min(max(end.y / size.height, 0), 1)
        let left = min(x1, x2), top = min(y1, y2)
        return WebRegion(x: Double(left), y: Double(top), width: Double(min(max(abs(x2 - x1), 0.04), 1 - left)),
                         height: Double(min(max(abs(y2 - y1), 0.04), 1 - top)))
    }
}

struct BrowserPreferences: Codable, Equatable {
    var region = WebRegion.whole
    var replacePageText = true
    var imageCaptions = true
    var fontSize = 12.0
    var textOpacity = 0.9
    var backgroundOpacity = 0.85
    var red = 0.08, green = 0.12, blue = 0.14
    var backgroundRed = 1.0, backgroundGreen = 1.0, backgroundBlue = 1.0
    var textColor: Color { Color(red: red, green: green, blue: blue) }
    var backgroundColor: Color { Color(red: backgroundRed, green: backgroundGreen, blue: backgroundBlue) }

    mutating func setColor(_ color: Color, background: Bool) {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, alpha: CGFloat = 0
        guard UIColor(color).getRed(&r, green: &g, blue: &b, alpha: &alpha) else { return }
        if background { backgroundRed = Double(r); backgroundGreen = Double(g); backgroundBlue = Double(b) }
        else { red = Double(r); green = Double(g); blue = Double(b) }
    }
}

struct WebCaption: Identifiable {
    let id: Int
    let text: String
    let box: CGRect // normalized viewport coordinates, origin at top left
}

struct PageTextItem: Decodable {
    let id: String
    let text: String
}

struct PageTextBatch: Decodable {
    let documentID: String
    let revision: Int
    let scrolling: Bool
    let width: Double
    let height: Double
    let items: [PageTextItem]
}

struct PageSnapshotInfo: Decodable {
    let documentID: String
    let revision: Int
    let scrolling: Bool
    let privateRects: [WebRegion]
}
