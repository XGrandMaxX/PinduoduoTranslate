import SwiftUI
import AVFoundation
import CryptoKit
import Security

struct RemotePair: Codable {
    let version: Int
    let host: String
    let port: Int
    let fingerprint: String
    let secret: String
    var valid: Bool {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        let octets = parts.compactMap { Int($0) }
        let local = parts.count == 4 && octets.count == 4 && parts.allSatisfy { !$0.isEmpty && $0.allSatisfy { $0.isNumber } } && octets.allSatisfy { (0...255).contains($0) } &&
            (octets[0] == 10 || (octets[0] == 172 && (16...31).contains(octets[1])) || (octets[0] == 192 && octets[1] == 168))
        return version == 1 && local && (1...65535).contains(port) &&
        fingerprint.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil && secret.count >= 40
    }
}

final class RemoteSurface: UIView, UIKeyInput {
    let video = AVSampleBufferDisplayLayer()
    var send: (([String: Any]) -> Void)?
    var sourceSize = CGSize(width: 480, height: 1040)
    var original = false
    var fontScale: CGFloat = 1
    var automaticColors = true
    var textColor = UIColor.label
    var backgroundColorForText = UIColor.white
    private var labels: [(UILabel, [String: Any])] = []
    private var captions: [[String: Any]] = []
    private var offset: CGFloat = 0
    private var pointers: [ObjectIdentifier: Int] = [:]
    var videoRect: CGRect { RemoteGeometry.fillRect(source: sourceSize, in: bounds) }
    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .black
        clipsToBounds = true
        isMultipleTouchEnabled = true
        video.videoGravity = .resizeAspectFill
        layer.addSublayer(video)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:)") }
    override func layoutSubviews() {
        super.layoutSubviews()
        CATransaction.begin(); CATransaction.setDisableActions(true)
        video.frame = bounds
        moveLabels()
        CATransaction.commit()
    }
    func state(_ value: [String: Any]) {
        if let w = value["width"] as? Double, let h = value["height"] as? Double, w > 0, h > 0 {
            sourceSize = CGSize(width: w, height: h); setNeedsLayout()
        }
        if let v = value["scroll_offset"] as? Double { offset = v }
        if let motion = value["motion"] as? [String: Any], let v = motion["offset"] as? Double { offset = v }
        if let enabled = value["translation_enabled"] as? Bool { original = !enabled }
        if let values = value["captions"] as? [[String: Any]] { captions = values; rebuildLabels() }
        moveLabels()
    }
    func rebuildLabels() {
        for (label, _) in labels { label.removeFromSuperview() }
        labels = []
        guard !original else { return }
        for item in captions {
            guard let text = item["text"] as? String, let role = item["role"] as? String,
                  ["title", "button"].contains(role) else { continue }
            let label = UILabel()
            label.text = text; label.lineBreakMode = .byTruncatingTail
            label.numberOfLines = 1; label.clipsToBounds = true
            label.textAlignment = role == "button" ? .center : .left
            label.isUserInteractionEnabled = false
            let bg = UIColor(hex: item["background"] as? String ?? "#ffffff")
            label.backgroundColor = automaticColors ? bg : backgroundColorForText
            label.textColor = automaticColors ? (bg.brightness > 0.53 ? UIColor(red: 0.09, green: 0.15, blue: 0.17, alpha: 1) : .white) : textColor
            addSubview(label); labels.append((label, item))
        }
        moveLabels()
    }
    private func moveLabels() {
        let rect = videoRect, scale = rect.width / sourceSize.width
        CATransaction.begin(); CATransaction.setDisableActions(true)
        for (label, item) in labels {
            guard let box = item["box"] as? [Double], box.count == 4 else { continue }
            var shift: CGFloat = 0
            if item["scroll_box"] is [Double] { shift = offset - CGFloat(item["scroll_ref"] as? Double ?? 0) }
            let y = box[1] * sourceSize.height + shift
            label.frame = CGRect(x: rect.minX + box[0] * rect.width,
                                 y: rect.minY + y * scale,
                                 width: box[2] * rect.width, height: box[3] * rect.height)
            label.font = .systemFont(ofSize: min(24 * fontScale, box[3] * sourceSize.height * 0.8) * scale)
            var visible = label.frame.intersection(bounds)
            if let scroll = item["scroll_box"] as? [Double], scroll.count == 4 {
                let viewport = CGRect(x: rect.minX + scroll[0] * rect.width,
                                      y: rect.minY + scroll[1] * rect.height,
                                      width: (scroll[2] - scroll[0]) * rect.width,
                                      height: (scroll[3] - scroll[1]) * rect.height)
                visible = visible.intersection(viewport)
            }
            label.isHidden = visible.isNull || visible.isEmpty
            if label.isHidden { label.layer.mask=nil;continue }
            let mask = CAShapeLayer()
            mask.path = UIBezierPath(rect: visible.offsetBy(dx: -label.frame.minX, dy: -label.frame.minY)).cgPath
            label.layer.mask = mask
        }
        CATransaction.commit()
    }
    private func point(_ touch: UITouch) -> [String: Any] {
        let p = RemoteGeometry.sourcePoint(touch.location(in: self), source: sourceSize, bounds: bounds)
        return ["x": p.x, "y": p.y]
    }
    private func inject(_ touch: UITouch, action: Int, pointer: Int) {
        var value = point(touch)
        value["type"] = "touch"; value["action"] = action; value["pointer"] = pointer
        send?(value)
    }
    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches where pointers.count < 10 && videoRect.contains(touch.location(in: self)) {
            let id = (0..<10).first { !pointers.values.contains($0) } ?? 0
            pointers[ObjectIdentifier(touch)] = id; inject(touch, action: 0, pointer: id)
        }
    }
    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches { if let id = pointers[ObjectIdentifier(touch)] { inject(touch, action: 2, pointer: id) } }
    }
    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches { if let id = pointers.removeValue(forKey: ObjectIdentifier(touch)) { inject(touch, action: 1, pointer: id) } }
    }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches { if let id = pointers.removeValue(forKey: ObjectIdentifier(touch)) { inject(touch, action: 3, pointer: id) } }
    }
    func cancelInput() {
        for id in pointers.values { send?(["type":"touch","action":3,"pointer":id,"x":0,"y":0]) }
        pointers.removeAll()
        resignFirstResponder()
    }
    override var canBecomeFirstResponder: Bool { true }
    var hasText: Bool { true }
    var autocorrectionType: UITextAutocorrectionType = .no
    func insertText(_ text: String) { send?(["type":"text","text":text]) }
    func deleteBackward() { send?(["type":"key","code":67]) }
}

private extension UIColor {
    convenience init(hex: String) {
        let value = UInt32(hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")), radix: 16) ?? 0xffffff
        self.init(red: CGFloat((value >> 16) & 255)/255, green: CGFloat((value >> 8) & 255)/255, blue: CGFloat(value & 255)/255, alpha: 1)
    }
    var brightness: CGFloat {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        getRed(&r, green: &g, blue: &b, alpha: &a)
        return r*0.299+g*0.587+b*0.114
    }
}

final class RemoteH264 {
    private var format: CMVideoFormatDescription?
    weak var surface: RemoteSurface?
    private func nals(_ data: Data) -> [Data] {
        let bytes = [UInt8](data); var starts: [(Int, Int)] = []; var i = 0
        while i + 3 < bytes.count {
            if bytes[i] == 0 && bytes[i+1] == 0 && bytes[i+2] == 1 { starts.append((i,i+3)); i += 3 }
            else if i+4 <= bytes.count && bytes[i] == 0 && bytes[i+1] == 0 && bytes[i+2] == 0 && bytes[i+3] == 1 { starts.append((i,i+4)); i += 4 }
            else { i += 1 }
        }
        return starts.enumerated().compactMap { index, start in
            let end = index+1 < starts.count ? starts[index+1].0 : bytes.count
            return start.1 < end ? Data(bytes[start.1..<end]) : nil
        }
    }
    func receive(_ packet: Data) {
        guard packet.count >= 12, packet.count < 8*1024*1024 else { return }
        let flags = packet.prefix(8).reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
        let units = nals(packet.dropFirst(12))
        if flags & (UInt64(1) << 62) != 0 {
            guard let sps = units.first(where: { ($0.first ?? 0) & 31 == 7 }),
                  let pps = units.first(where: { ($0.first ?? 0) & 31 == 8 }) else { return }
            sps.withUnsafeBytes { sb in pps.withUnsafeBytes { pb in
                var pointers = [sb.baseAddress!.assumingMemoryBound(to: UInt8.self), pb.baseAddress!.assumingMemoryBound(to: UInt8.self)]
                var sizes = [sps.count, pps.count]
                _ = CMVideoFormatDescriptionCreateFromH264ParameterSets(allocator: kCFAllocatorDefault, parameterSetCount: 2,
                    parameterSetPointers: &pointers, parameterSetSizes: &sizes, nalUnitHeaderLength: 4, formatDescriptionOut: &format)
            }}
            DispatchQueue.main.async { [weak self] in self?.surface?.video.flushAndRemoveImage() }
            return
        }
        guard let format else { return }
        var avcc = Data()
        for nal in units {
            var length = UInt32(nal.count).bigEndian
            withUnsafeBytes(of: &length) { avcc.append(contentsOf: $0) }; avcc.append(nal)
        }
        guard !avcc.isEmpty else { return }
        var block: CMBlockBuffer?
        guard CMBlockBufferCreateWithMemoryBlock(allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: avcc.count,
             blockAllocator: kCFAllocatorDefault, customBlockSource: nil, offsetToData: 0, dataLength: avcc.count,
             flags: 0, blockBufferOut: &block) == noErr, let block else { return }
        _ = avcc.withUnsafeBytes { CMBlockBufferReplaceDataBytes(with: $0.baseAddress!, blockBuffer: block, offsetIntoDestination: 0, dataLength: avcc.count) }
        var timing = CMSampleTimingInfo(duration: .invalid, presentationTimeStamp: CMTime(value: Int64(flags & ((UInt64(1) << 61)-1)), timescale: 1_000_000), decodeTimeStamp: .invalid)
        var size = avcc.count; var sample: CMSampleBuffer?
        guard CMSampleBufferCreateReady(allocator: kCFAllocatorDefault, dataBuffer: block, formatDescription: format, sampleCount: 1,
             sampleTimingEntryCount: 1, sampleTimingArray: &timing, sampleSizeEntryCount: 1, sampleSizeArray: &size,
             sampleBufferOut: &sample) == noErr, let sample else { return }
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true) as? [NSMutableDictionary] {
            attachments.first?[kCMSampleAttachmentKey_DisplayImmediately as String] = true
        }
        DispatchQueue.main.async { [weak self] in
            guard let layer = self?.surface?.video else { return }
            if layer.status == .failed { layer.flush() }
            layer.enqueue(sample)
        }
    }
}

final class RemoteClient: NSObject, ObservableObject, URLSessionDelegate {
    @Published var status = "Подключите iPhone к той же домашней сети, что и ПК"
    @Published var connected = false
    let surface = RemoteSurface()
    private var pair: RemotePair?
    private var session: URLSession?
    private var socket: URLSessionWebSocketTask?
    private let decoder = RemoteH264()
    private let work = DispatchQueue(label: "pinduo.remote.video")
    private var retry: DispatchWorkItem?
    private var retryCount = 0
    private var wantsConnection = true
    var isPaired: Bool { pair != nil }
    override init() {
        super.init()
        decoder.surface = surface
        surface.send = { [weak self] value in self?.send(value) }
        if let data = Self.loadPair(), let value = try? JSONDecoder().decode(RemotePair.self, from: data), value.valid { pair = value }
    }
    func pairAndConnect(_ text: String) {
        guard let data = text.data(using: .utf8), let value = try? JSONDecoder().decode(RemotePair.self, from: data), value.valid else {
            status = "Это не QR-код подключения Пиндуо"; return
        }
        pair = value; Self.savePair(data); connect()
    }
    func connect() {
        guard let pair else { status = "На ПК откройте «iPhone» и отсканируйте QR-код"; return }
        wantsConnection = true
        closeTransport()
        status = "Подключаюсь к ПК…"
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 12
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        self.session = session
        guard let url = URL(string: "wss://\(pair.host):\(pair.port)/stream") else { return }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(pair.secret)", forHTTPHeaderField: "Authorization")
        let task = session.webSocketTask(with: request)
        socket = task; task.resume(); receive(task)
    }
    func disconnect() {
        wantsConnection = false
        closeTransport()
        status = "Подключение отключено"
    }
    private func closeTransport() {
        retry?.cancel(); retry = nil
        surface.cancelInput()
        socket?.cancel(with: .normalClosure, reason: nil); socket = nil
        session?.invalidateAndCancel(); session = nil; connected = false
    }
    func suspend() { closeTransport() }
    func resume() { if wantsConnection && isPaired && !connected { connect() } }
    func send(_ value: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: value), let text = String(data: data, encoding: .utf8) else { return }
        socket?.send(.string(text)) { _ in }
    }
    private func receive(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self, weak task] result in
            guard let self, let task, self.socket === task else { return }
            switch result {
            case .success(let message):
                switch message {
                case .data(let data): self.work.async { self.decoder.receive(data) }
                case .string(let text):
                    if let data = text.data(using: .utf8), let value = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
                        DispatchQueue.main.async {
                            guard self.socket === task else { return }
                            self.retryCount = 0
                            self.connected = true; self.status = "ПК подключён"
                            self.surface.state(value)
                        }
                    }
                @unknown default: break
                }
                self.receive(task)
            case .failure:
                DispatchQueue.main.async {
                    guard self.socket === task else { return }
                    self.connected = false
                    self.status = "Восстанавливаю связь с ПК…"
                    guard self.wantsConnection else { return }
                    self.retryCount += 1
                    let item = DispatchWorkItem { [weak self] in self?.resume() }
                    self.retry = item
                    DispatchQueue.main.asyncAfter(deadline: .now() + min(8, pow(2, Double(min(self.retryCount,3)))*0.5), execute: item)
                }
            }
        }
    }
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard let pair, challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              challenge.protectionSpace.host == pair.host, challenge.protectionSpace.port == pair.port,
              let trust = challenge.protectionSpace.serverTrust, let certificate = SecTrustGetCertificateAtIndex(trust, 0) else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let data = SecCertificateCopyData(certificate) as Data
        let fingerprint = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard fingerprint == pair.fingerprint else {
            DispatchQueue.main.async { self.wantsConnection = false; self.status = "Сертификат ПК изменился. Отсканируйте новый QR-код." }
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
    private static let pairKey = "pinduo.remote.pair.v1"
    private static func loadPair() -> Data? {
        let query: [String: Any] = [kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:pairKey,kSecReturnData as String:true]
        var value: CFTypeRef?; guard SecItemCopyMatching(query as CFDictionary, &value) == errSecSuccess else { return nil }
        return value as? Data
    }
    private static func savePair(_ data: Data) {
        let query: [String: Any] = [kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:pairKey]
        SecItemDelete(query as CFDictionary)
        var value = query; value[kSecValueData as String] = data
        value[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(value as CFDictionary, nil)
    }
}

private struct RemoteSurfaceView: UIViewRepresentable {
    let client: RemoteClient
    func makeUIView(context: Context) -> RemoteSurface { client.surface }
    func updateUIView(_ view: RemoteSurface, context: Context) {}
}

struct RemoteClientView: View {
    @StateObject private var client = RemoteClient()
    @Environment(\.scenePhase) private var scenePhase
    @State private var scanning = false
    @State private var settings = false
    @State private var tools = false
    @AppStorage("remote.fontScale") private var scale = 1.0
    @AppStorage("remote.autoColors") private var automatic = true
    @State private var textColor = Color.black
    @State private var background = Color.white
    private let accent = Color(red: 0.89, green: 0.16, blue: 0.13)
    var body: some View {
        ZStack(alignment: .topTrailing) {
            Color(.systemBackground).ignoresSafeArea()
            if client.connected {
                RemoteSurfaceView(client: client)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .ignoresSafeArea()
                VStack(alignment: .trailing, spacing: 8) {
                    Button { withAnimation(.easeInOut(duration: 0.15)) { tools.toggle() } } label: {
                        Image(systemName: tools ? "xmark" : "ellipsis")
                            .font(.system(size: 17, weight: .semibold))
                            .frame(width: 42, height: 42)
                    }
                    .background(.regularMaterial, in: Circle())
                    .accessibilityLabel("Управление")
                    if tools {
                        VStack(alignment: .leading, spacing: 0) {
                            tool("Назад", "chevron.left") { client.send(["type":"key","code":4]); tools=false }
                            tool("Ввести текст", "keyboard") { client.surface.becomeFirstResponder(); tools=false }
                            tool("Оригинал / перевод", "character.bubble") {
                                client.surface.original.toggle(); client.surface.rebuildLabels()
                                client.send(["type":"translation","enabled":!client.surface.original]); tools=false
                            }
                            tool("Размер и цвет текста", "textformat.size") { client.surface.cancelInput(); settings=true; tools=false }
                            Divider()
                            tool("Отключиться", "power") { client.disconnect(); tools=false }
                        }
                        .padding(6).frame(width: 245)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
                    }
                }.padding(.trailing, 12).padding(.top, 8)
            } else {
                VStack(alignment: .leading, spacing: 24) {
                    Spacer()
                    Image(systemName: "character.bubble.fill")
                        .font(.system(size: 42, weight: .medium)).foregroundStyle(accent)
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Пиндуо").font(.system(size: 42, weight: .bold, design: .rounded))
                        Text("Товары по-русски").font(.title3).foregroundStyle(.secondary)
                    }
                    Text(client.status).font(.subheadline).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    VStack(spacing: 12) {
                        if client.isPaired {
                            Button { client.connect() } label: {
                                Label("Подключиться к ПК", systemImage: "arrow.right")
                                    .font(.headline).frame(maxWidth: .infinity).padding(.vertical, 10)
                            }.buttonStyle(.borderedProminent)
                        }
                        Button { scanning=true } label: {
                            Label(client.isPaired ? "Подключить другой ПК" : "Сканировать QR-код с ПК", systemImage: "qrcode.viewfinder")
                                .font(.headline).frame(maxWidth: .infinity).padding(.vertical, 10)
                        }.buttonStyle(.bordered)
                    }
                    Text("Телефон и ПК должны быть в одной домашней сети. На ПК откройте переводчик и нажмите «iPhone».")
                        .font(.footnote).foregroundStyle(.secondary)
                    Spacer()
                    Text("0.4 · С ПК").font(.caption).foregroundStyle(.tertiary)
                }.padding(28).frame(maxWidth: 480)
            }
        }
        .tint(accent)
        .statusBarHidden(client.connected)
        .persistentSystemOverlays(client.connected ? .hidden : .automatic)
        .ignoresSafeArea(.keyboard)
        .sheet(isPresented: $scanning) {
            ZStack(alignment: .topTrailing) {
                RemoteQRScanner { value in scanning=false; client.pairAndConnect(value) }.ignoresSafeArea()
                Button("Закрыть") { scanning=false }.buttonStyle(.borderedProminent).padding()
            }
        }
        .sheet(isPresented: $settings) {
            NavigationStack {
                Form {
                    Section("Размер текста") { Slider(value: $scale, in: 0.8...1.4) }
                    Toggle("Подбирать цвет под экран", isOn: $automatic)
                    if !automatic { ColorPicker("Текст", selection: $textColor); ColorPicker("Подложка", selection: $background) }
                }
                .navigationTitle("Оформление").navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("Готово") { settings=false } }
                .onChange(of: scale) { _, _ in applyAppearance() }
                .onChange(of: automatic) { _, _ in applyAppearance() }
                .onChange(of: textColor) { _, _ in applyAppearance() }
                .onChange(of: background) { _, _ in applyAppearance() }
            }.presentationDetents([.medium, .large])
        }
        .onAppear { applyAppearance(); client.resume() }
        .onDisappear { client.surface.cancelInput() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { client.resume() }
            else if phase == .background { tools=false; client.suspend() }
            else { client.surface.cancelInput() }
        }
    }
    private func tool(_ title: String, _ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol).font(.subheadline)
                .frame(maxWidth: .infinity, alignment: .leading).padding(12)
        }.buttonStyle(.plain)
    }
    private func applyAppearance() {
        client.surface.fontScale=scale; client.surface.automaticColors=automatic
        client.surface.textColor=UIColor(textColor); client.surface.backgroundColorForText=UIColor(background)
        client.surface.rebuildLabels()
    }
}

private struct RemoteQRScanner: UIViewControllerRepresentable {
    let found: (String) -> Void
    func makeUIViewController(context: Context) -> QRController { let view = QRController();view.found=found;return view }
    func updateUIViewController(_ controller: QRController, context: Context) {}
    final class QRController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
        var found: ((String)->Void)?
        let capture = AVCaptureSession()
        var preview: AVCaptureVideoPreviewLayer?
        override func viewDidLoad() {
            super.viewDidLoad();view.backgroundColor = .black
            AVCaptureDevice.requestAccess(for:.video) { [weak self] allowed in if allowed { self?.configure() } }
        }
        private func configure() {
            guard let device = AVCaptureDevice.default(for:.video), let input = try? AVCaptureDeviceInput(device:device), capture.canAddInput(input) else { return }
            capture.addInput(input)
            let output = AVCaptureMetadataOutput()
            guard capture.canAddOutput(output) else { return }
            capture.addOutput(output);output.setMetadataObjectsDelegate(self,queue:.main);output.metadataObjectTypes=[.qr]
            DispatchQueue.main.async {
                let layer=AVCaptureVideoPreviewLayer(session:self.capture);layer.videoGravity = .resizeAspectFill
                layer.frame=self.view.bounds;self.view.layer.addSublayer(layer);self.preview=layer
            }
            capture.startRunning()
        }
        override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews();preview?.frame=view.bounds }
        override func viewDidDisappear(_ animated: Bool) { super.viewDidDisappear(animated);DispatchQueue.global().async { self.capture.stopRunning() } }
        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
            guard let value=(objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }
            found?(value);found=nil
        }
    }
}
