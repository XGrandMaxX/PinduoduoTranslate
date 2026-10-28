import SwiftUI
import Translation
import ReplayKit

@main
struct PinduoTranslateApp: App {
    var body: some Scene {
        WindowGroup {
            TabView {
                BrowserView().tabItem { Label("Браузер", systemImage: "globe") }
                ContentView().tabItem { Label("Захват экрана", systemImage: "rectangle.inset.filled") }
            }
        }
    }
}

struct ContentView: View {
    @StateObject private var engine = TranslationEngine()
    @StateObject private var floating = FloatingWindow()
    @StateObject private var capture = CaptureCoordinator()
    @State private var configuration: TranslationSession.Configuration?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    Text("Листайте товары.\nЧитайте по-русски.")
                        .font(.largeTitle.bold())
                    Text("Китайский → русский · перевод на iPhone")
                        .foregroundStyle(.secondary)
                    Label(engine.status, systemImage: "translate")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding().background(.mint.opacity(0.12), in: RoundedRectangle(cornerRadius: 16))

                    VStack(alignment: .leading, spacing: 14) {
                        Text("1. Подготовьте языки").font(.headline)
                        Text("При первом запуске разрешите загрузку языковых моделей. После этого перевод работает на устройстве.")
                            .font(.subheadline).foregroundStyle(.secondary)
                        Button(engine.languagesReady ? "Проверить языки ещё раз" : "Загрузить китайский и русский") {
                            if configuration == nil {
                                configuration = .init(source: TranslationEngine.source, target: TranslationEngine.target)
                            } else { configuration?.invalidate() }
                        }
                        .buttonStyle(.borderedProminent).disabled(engine.isPreparing)
                    }

                    VStack(alignment: .leading, spacing: 14) {
                        Text("2. Включите плавающее окно").font(.headline)
                        VideoPreview(surface: floating.surface)
                            .frame(width: 180, height: 252).clipShape(RoundedRectangle(cornerRadius: 12))
                        Button(floating.isActive ? "Закрыть окно" : "Открыть окно перевода") {
                            if floating.isActive { floating.stop() } else { floating.start() }
                        }
                        .buttonStyle(.bordered).disabled(!engine.languagesReady)
                        if let error = floating.error { Text(error).foregroundStyle(.red).font(.caption) }
                    }

                    VStack(alignment: .leading, spacing: 14) {
                        Text("3. Разрешите захват экрана").font(.headline)
                        if #available(iOS 27.0, *) {
                            Button(capture.isCapturing ? "Выбрать экран заново" : "Выбрать экран для перевода") {
                                capture.start()
                            }.buttonStyle(.borderedProminent).disabled(!engine.languagesReady)
                            Text("Выберите захват всего экрана, затем откройте Pinduoduo.")
                                .font(.subheadline)
                            if capture.isCapturing {
                                Button("Остановить захват", role: .destructive) { capture.stop() }
                            }
                            if let status = capture.status { Text(status).font(.caption) }
                        } else {
                            HStack {
                                BroadcastPicker().frame(width: 48, height: 48).disabled(!engine.languagesReady)
                                Text("Нажмите системную кнопку и выберите «Начать трансляцию», затем откройте Pinduoduo.")
                                    .font(.subheadline)
                            }
                        }
                        Text("Микрофон не нужен. Также можно завершить захват через системный индикатор записи iOS → «Остановить».")
                            .font(.caption).foregroundStyle(.secondary)
                    }

                    Picker("Какую область читать", selection: $engine.region) {
                        ForEach(CaptureRegion.allCases) { region in Text(region.title).tag(region) }
                    }.pickerStyle(.menu)
                    Text("По умолчанию читается верхняя половина. Переместите окно вниз, чтобы оно не закрывало товар. Закрытый окном текст распознать нельзя.")
                        .font(.caption).foregroundStyle(.secondary)
                    Toggle("Приостановить перевод", isOn: $engine.isPaused)
                    Text("Пауза и закрытие окна не останавливают системный захват экрана. Остановите его отдельно.")
                        .font(.caption).foregroundStyle(.secondary)

                    if !engine.rows.isEmpty {
                        Text("Текущий экран").font(.title2.bold())
                        ForEach(engine.rows) { row in
                            VStack(alignment: .leading, spacing: 6) {
                                Text(row.target).font(.headline)
                                Text(row.source).font(.subheadline).foregroundStyle(.secondary)
                                if row.confidence < 0.65 {
                                    Label("Текст распознан неуверенно — проверьте оригинал", systemImage: "exclamationmark.triangle")
                                        .font(.caption).foregroundStyle(.orange)
                                }
                            }
                            Divider()
                        }
                    }
                    Text("Это прототип. Перевод обновляется после распознавания и может ошибаться. Цены, размеры и условия заказа сверяйте с оригиналом. Снимки экрана не сохраняются; один текущий результат распознавания временно хранится на устройстве.")
                        .font(.footnote).foregroundStyle(.secondary)
                }.padding(22)
            }
            .navigationTitle("Пиндуо Перевод").navigationBarTitleDisplayMode(.inline)
            .tint(.teal)
        }
        .onAppear { floating.configure(engine: engine) }
        .translationTask(configuration) { session in await engine.prepare(using: session) }
    }
}

struct VideoPreview: UIViewRepresentable {
    let surface: VideoSurface
    func makeUIView(context: Context) -> VideoSurface { surface }
    func updateUIView(_ uiView: VideoSurface, context: Context) {}
}

struct BroadcastPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 48, height: 48))
        picker.preferredExtension = Bundle.main.object(forInfoDictionaryKey: "BroadcastExtensionIdentifier") as? String
        picker.showsMicrophoneButton = false
        return picker
    }
    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}
