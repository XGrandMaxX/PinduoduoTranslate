import ReplayKit
import Vision
import ImageIO

final class SampleHandler: RPBroadcastSampleHandler {
    private let worker = DispatchQueue(label: "pinduo.ocr", qos: .userInitiated)
    private let gate = NSLock()
    private let store = SharedStore()
    private var busy = false
    private var active = false
    private var generation = 0
    private var lastFrameTime: TimeInterval = 0

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        worker.sync {
            gate.lock()
            generation += 1
            active = true
            lastFrameTime = 0
            gate.unlock()
            publish(ScreenSnapshot(state: .reading, message: "Ищу китайский текст…"))
        }
    }

    override func broadcastPaused() { changeState(.paused) }
    override func broadcastFinished() { changeState(.stopped) }

    override func broadcastResumed() {
        worker.sync {
            gate.lock()
            generation += 1
            active = true
            lastFrameTime = 0
            gate.unlock()
            publish(ScreenSnapshot(state: .reading))
        }
    }

    private func changeState(_ state: ScreenSnapshot.State) {
        // Enqueued OCR finishes first; no old result can overwrite the stop marker.
        gate.lock()
        active = false
        generation += 1
        gate.unlock()
        worker.sync { publish(ScreenSnapshot(state: state)) }
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with type: RPSampleBufferType) {
        guard type == .video, let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let now = ProcessInfo.processInfo.systemUptime
        gate.lock()
        guard active, !busy, now - lastFrameTime >= 1.5 else { gate.unlock(); return }
        busy = true
        lastFrameTime = now
        let token = generation
        gate.unlock()

        let attachment = CMGetAttachment(sampleBuffer, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil)
        let orientation = CGImagePropertyOrientation(rawValue: (attachment as? NSNumber)?.uint32Value ?? 1) ?? .up
        worker.async { [self] in
            defer { gate.lock(); busy = false; gate.unlock() }
            autoreleasepool {
                do {
                    let lines = try ChineseOCR.recognize(buffer, orientation: orientation,
                                                         region: store.settings().region)
                    gate.lock()
                    let isCurrent = active && token == generation
                    gate.unlock()
                    if isCurrent { publish(ScreenSnapshot(state: .reading, lines: lines)) }
                } catch {
                    gate.lock()
                    let isCurrent = active && token == generation
                    gate.unlock()
                    if isCurrent { publish(ScreenSnapshot(state: .failed, message: "Не удалось распознать экран: \(error.localizedDescription)")) }
                }
            }
        }
    }

    private func publish(_ snapshot: ScreenSnapshot) {
        do { try store.write(snapshot) }
        catch { finishBroadcastWithError(error) }
    }
}
