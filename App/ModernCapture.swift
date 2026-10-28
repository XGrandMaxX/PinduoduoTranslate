import SwiftUI
import ScreenCaptureKit
import CoreMedia

@MainActor
final class CaptureCoordinator: ObservableObject {
    @Published private(set) var status: String?
    @Published private(set) var isCapturing = false
    private var implementation: AnyObject?

    func start() {
        if #available(iOS 27.0, *) {
            if implementation == nil {
                implementation = ModernCapture { [weak self] status, active in
                    self?.status = status
                    self?.isCapturing = active
                }
            }
            (implementation as? ModernCapture)?.presentPicker()
        }
    }

    func stop() {
        if #available(iOS 27.0, *) {
            Task { await (implementation as? ModernCapture)?.stop() }
        }
    }
}

@available(iOS 27.0, *)
@MainActor
private final class ModernCapture: NSObject, SCContentSharingPickerObserver, SCStreamOutput, SCStreamDelegate {
    private let queue = DispatchQueue(label: "pinduo.modern-ocr", qos: .userInitiated)
    private let store = SharedStore()
    private let onStatus: @MainActor (String, Bool) -> Void
    private var capture: SCStream?
    private var busy = false
    private var generation = 0
    private var lastFrameTime: TimeInterval = 0

    init(onStatus: @escaping @MainActor (String, Bool) -> Void) {
        self.onStatus = onStatus
        super.init()
        SCContentSharingPicker.shared.add(self)
    }

    func presentPicker() {
        let picker = SCContentSharingPicker.shared
        var configuration = SCContentSharingPickerConfiguration()
        configuration.showsMicrophoneControl = false
        picker.defaultConfiguration = configuration
        picker.isActive = true
        picker.present()
    }

    func stop() async {
        generation += 1
        let old = capture
        capture = nil
        do {
            try await old?.stopCapture()
            try store.write(ScreenSnapshot(state: .stopped))
            onStatus("Захват экрана завершён", false)
        } catch { onStatus(error.localizedDescription, false) }
    }

    nonisolated func contentSharingPicker(_ picker: SCContentSharingPicker,
                                         didUpdateWith filter: SCContentFilter, for stream: SCStream?) {
        Task { @MainActor [weak self] in await self?.begin(filter: filter) }
    }

    nonisolated func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            onStatus("Выбор экрана отменён", capture != nil)
        }
    }

    nonisolated func contentSharingPickerStartDidFailWithError(_ error: Error) {
        Task { @MainActor [weak self] in self?.onStatus(error.localizedDescription, self?.capture != nil) }
    }

    private func begin(filter: SCContentFilter) async {
        await stop()
        generation += 1
        let token = generation
        do {
            let config = SCStreamConfiguration()
            config.width = 828
            config.height = 1792
            config.capturesAudio = false
            let stream = SCStream(filter: filter, configuration: config, delegate: self)
            try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: .main)
            capture = stream
            lastFrameTime = 0
            try await stream.startCapture()
            guard token == generation else { try? await stream.stopCapture(); return }
            try store.write(ScreenSnapshot(state: .reading))
            onStatus("Захват экрана включён", true)
        } catch {
            guard token == generation else { return }
            capture = nil
            try? store.write(ScreenSnapshot(state: .failed, message: error.localizedDescription))
            onStatus(error.localizedDescription, false)
        }
    }

    nonisolated func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                            of type: SCStreamOutputType) {
        guard type == .screen, let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        Task { @MainActor [weak self] in self?.recognize(buffer, from: stream) }
    }

    private func recognize(_ buffer: CVPixelBuffer, from stream: SCStream) {
        let now = ProcessInfo.processInfo.systemUptime
        guard stream === capture, !busy, now - lastFrameTime >= 1.5 else { return }
        busy = true
        lastFrameTime = now
        let token = generation
        let sharedStore = store
        queue.async { [weak self] in
            let snapshot: ScreenSnapshot = autoreleasepool {
                do {
                    let lines = try ChineseOCR.recognize(buffer, orientation: .up, region: sharedStore.settings().region)
                    return ScreenSnapshot(state: .reading, lines: lines)
                } catch { return ScreenSnapshot(state: .failed, message: error.localizedDescription) }
            }
            Task { @MainActor [weak self] in
                guard let self else { return }
                busy = false
                guard token == generation, capture != nil else { return }
                do { try sharedStore.write(snapshot) }
                catch { onStatus(error.localizedDescription, true) }
            }
        }
    }

    nonisolated func stream(_ stream: SCStream, didStopWithError error: Error) {
        Task { @MainActor [weak self] in
            guard let self, stream === capture else { return }
            generation += 1
            capture = nil
            try? store.write(ScreenSnapshot(state: .failed, message: error.localizedDescription))
            onStatus(error.localizedDescription, false)
        }
    }
}
