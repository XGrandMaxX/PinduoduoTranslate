import SwiftUI
import WebKit
import Translation

struct BrowserView: View {
    @StateObject private var model = BrowserModel()
    @State private var address = ""
    @State private var settings = false
    @State private var configuration: TranslationSession.Configuration?
    @FocusState private var editingAddress: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "globe").foregroundStyle(.secondary)
                TextField("Ссылка на сайт или товар", text: $address)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .submitLabel(.go).focused($editingAddress).onSubmit(openAddress)
                Button(action: openAddress) { Image(systemName: "arrow.right.circle.fill") }
                    .accessibilityLabel("Открыть адрес")
            }
            .padding(10).background(.quaternary, in: RoundedRectangle(cornerRadius: 12)).padding(8)

            HStack(spacing: 18) {
                Button { model.webView.goBack() } label: { Image(systemName: "chevron.left") }
                    .disabled(!model.canGoBack).accessibilityLabel("Назад")
                Button { model.webView.goForward() } label: { Image(systemName: "chevron.right") }
                    .disabled(!model.canGoForward).accessibilityLabel("Вперёд")
                Button { model.webView.reload() } label: { Image(systemName: "arrow.clockwise") }
                    .accessibilityLabel("Обновить")
                Menu {
                    Button("Pinduoduo") { editingAddress = false; model.navigate("https://mobile.pinduoduo.com/") }
                    Button("Тестовая страница") { editingAddress = false; model.showDemo() }
                } label: { Image(systemName: "bookmark") }
                    .accessibilityLabel("Закладки")
                Spacer(minLength: 0)
                Button { model.enabled.toggle() } label: {
                    Label(model.enabled ? "RU" : "中文", systemImage: "translate")
                        .font(.subheadline.weight(.semibold))
                }.tint(model.enabled ? .accentColor : .secondary)
                Button { settings = true } label: { Image(systemName: "slider.horizontal.3") }
                    .accessibilityLabel("Настройки перевода")
            }.padding(.horizontal, 16).padding(.bottom, 10)

            if !model.languagesReady {
                Button(model.preparing ? "Подготовка языков…" : "Загрузить китайский и русский") {
                    if configuration == nil {
                        configuration = .init(source: TranslationEngine.source, target: TranslationEngine.target)
                    } else { configuration?.invalidate() }
                }.buttonStyle(.borderedProminent).disabled(model.preparing).padding(.bottom, 8)
            }
            HStack(spacing: 6) {
                if model.loading || model.preparing { ProgressView().controlSize(.mini) }
                Text(model.status).font(.caption2).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.foregroundStyle(.secondary).padding(.horizontal, 12).padding(.bottom, 6)

            GeometryReader { geometry in
                ZStack(alignment: .topLeading) {
                    BrowserWebView(webView: model.webView)
                    if !model.selectingRegion {
                        if model.preferences.region != .whole {
                            regionBorder(model.preferences.region, in: geometry.size)
                                .allowsHitTesting(false)
                        }
                        CaptionLayer(captions: model.captions, preferences: model.preferences)
                            .allowsHitTesting(false).accessibilityHidden(true)
                    } else {
                        RegionSelector(region: $model.preferences.region)
                    }
                }.clipped()
            }
            if model.selectingRegion {
                HStack {
                    Text("Обведите область пальцем").font(.caption)
                    Spacer()
                    Button("Весь экран") { model.preferences.region = .whole }
                    Button("Готово") { model.selectingRegion = false }.bold()
                }.padding(12).background(.regularMaterial)
            }
        }
        .background(Color(.systemBackground))
        .sheet(isPresented: $settings) {
            BrowserSettings(model: model) {
                settings = false
                model.selectingRegion = true
            }
        }
        .alert("Открыть другое приложение?", isPresented: Binding(
            get: { model.externalURL != nil }, set: { _ in })) {
                Button("Открыть") { model.openExternal() }
                Button("Остаться здесь", role: .cancel) { model.externalURL = nil }
            } message: {
                Text("Перевод работает внутри этого браузера. Внешнее приложение откроется в своём интерфейсе.")
            }
        .translationTask(configuration) { session in await model.prepare(using: session) }
        .onAppear { model.activate() }
        .onDisappear { model.deactivate() }
        .onChange(of: model.address) { _, value in if !editingAddress { address = value } }
    }

    private func openAddress() {
        editingAddress = false
        model.navigate(address)
    }

    private func regionBorder(_ region: WebRegion, in size: CGSize) -> some View {
        let rect = region.rect(in: size)
        return Rectangle().stroke(Color.accentColor.opacity(0.4), style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
            .frame(width: rect.width, height: rect.height).position(x: rect.midX, y: rect.midY)
    }
}

private struct BrowserWebView: UIViewRepresentable {
    let webView: WKWebView
    func makeUIView(context: Context) -> WKWebView { webView }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

private struct CaptionLayer: View {
    let captions: [WebCaption]
    let preferences: BrowserPreferences
    var body: some View {
        GeometryReader { geometry in
            ForEach(captions) { caption in
                let rect = CGRect(x: caption.box.minX * geometry.size.width,
                                  y: caption.box.minY * geometry.size.height,
                                  width: caption.box.width * geometry.size.width,
                                  height: caption.box.height * geometry.size.height)
                let width = min(max(rect.width + 32, 100), min(190, geometry.size.width))
                let x = min(max(rect.midX, width / 2), geometry.size.width - width / 2)
                let y = min(max(rect.minY - 20, 24), max(24, geometry.size.height - 24))
                Text(caption.text)
                    .font(.system(size: preferences.fontSize, weight: .medium))
                    .foregroundStyle(preferences.textColor.opacity(preferences.textOpacity))
                    .lineLimit(2).minimumScaleFactor(0.8)
                    .padding(.horizontal, 5).padding(.vertical, 3)
                    .frame(width: width)
                    .background(preferences.backgroundColor.opacity(preferences.backgroundOpacity),
                                in: RoundedRectangle(cornerRadius: 4))
                    .overlay(alignment: .bottom) {
                        Rectangle().fill(preferences.textColor.opacity(preferences.textOpacity * 0.5))
                            .frame(width: 1, height: 7).offset(y: 7)
                    }
                    .position(x: x, y: y)
            }
        }
    }
}

private struct RegionSelector: View {
    @Binding var region: WebRegion
    @State private var start: CGPoint?
    var body: some View {
        GeometryReader { geometry in
            let rect = region.rect(in: geometry.size)
            ZStack(alignment: .topLeading) {
                Color.black.opacity(0.16)
                Rectangle().fill(Color.accentColor.opacity(0.13))
                    .overlay(Rectangle().stroke(Color.accentColor, lineWidth: 2))
                    .frame(width: rect.width, height: rect.height).position(x: rect.midX, y: rect.midY)
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { value in
                    if start == nil { start = value.startLocation }
                    region = .dragged(from: start ?? value.startLocation, to: value.location, size: geometry.size)
                }
                .onEnded { _ in start = nil })
        }
    }
}

private struct BrowserSettings: View {
    @ObservedObject var model: BrowserModel
    let selectRegion: () -> Void
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            Form {
                Section("Перевод страницы") {
                    Toggle("Заменять китайский текст русским", isOn: $model.preferences.replacePageText)
                    Toggle("Подписи для текста на картинках", isOn: $model.preferences.imageCaptions)
                    Text("Обычный текст сохраняет оформление сайта. Настройки ниже меняют подписи над картинками. После прокрутки перевод появляется с небольшой задержкой.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Область перевода") {
                    Button("Выделить область на странице", action: selectRegion)
                    Button("Переводить всю видимую страницу") { model.preferences.region = .whole }
                    Text("Область закреплена на экране браузера. При прокрутке переводится новый текст внутри рамки.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Подписи") {
                    HStack { Text("Размер"); Spacer(); Text("\(Int(model.preferences.fontSize)) пт") }
                    Slider(value: $model.preferences.fontSize, in: 8...22, step: 1)
                    ColorPicker("Цвет текста", selection: Binding(
                        get: { model.preferences.textColor },
                        set: { model.preferences.setColor($0, background: false) }), supportsOpacity: false)
                    Text("Видимость текста: \(Int(model.preferences.textOpacity * 100))%")
                    Slider(value: $model.preferences.textOpacity, in: 0.15...1)
                    ColorPicker("Цвет подложки", selection: Binding(
                        get: { model.preferences.backgroundColor },
                        set: { model.preferences.setColor($0, background: true) }), supportsOpacity: false)
                    Text("Видимость подложки: \(Int(model.preferences.backgroundOpacity * 100))%")
                    Slider(value: $model.preferences.backgroundOpacity, in: 0...1)
                    Text("Пример: мягкий хлопок · 39,90 юаня")
                        .font(.system(size: model.preferences.fontSize))
                        .foregroundStyle(model.preferences.textColor.opacity(model.preferences.textOpacity))
                        .padding(6).background(model.preferences.backgroundColor.opacity(model.preferences.backgroundOpacity))
                }
                Section {
                    Text("Перевод выполняется на iPhone. Пароли и поля ввода исключены из распознавания. Вход на сайт и платежи обрабатывает сам сайт. Машинный перевод может ошибаться.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Перевод").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Готово") { dismiss() } } }
        }
    }
}
