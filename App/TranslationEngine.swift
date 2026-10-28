import SwiftUI
import Translation

struct TranslatedLine: Identifiable {
    let id: Int
    let source: String
    let target: String
    let confidence: Float
}

@MainActor
final class TranslationEngine: ObservableObject {
    @Published private(set) var rows: [TranslatedLine] = []
    @Published private(set) var status = "Сначала загрузите языки"
    @Published private(set) var languagesReady = false
    @Published private(set) var isPreparing = false
    @Published var isPaused = false {
        didSet {
            generation += 1
            rows = []
            lastFingerprint = nil
            status = isPaused ? "Перевод на паузе" : "Жду экран…"
        }
    }
    @Published var region: CaptureRegion = .top {
        didSet {
            generation += 1
            rows = []
            lastFingerprint = nil
            do { try store.saveSettings(CaptureSettings(region: region)) }
            catch { status = error.localizedDescription }
        }
    }

    static let source = Locale.Language(identifier: "zh-Hans")
    static let target = Locale.Language(identifier: "ru")
    private let store = SharedStore()
    private var session: TranslationSession?
    private var cache: [String: String] = [:]
    private var lastFingerprint: String?
    private var generation = 0
    private var polling: Task<Void, Never>?

    init() {
        do {
            try store.checkAccess()
            region = try store.settings().region
            // Text from an earlier launch must never be shown as a live result.
            try store.clearSnapshot()
        } catch { status = error.localizedDescription }
    }

    func prepare(using preparationSession: TranslationSession) async {
        guard !isPreparing else { return }
        isPreparing = true
        defer { isPreparing = false }
        status = "Подготавливаю китайский и русский…"
        do {
            let availability = await LanguageAvailability().status(from: Self.source, to: Self.target)
            guard availability != .unsupported else {
                status = "Эта система не поддерживает пару китайский → русский"
                return
            }
            try await preparationSession.prepareTranslation()
            // This view-bound session is used only inside translationTask.
            _ = try await preparationSession.translate("商品规格")
            session = TranslationSession(installedSource: Self.source, target: Self.target)
            languagesReady = true
            status = "Языки готовы. Включите плавающее окно и захват экрана"
            startPolling()
        } catch {
            languagesReady = false
            session = nil
            status = "Не удалось подготовить языки: \(error.localizedDescription)"
        }
    }

    private func startPolling() {
        guard polling == nil else { return }
        polling = Task { [weak self] in
            while !Task.isCancelled {
                await self?.readAndTranslate()
                do { try await Task.sleep(nanoseconds: 650_000_000) }
                catch { return }
            }
        }
    }

    private func fingerprint(_ lines: [RecognizedLine]) -> String {
        lines.map(\.text).joined(separator: "\u{001F}")
    }

    private func readAndTranslate() async {
        guard !isPaused, languagesReady, let session else { return }
        do {
            guard let snapshot = try store.read() else { return }
            guard Date().timeIntervalSince(snapshot.date) < 8 else {
                rows = []
                lastFingerprint = nil
                status = "Нет свежего кадра. Проверьте захват экрана"
                return
            }
            guard snapshot.state == .reading else {
                rows = []
                lastFingerprint = nil
                switch snapshot.state {
                case .paused: status = "Захват экрана на паузе"
                case .stopped:
                    status = "Захват экрана завершён"
                    cache.removeAll()
                    try store.clearSnapshot()
                case .failed: status = snapshot.message ?? "Ошибка распознавания"
                case .reading: break
                }
                return
            }
            let signature = fingerprint(snapshot.lines)
            guard signature != lastFingerprint else { return }
            rows = []
            guard !snapshot.lines.isEmpty else {
                lastFingerprint = signature
                status = "В выбранной области нет китайского текста"
                return
            }
            status = "Перевожу…"
            let token = generation
            var missing = Set<String>()
            let requests = snapshot.lines.compactMap { line -> TranslationSession.Request? in
                guard cache[line.text] == nil, missing.insert(line.text).inserted else { return nil }
                return TranslationSession.Request(sourceText: line.text, clientIdentifier: line.text)
            }
            if !requests.isEmpty {
                let responses = try await session.translations(from: requests)
                // Bound memory; no translations are persisted to disk.
                if cache.count + responses.count > 128 {
                    let currentTexts = Set(snapshot.lines.map(\.text))
                    cache = cache.filter { currentTexts.contains($0.key) }
                }
                for response in responses {
                    if let key = response.clientIdentifier { cache[key] = response.targetText }
                }
            }
            // A slow result from before a scroll must not appear over a newer screen.
            guard !isPaused, token == generation,
                  let latest = try store.read(), latest.state == .reading,
                  Date().timeIntervalSince(latest.date) < 8,
                  fingerprint(latest.lines) == signature else { return }
            rows = snapshot.lines.enumerated().map { index, line in
                TranslatedLine(id: index, source: line.text, target: cache[line.text] ?? line.text,
                               confidence: line.confidence)
            }
            lastFingerprint = signature
            status = "Китайский → русский · \(rows.count) строк"
        } catch {
            rows = []
            lastFingerprint = nil
            status = "Ошибка перевода: \(error.localizedDescription)"
        }
    }
}
