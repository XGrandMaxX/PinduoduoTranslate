import Foundation
import Vision
import ImageIO
import CoreVideo

enum ChineseOCR {
    static func recognize(_ buffer: CVPixelBuffer, orientation: CGImagePropertyOrientation,
                          region: CaptureRegion) throws -> [RecognizedLine] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true
        request.minimumTextHeight = 0.014
        request.regionOfInterest = region.rectangle
        try VNImageRequestHandler(cvPixelBuffer: buffer, orientation: orientation).perform([request])
        let observations = (request.results ?? []).sorted {
            // A strict order avoids comparator cycles with near-aligned boxes.
            let lhsRow = Int(($0.boundingBox.midY * 50).rounded())
            let rhsRow = Int(($1.boundingBox.midY * 50).rounded())
            return lhsRow == rhsRow ? $0.boundingBox.minX < $1.boundingBox.minX : lhsRow > rhsRow
        }
        return Array(observations.compactMap { item -> RecognizedLine? in
            guard let result = item.topCandidates(1).first, result.confidence >= 0.3 else { return nil }
            let text = String(result.string.trimmingCharacters(in: .whitespacesAndNewlines).prefix(240))
            guard text.unicodeScalars.contains(where: { (0x3400...0x9FFF).contains($0.value) }) else { return nil }
            return RecognizedLine(text: text, confidence: result.confidence)
        }.prefix(16))
    }
}
