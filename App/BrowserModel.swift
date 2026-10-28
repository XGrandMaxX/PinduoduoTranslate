import SwiftUI
import WebKit
import Translation
import Vision

private struct BrowserOCRLine: Sendable {
    let text: String
    let box: CGRect
}

private final class BrowserMessageHandler: NSObject, WKScriptMessageHandler {
    weak var owner: BrowserModel?
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.body as? String == "layout" else { return }
        Task { @MainActor [weak self] in self?.owner?.viewportChanged() }
    }
}

@MainActor
final class BrowserModel: NSObject, ObservableObject {
    let webView: WKWebView
    @Published private(set) var status = "Подготавливаю браузер…"
    @Published private(set) var languagesReady = false
    @Published private(set) var preparing = false
    @Published private(set) var loading = false
    @Published private(set) var canGoBack = false
    @Published private(set) var canGoForward = false
    @Published private(set) var address = ""
    @Published private(set) var captions: [WebCaption] = []
    @Published private(set) var translatedCount = 0
    @Published var externalURL: URL?
    @Published var selectingRegion = false { didSet { viewportChanged() } }
    @Published var enabled = true {
        didSet {
            epoch += 1
            viewportChanged()
            if !enabled {
                status = "Оригинал · перевод выключен"
                Task { await restorePage() }
            }
        }
    }
    @Published var preferences = BrowserPreferences() {
        didSet {
            if let data = try? JSONEncoder().encode(preferences) {
                UserDefaults.standard.set(data, forKey: "browser.preferences.v1")
            }
            if oldValue.region != preferences.region || oldValue.replacePageText != preferences.replacePageText {
                epoch += 1
                translatedCount = 0
                viewportChanged()
                Task { await restorePage() }
            }
            if !preferences.imageCaptions { captions = [] }
        }
    }

    private var session: TranslationSession?
    private var cache: [String: String] = [:]
    private var timerTask: Task<Void, Never>?
    private var active = false
    private var busy = false
    private var epoch = 0
    private var visualEpoch = 0
    private var lastOCR = Date.distantPast
    private let messageHandler: BrowserMessageHandler

    override init() {
        let controller = WKUserContentController()
        messageHandler = BrowserMessageHandler()
        controller.add(messageHandler, contentWorld: .defaultClient, name: "pdtLayout")
        if let url = Bundle.main.url(forResource: "TranslatePage", withExtension: "js"),
           let script = try? String(contentsOf: url, encoding: .utf8) {
            controller.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentEnd,
                                                 forMainFrameOnly: true, in: .defaultClient))
        }
        let config = WKWebViewConfiguration()
        config.userContentController = controller
        config.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        messageHandler.owner = self
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.scrollView.delegate = self
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.allowsBackForwardNavigationGestures = true
        if let saved = UserDefaults.standard.data(forKey: "browser.preferences.v1"), saved.count < 4096,
           let style = try? JSONDecoder().decode(BrowserPreferences.self, from: saved) {
            preferences = style
        }
    }

    func activate() {
        guard !active else { return }
        active = true
        if webView.url == nil { showDemo() }
        timerTask = Task { [weak self] in
            await self?.checkLanguages()
            while !Task.isCancelled {
                await self?.translateVisiblePage()
                do { try await Task.sleep(nanoseconds: 1_100_000_000) } catch { return }
            }
        }
    }

    func deactivate() {
        active = false
        epoch += 1
        timerTask?.cancel()
        timerTask = nil
        captions = []
    }

    private func checkLanguages() async {
        let availability = await LanguageAvailability().status(from: TranslationEngine.source, to: TranslationEngine.target)
        if availability == .installed {
            session = TranslationSession(installedSource: TranslationEngine.source, target: TranslationEngine.target)
            languagesReady = true
            status = "Китайский → русский · перевод включён"
        } else { status = "Подготовьте китайский и русский языки" }
    }

    func prepare(using preparationSession: TranslationSession) async {
        guard !preparing else { return }
        preparing = true
        defer { preparing = false }
        status = "Подготавливаю китайский и русский…"
        do {
            try await preparationSession.prepareTranslation()
            _ = try await preparationSession.translate("商品规格")
            session = TranslationSession(installedSource: TranslationEngine.source, target: TranslationEngine.target)
            languagesReady = true
            status = "Языки готовы"
        } catch { status = "Не удалось подготовить языки: \(error.localizedDescription)" }
    }

    func navigate(_ input: String) {
        var text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.contains("://") { text = "https://" + text }
        guard let url = URL(string: text), url.scheme?.lowercased() == "https",
              url.host != nil, url.user == nil, url.password == nil else {
            status = "Введите ссылку https:// на страницу или товар"
            return
        }
        webView.load(URLRequest(url: url))
    }

    func showDemo() {
        guard let url = Bundle.main.url(forResource: "Demo", withExtension: "html") else {
            status = "Не найдена локальная демонстрационная страница"
            return
        }
        webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    }

    func openExternal() {
        guard let url = externalURL else { return }
        externalURL = nil
        UIApplication.shared.open(url)
    }

    func viewportChanged() {
        visualEpoch += 1
        captions = []
    }

    private func javascript<T: Decodable>(_ body: String, arguments: [String: Any] = [:], as type: T.Type) async throws -> T {
        let result = try await webView.callAsyncJavaScript(body, arguments: arguments, in: nil, contentWorld: .defaultClient)
        guard let result else { throw BrowserError.pageNotReady }
        let data = try JSONSerialization.data(withJSONObject: result, options: [.fragmentsAllowed])
        return try JSONDecoder().decode(type, from: data)
    }

    private func restorePage() async {
        _ = try? await webView.callAsyncJavaScript("window.__pdtTranslator?.restore();", arguments: [:],
                                                  in: nil, contentWorld: .defaultClient)
    }

    private func translate(_ strings: [String]) async throws -> [String: String] {
        guard let session else { throw BrowserError.languagesMissing }
        let unique = Array(Set(strings))
        let missing = unique.filter { cache[$0] == nil }
        if !missing.isEmpty {
            let requests = missing.map { TranslationSession.Request(sourceText: $0, clientIdentifier: $0) }
            let result = try await session.translations(from: requests)
            if cache.count + result.count > 256 {
                let current = Set(unique)
                cache = cache.filter { current.contains($0.key) }
            }
            for response in result {
                if let source = response.clientIdentifier { cache[source] = response.targetText }
            }
        }
        return Dictionary(uniqueKeysWithValues: unique.map { ($0, cache[$0] ?? $0) })
    }

    private func translateVisiblePage() async {
        guard active, enabled, languagesReady, !loading, !busy, !selectingRegion,
              !webView.scrollView.isDragging, !webView.scrollView.isDecelerating else { return }
        busy = true
        defer { busy = false }
        let token = epoch
        do {
            let batch: PageTextBatch = try await javascript(
                "return window.__pdtTranslator.collect(region, 48);",
                arguments: ["region": preferences.region.dictionary], as: PageTextBatch.self)
            guard !batch.scrolling else { return }
            if preferences.replacePageText && !batch.items.isEmpty {
                status = "Перевожу страницу…"
                let values = try await translate(batch.items.map(\.text))
                guard token == epoch, active, enabled, !Task.isCancelled else { return }
                let entries = batch.items.map { ["id": $0.id, "source": $0.text, "target": values[$0.text] ?? $0.text] }
                let count: Int = try await javascript("return window.__pdtTranslator.apply(documentID, entries);",
                    arguments: ["documentID": batch.documentID, "entries": entries], as: Int.self)
                translatedCount += count
            }
            if preferences.imageCaptions && Date().timeIntervalSince(lastOCR) > 2 {
                lastOCR = Date()
                try await translateImages(pageEpoch: token)
            }
            guard token == epoch, active, enabled else { return }
            status = "Китайский → русский · заменено \(translatedCount) · подписей \(captions.count)"
        } catch {
            guard token == epoch, active, !Task.isCancelled else { return }
            status = "Перевод страницы: \(error.localizedDescription)"
        }
    }

    private func translateImages(pageEpoch: Int) async throws {
        guard webView.bounds.width > 0, webView.bounds.height > 0 else { return }
        let before: PageSnapshotInfo = try await javascript(
            "return window.__pdtTranslator.snapshotInfo();", as: PageSnapshotInfo.self)
        guard !before.scrolling else { return }
        let visualToken = visualEpoch
        let configuration = WKSnapshotConfiguration()
        configuration.rect = webView.bounds
        configuration.snapshotWidth = NSNumber(value: Double(webView.bounds.width))
        let image = try await webView.takeSnapshot(configuration: configuration)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let masked = UIGraphicsImageRenderer(size: image.size, format: format).image { context in
            image.draw(at: .zero)
            UIColor.white.setFill()
            for region in before.privateRects { context.fill(region.rect(in: image.size)) }
        }
        guard let cgImage = masked.cgImage else { return }
        let region = preferences.region.visionRectangle
        let lines = try await Task.detached(priority: .utility) { () throws -> [BrowserOCRLine] in
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.minimumTextHeight = 0.012
            request.regionOfInterest = region
            try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
            return Array((request.results ?? []).compactMap { observation -> BrowserOCRLine? in
                guard let value = observation.topCandidates(1).first, value.confidence >= 0.35,
                      value.string.unicodeScalars.contains(where: { (0x3400...0x9FFF).contains($0.value) }) else { return nil }
                let b = observation.boundingBox
                return BrowserOCRLine(text: String(value.string.prefix(240)),
                    box: CGRect(x: b.minX, y: 1 - b.maxY, width: b.width, height: b.height))
            }.prefix(24))
        }.value
        guard pageEpoch == epoch, visualToken == visualEpoch, active, enabled else { return }
        let values = try await translate(lines.map(\.text))
        let after: PageSnapshotInfo = try await javascript(
            "return window.__pdtTranslator.snapshotInfo();", as: PageSnapshotInfo.self)
        guard pageEpoch == epoch, visualToken == visualEpoch, enabled, active, !selectingRegion,
              after.documentID == before.documentID, after.revision == before.revision, !after.scrolling else { return }
        captions = lines.enumerated().map { WebCaption(id: $0.offset, text: values[$0.element.text] ?? $0.element.text,
                                                       box: $0.element.box) }
    }

    private func navigationChanged() {
        epoch += 1
        viewportChanged()
        cache.removeAll()
        translatedCount = 0
        address = webView.url?.isFileURL == true ? "" : (webView.url?.absoluteString ?? "")
        canGoBack = webView.canGoBack
        canGoForward = webView.canGoForward
    }
}

private enum BrowserError: LocalizedError {
    case pageNotReady, languagesMissing
    var errorDescription: String? {
        switch self {
        case .pageNotReady: return "Страница ещё загружается"
        case .languagesMissing: return "Сначала подготовьте языки"
        }
    }
}

extension BrowserModel: WKNavigationDelegate, WKUIDelegate, UIScrollViewDelegate {
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        loading = true
        status = "Загружаю страницу…"
        navigationChanged()
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loading = false
        status = languagesReady ? "Перевожу видимый текст страницы…" : "Подготовьте китайский и русский языки"
        navigationChanged()
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        loading = false
        status = "Страница не открылась: \(error.localizedDescription)"
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        loading = false
        status = "Ошибка страницы: \(error.localizedDescription)"
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        loading = false
        navigationChanged()
        status = "Браузер остановил страницу. Нажмите обновление"
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard let url = navigationAction.request.url else { return .cancel }
        if ["https", "file", "about"].contains(url.scheme?.lowercased() ?? "") { return .allow }
        if navigationAction.navigationType == .linkActivated,
           ["weixin", "wechat", "alipay", "alipays", "pinduoduo", "pinduoduoapp"].contains(url.scheme?.lowercased() ?? "") {
            externalURL = url
        } else { status = "Сайт предлагает перейти в другое приложение" }
        return .cancel
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if navigationAction.targetFrame == nil, navigationAction.request.url?.scheme == "https" {
            webView.load(navigationAction.request)
        }
        return nil
    }
    func scrollViewWillBeginDragging(_ scrollView: UIScrollView) { viewportChanged() }
    func scrollViewDidScroll(_ scrollView: UIScrollView) { viewportChanged() }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        presentDialog(webView, message: message, completion: { _ in completionHandler() })
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        presentDialog(webView, message: message, cancel: true, completion: completionHandler)
    }
    private func presentDialog(_ webView: WKWebView, message: String, cancel: Bool = false,
                               completion: @escaping (Bool) -> Void) {
        guard var parent = webView.window?.rootViewController else { completion(false); return }
        while let presented = parent.presentedViewController { parent = presented }
        guard !(parent is UIAlertController) else { completion(false); return }
        let dialog = UIAlertController(title: webView.url?.host ?? "Страница", message: message, preferredStyle: .alert)
        if cancel { dialog.addAction(UIAlertAction(title: "Отмена", style: .cancel) { _ in completion(false) }) }
        dialog.addAction(UIAlertAction(title: "OK", style: .default) { _ in completion(true) })
        parent.present(dialog, animated: true)
    }
}
