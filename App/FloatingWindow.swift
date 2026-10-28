import AVKit
import UIKit
import CoreMedia

final class VideoSurface: UIView {
    override class var layerClass: AnyClass { AVSampleBufferDisplayLayer.self }
    var videoLayer: AVSampleBufferDisplayLayer { layer as! AVSampleBufferDisplayLayer }
}

private final class PlaybackState: @unchecked Sendable {
    private let lock = NSLock()
    private var paused = false
    var isPaused: Bool {
        get { lock.lock(); defer { lock.unlock() }; return paused }
        set { lock.lock(); paused = newValue; lock.unlock() }
    }
}

@MainActor
final class FloatingWindow: NSObject, ObservableObject {
    @Published private(set) var isActive = false
    @Published private(set) var isPossible = false
    @Published private(set) var error: String?
    let surface = VideoSurface()
    private var controller: AVPictureInPictureController?
    private var possibleObservation: NSKeyValueObservation?
    private var timer: Timer?
    private weak var engine: TranslationEngine?
    private var frameNumber: Int64 = 0
    private nonisolated let playbackState = PlaybackState()

    func configure(engine: TranslationEngine) {
        guard controller == nil else { return }
        self.engine = engine
        guard AVPictureInPictureController.isPictureInPictureSupported() else {
            error = "Система не поддерживает плавающее окно"
            return
        }
        do {
            let audio = AVAudioSession.sharedInstance()
            try audio.setCategory(.playback, mode: .moviePlayback, options: [.mixWithOthers])
            try audio.setActive(true)
        } catch {
            self.error = "Не удалось подготовить окно: \(error.localizedDescription)"
            return
        }
        // Prevent the translated window from being recognized again in the broadcast.
        surface.videoLayer.preventsCapture = true
        surface.videoLayer.videoGravity = .resizeAspect
        var timebase: CMTimebase?
        CMTimebaseCreateWithSourceClock(allocator: kCFAllocatorDefault,
                                       sourceClock: CMClockGetHostTimeClock(), timebaseOut: &timebase)
        if let timebase {
            CMTimebaseSetTime(timebase, time: .zero)
            CMTimebaseSetRate(timebase, rate: 1)
            surface.videoLayer.controlTimebase = timebase
        }
        let source = AVPictureInPictureController.ContentSource(
            sampleBufferDisplayLayer: surface.videoLayer, playbackDelegate: self)
        let pip = AVPictureInPictureController(contentSource: source)
        pip.delegate = self
        pip.canStartPictureInPictureAutomaticallyFromInline = true
        controller = pip
        possibleObservation = pip.observe(\.isPictureInPicturePossible, options: [.initial, .new]) { [weak self] pip, _ in
            let possible = pip.isPictureInPicturePossible
            Task { @MainActor in self?.isPossible = possible }
        }
        render()
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.render() }
        }
    }

    func start() {
        error = nil
        guard isPossible else {
            error = "Окно ещё готовится. Подождите секунду и нажмите снова"
            return
        }
        controller?.startPictureInPicture()
    }

    func stop() { controller?.stopPictureInPicture() }

    private func render() {
        guard let engine else { return }
        playbackState.isPaused = engine.isPaused
        // Fixed, modest resolution bounds memory and remains readable in PiP.
        let width = 600, height = 840
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: CGFloat(width), height: CGFloat(height)), format: format)
        let image = renderer.image { context in
            UIColor(red: 0.055, green: 0.07, blue: 0.10, alpha: 1).setFill()
            context.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
            draw("ПЕРЕВОД · 中文 → РУ", y: 20, size: 22, color: .systemMint, height: 30)
            draw(engine.status, y: 58, size: 22, color: .lightGray, height: 62)
            var y: CGFloat = 132
            if engine.rows.isEmpty {
                draw("Откройте Pinduoduo.\nПосле прокрутки остановитесь,\nчтобы прочитать перевод.",
                     y: y, size: 30, color: .white, height: 220)
            } else {
                let visible = Array(engine.rows.prefix(6))
                for row in visible {
                    let suffix = row.confidence < 0.65 ? "  [?]" : ""
                    draw(row.target + suffix, y: y, size: 30, color: .white, height: 64)
                    draw(row.source, y: y + 65, size: 19, color: .lightGray, height: 30)
                    y += 108
                }
                if engine.rows.count > visible.count {
                    draw("Ещё \(engine.rows.count - visible.count) строк — в приложении",
                         y: 792, size: 21, color: .systemMint, height: 30)
                }
            }
        }
        guard let cgImage = image.cgImage else { return }
        var pixelBuffer: CVPixelBuffer?
        let attributes: [CFString: Any] = [
            kCVPixelBufferCGImageCompatibilityKey: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey: true,
            kCVPixelBufferIOSurfacePropertiesKey: [:]
        ]
        guard CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA,
                                  attributes as CFDictionary, &pixelBuffer) == kCVReturnSuccess,
              let pixelBuffer else { return }
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        let context = CGContext(data: CVPixelBufferGetBaseAddress(pixelBuffer), width: width, height: height,
                                bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(pixelBuffer),
                                space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        context?.draw(cgImage, in: CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
        CVPixelBufferUnlockBaseAddress(pixelBuffer, [])
        var description: CMVideoFormatDescription?
        guard CMVideoFormatDescriptionCreateForImageBuffer(allocator: kCFAllocatorDefault,
              imageBuffer: pixelBuffer, formatDescriptionOut: &description) == noErr,
              let description else { return }
        frameNumber += 1
        var timing = CMSampleTimingInfo(duration: CMTime(value: 1, timescale: 2),
                                       presentationTimeStamp: CMTime(value: frameNumber, timescale: 2),
                                       decodeTimeStamp: .invalid)
        var sample: CMSampleBuffer?
        guard CMSampleBufferCreateReadyWithImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: pixelBuffer,
              formatDescription: description, sampleTiming: &timing, sampleBufferOut: &sample) == noErr,
              let sample else { return }
        if let array = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true) as? [NSMutableDictionary],
           let attachment = array.first {
            attachment[kCMSampleAttachmentKey_DisplayImmediately as String] = true
        }
        if surface.videoLayer.status == .failed { surface.videoLayer.flush() }
        if surface.videoLayer.isReadyForMoreMediaData { surface.videoLayer.enqueue(sample) }
    }

    private func draw(_ text: String, y: CGFloat, size: CGFloat, color: UIColor, height: CGFloat) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byTruncatingTail
        (text as NSString).draw(in: CGRect(x: 22, y: y, width: 556, height: height), withAttributes: [
            .font: UIFont.systemFont(ofSize: size, weight: .medium),
            .foregroundColor: color, .paragraphStyle: paragraph
        ])
    }
}

extension FloatingWindow: AVPictureInPictureControllerDelegate {
    nonisolated func pictureInPictureControllerDidStartPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        Task { @MainActor [weak self] in self?.isActive = true }
    }
    nonisolated func pictureInPictureControllerDidStopPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        Task { @MainActor [weak self] in self?.isActive = false }
    }
    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController,
                                   failedToStartPictureInPictureWithError error: Error) {
        let message = error.localizedDescription
        Task { @MainActor [weak self] in self?.error = message }
    }
    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController,
        restoreUserInterfaceForPictureInPictureStopWithCompletionHandler completionHandler: @escaping (Bool) -> Void) {
        completionHandler(true)
    }
}

extension FloatingWindow: AVPictureInPictureSampleBufferPlaybackDelegate {
    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController, setPlaying playing: Bool) {
        playbackState.isPaused = !playing
        Task { @MainActor [weak self] in self?.engine?.isPaused = !playing }
    }
    nonisolated func pictureInPictureControllerTimeRangeForPlayback(_ pictureInPictureController: AVPictureInPictureController) -> CMTimeRange {
        CMTimeRange(start: .zero, duration: .positiveInfinity)
    }
    nonisolated func pictureInPictureControllerIsPlaybackPaused(_ pictureInPictureController: AVPictureInPictureController) -> Bool {
        playbackState.isPaused
    }
    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController,
                                   didTransitionToRenderSize newRenderSize: CMVideoDimensions) {}
    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController,
                                   skipByInterval skipInterval: CMTime, completion completionHandler: @escaping @Sendable () -> Void) {
        completionHandler()
    }
}
