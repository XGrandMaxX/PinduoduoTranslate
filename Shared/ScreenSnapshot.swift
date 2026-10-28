import Foundation

enum CaptureRegion: String, CaseIterable, Codable, Identifiable {
    case whole, top, bottom
    var id: String { rawValue }
    var title: String {
        switch self {
        case .whole: return "Весь экран"
        case .top: return "Верхняя половина"
        case .bottom: return "Нижняя половина"
        }
    }
    // Vision uses normalized coordinates with the origin at bottom left.
    var rectangle: CGRect {
        switch self {
        case .whole: return CGRect(x: 0, y: 0, width: 1, height: 1)
        case .top: return CGRect(x: 0, y: 0.5, width: 1, height: 0.5)
        case .bottom: return CGRect(x: 0, y: 0, width: 1, height: 0.5)
        }
    }
}

struct RecognizedLine: Codable, Sendable {
    let text: String
    let confidence: Float
}

struct ScreenSnapshot: Codable, Sendable {
    enum State: String, Codable { case reading, paused, stopped, failed }
    let id: UUID
    let date: Date
    let state: State
    let lines: [RecognizedLine]
    let message: String?

    init(state: State, lines: [RecognizedLine] = [], message: String? = nil) {
        id = UUID()
        date = Date()
        self.state = state
        self.lines = lines
        self.message = message
    }
}

struct CaptureSettings: Codable {
    var region: CaptureRegion = .top
}

enum SharedStoreError: LocalizedError {
    case missingGroup
    var errorDescription: String? {
        "Нет доступа к App Group. Проверьте подпись и одинаковую группу у приложения и расширения."
    }
}

// Only one current OCR result is stored, never screen images or a history.
struct SharedStore {
    private func file(_ name: String) throws -> URL {
        if Bundle.main.object(forInfoDictionaryKey: "StorageMode") as? String == "private" {
            let manager = FileManager.default
            let root = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                       appropriateFor: nil, create: true)
                .appendingPathComponent("LiveTranslation", isDirectory: true)
            try manager.createDirectory(at: root, withIntermediateDirectories: true)
            return root.appendingPathComponent(name)
        }
        guard let group = Bundle.main.object(forInfoDictionaryKey: "AppGroupIdentifier") as? String,
              let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)
        else { throw SharedStoreError.missingGroup }
        return root.appendingPathComponent(name)
    }

    func checkAccess() throws { _ = try file("screen.json") }

    func write(_ snapshot: ScreenSnapshot) throws {
        try JSONEncoder().encode(snapshot).write(to: file("screen.json"), options: [.atomic, .completeFileProtection])
    }

    func read() throws -> ScreenSnapshot? {
        let url = try file("screen.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard ((attributes[.size] as? NSNumber)?.intValue ?? 0) < 65_536 else { return nil }
        return try JSONDecoder().decode(ScreenSnapshot.self, from: Data(contentsOf: url))
    }

    func clearSnapshot() throws {
        let url = try file("screen.json")
        if FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.removeItem(at: url)
        }
    }

    func settings() throws -> CaptureSettings {
        let url = try file("settings.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return CaptureSettings() }
        return try JSONDecoder().decode(CaptureSettings.self, from: Data(contentsOf: url))
    }

    func saveSettings(_ settings: CaptureSettings) throws {
        try JSONEncoder().encode(settings).write(to: file("settings.json"), options: [.atomic, .completeFileProtection])
    }
}
