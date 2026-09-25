import AppKit
import AVFoundation
import Foundation

// Track the lower envelope even when the initial floor labels ambient sound as
// speech. Updating only on unvoiced frames locks the gate open in a noisy room.
struct AdaptiveVoiceGate {
    let floorDB: Double = -48
    let marginDB: Double = 8
    private var history: [(db: Double, seconds: Double)] = []
    private var historySeconds: Double = 0
    private var refreshSeconds: Double = 0
    private(set) var noiseDB: Double = -65
    var thresholdDB: Double { max(floorDB, noiseDB + marginDB) }

    mutating func isVoiced(db: Double, seconds: Double) -> Bool {
        guard db.isFinite, seconds.isFinite, seconds > 0 else { return false }
        history.append((db, seconds))
        historySeconds += seconds
        refreshSeconds += seconds
        while history.count > 1 && historySeconds - history[0].seconds >= 3 {
            historySeconds -= history.removeFirst().seconds
        }
        if historySeconds >= 0.5 && refreshSeconds >= 0.1 {
            refreshSeconds = 0
            // A low percentile retains the room baseline across short calls and
            // syllable peaks; unlike a mean it does not chase each speech peak.
            let sorted = history.sorted { $0.db < $1.db }
            let target = historySeconds * 0.2
            var elapsed = 0.0
            for frame in sorted {
                elapsed += frame.seconds
                if elapsed >= target { noiseDB = frame.db; break }
            }
        }
        return db > thresholdDB
    }
}

// Bounded recovery of an engine stopped by a device/configuration change.
// The menu's explicit pause, shutdown and fatal controller errors stay final.
struct MicrophoneRecovery {
    private(set) var attempts = 0
    mutating func shouldAttempt(running: Bool, authorized: Bool, paused: Bool,
                               quitting: Bool, failed: Bool) -> Bool {
        if running { attempts = 0; return false }
        guard authorized, !paused, !quitting, !failed, attempts < 3 else { return false }
        attempts += 1
        return true
    }
}

// Audio remains local. A single completed utterance is handed to the controller;
// capture stays suspended until that controller explicitly acknowledges readiness.
final class Listener: NSObject, NSApplicationDelegate {
    private var item: NSStatusItem!
    private var pauseItem: NSMenuItem!
    private var engine = AVAudioEngine()
    private let captureQueue = DispatchQueue(label: "jarvis.capture")
    private let slots = DispatchSemaphore(value: 8)
    private let outputQueue = DispatchQueue(label: "jarvis.controller.output")
    private var child: Process?
    private var inputPipe: Pipe?
    private var outputPipe: Pipe?
    private var outputData = Data()
    private var stateDir = URL(fileURLWithPath: "")
    private var manuallyPaused = false
    private var controllerReady = false
    private var microphoneReady = false
    private var tapInstalled = false
    private var microphoneRecovery = MicrophoneRecovery()
    private var microphoneRecoveryCount = 0
    private var quitting = false
    private var terminating = false
    private var microphoneAuthorized = false
    private var configured = false
    private var statusLabel = "준비 중"
    private var fatalStatus: String?
    private var latestRMS = -120.0
    private var latestPeak = -120.0
    private var meterUpdated = 0.0
    private var statusTimer: Timer?
    private let gateLock = NSLock()
    private var gateEpoch: UInt64 = 0
    private var gateEnabled = false
    private var lastMeterEmission = 0.0
    private let converterLock = NSLock()
    private var activeConverter: Process?
    private var converterShuttingDown = false
    private var pendingWav: URL?
    // Only captureQueue accesses these values.
    private var accepting = false
    private var sampleRate: Double = 48000
    private var preRoll: [Float] = []
    private var samples: [Float] = []
    private var voicedSeconds = 0.0
    private var silentSeconds = 0.0
    private var speechStarted = 0.0
    private var speechEnded = 0.0
    private var voiceGate = AdaptiveVoiceGate()
    private var latestNoiseDB = -65.0
    private var latestThresholdDB = -48.0
    private let voiceFloorDB = -48.0
    private let voiceNoiseMarginDB = 8.0
    private let minimumVoiceSeconds = 0.18
    private let preRollSeconds = 0.35

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        let title = NSMenuItem(title: "Jarvis · 준비 중", action: nil, keyEquivalent: "")
        title.tag = 1
        menu.addItem(title)
        pauseItem = NSMenuItem(title: "마이크 일시 정지", action: #selector(togglePause), keyEquivalent: "")
        pauseItem.target = self
        menu.addItem(pauseItem)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Jarvis 종료", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
        status("준비 중")
        do { try launchController() } catch { fail("설정/컨트롤러 오류"); return }
        statusTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            self.recoverMicrophoneIfNeeded()
            self.writeStatus()
        }
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async {
                self.microphoneAuthorized = granted
                guard granted else { self.fail("마이크 권한 필요"); return }
                do { try self.startMicrophone() } catch { self.fail("마이크 시작 실패") }
            }
        }
    }

    private func status(_ text: String) {
        statusLabel = text
        item.button?.title = "🎙 " + text
        item.menu?.item(withTag: 1)?.title = "Jarvis · " + text
        writeStatus()
    }
    private func writeStatus() {
        guard configured else { return }
        let snapshot: [String: Any] = ["microphone_authorized": microphoneAuthorized,
            "engine_running": engine.isRunning, "manual_paused": manuallyPaused,
            "controller_ready": controllerReady, "status": statusLabel,
            "updated_wall": Date().timeIntervalSince1970, "sample_rate": sampleRate,
            "rms_db": latestRMS, "peak_db": latestPeak, "meter_updated_wall": meterUpdated,
            "voice_floor_db": voiceFloorDB, "voice_noise_margin_db": voiceNoiseMarginDB,
            "noise_db": latestNoiseDB, "voice_threshold_db": latestThresholdDB,
            "microphone_recovery_count": microphoneRecoveryCount,
            "minimum_voice_seconds": minimumVoiceSeconds, "pre_roll_seconds": preRollSeconds]
        if let bytes = try? JSONSerialization.data(withJSONObject: snapshot, options: [.sortedKeys]) {
            let path = stateDir.appendingPathComponent("native-status.json")
            try? bytes.write(to: path, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
        }
    }
    private func setGate(_ enabled: Bool) {
        gateLock.lock()
        gateEpoch &+= 1
        gateEnabled = enabled
        gateLock.unlock()
    }
    private func gateSnapshot() -> (UInt64, Bool) {
        gateLock.lock()
        defer { gateLock.unlock() }
        return (gateEpoch, gateEnabled)
    }
    private func fail(_ text: String) {
        fatalStatus = text
        engine.stop()
        controllerReady = false
        updateCapture()
        status(text)
    }
    @objc private func togglePause() {
        manuallyPaused.toggle()
        pauseItem.title = manuallyPaused ? "마이크 다시 듣기" : "마이크 일시 정지"
        if manuallyPaused {
            setGate(false)
            engine.stop()
        } else if microphoneReady && fatalStatus == nil {
            do { try rebuildMicrophone() }
            catch { fail("마이크 재시작 실패"); return }
        }
        updateCapture()
    }
    private func updateCapture() {
        let enabled = microphoneReady && engine.isRunning && controllerReady && !manuallyPaused && !quitting && fatalStatus == nil
        setGate(enabled)
        captureQueue.async {
            self.accepting = enabled
            self.resetSegment()
        }
        status(fatalStatus ?? (manuallyPaused ? "마이크 꺼짐" : (enabled ? "호출 대기" : (!microphoneReady ? "마이크 준비 중" : "처리 중"))))
    }
    private func resetSegment() {
        preRoll.removeAll(keepingCapacity: true)
        samples.removeAll(keepingCapacity: true)
        voicedSeconds = 0
        silentSeconds = 0
        speechStarted = 0
        speechEnded = 0
    }

    private func launchController() throws {
        let base = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/JarvisMacOSS")
        let raw = try Data(contentsOf: base.appendingPathComponent("config.json"))
        guard let config = try JSONSerialization.jsonObject(with: raw) as? [String: Any],
              let python = config["python"] as? String, let controller = config["controller"] as? String,
              let dir = config["state_dir"] as? String, python.hasPrefix("/"), controller.hasPrefix("/"), dir.hasPrefix("/")
        else { throw NSError(domain: "JarvisConfig", code: 1) }
        stateDir = URL(fileURLWithPath: dir)
        configured = true
        let audio = stateDir.appendingPathComponent("audio", isDirectory: true)
        try FileManager.default.createDirectory(at: audio, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: audio.path)
        let process = Process()
        let input = Pipe(), output = Pipe()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [controller, "--state-dir", dir]
        process.standardInput = input
        process.standardOutput = output
        // Controller owns sanitized diagnostics. Never persist raw transcripts here.
        process.standardError = FileHandle.nullDevice
        inputPipe = input
        outputPipe = output
        child = process
        output.fileHandleForReading.readabilityHandler = { handle in
            let bytes = handle.availableData
            self.outputQueue.async {
                if bytes.isEmpty {
                    handle.readabilityHandler = nil
                    DispatchQueue.main.async { if !self.quitting { self.fail("컨트롤러 연결 종료") } }
                    return
                }
                self.outputData.append(bytes)
                guard self.outputData.count < 65536 else {
                    self.outputData.removeAll()
                    DispatchQueue.main.async { self.fail("컨트롤러 응답 오류") }
                    return
                }
                while let newline = self.outputData.firstIndex(of: 10) {
                    let line = Data(self.outputData[..<newline])
                    self.outputData.removeSubrange(...newline)
                    guard let event = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else { continue }
                    DispatchQueue.main.async {
                        guard !self.quitting else { return }
                        if let listen = event["listen"] as? Bool {
                            if listen { self.removePendingWav() }
                            self.controllerReady = listen
                            self.updateCapture()
                        }
                        if let state = event["state"] as? String, !self.manuallyPaused, self.microphoneReady, self.fatalStatus == nil {
                            // Only short status labels, never model output or recognized speech.
                            let labels = ["ready": "호출 대기", "listening": "호출 대기", "processing": "처리 중",
                                          "speaking": "응답 재생", "error": "오류", "waiting_question": "질문 대기",
                                          "preparing": "준비 중", "recognizing": "음성 인식 중",
                                          "answering": "답변 생성 중", "armed": "질문 대기", "stopped": "중지됨"]
                            if let label = labels[state] { self.status(label) }
                        }
                    }
                }
            }
        }
        process.terminationHandler = { _ in
            DispatchQueue.main.async { if !self.quitting { self.fail("컨트롤러 종료됨") } }
        }
        try process.run()
    }

    private func recoverMicrophoneIfNeeded() {
        guard microphoneRecovery.shouldAttempt(running: engine.isRunning,
            authorized: microphoneAuthorized, paused: manuallyPaused,
            quitting: quitting, failed: fatalStatus != nil) else {
            if !engine.isRunning && microphoneAuthorized && !manuallyPaused && !quitting
                && fatalStatus == nil && microphoneRecovery.attempts >= 3 {
                fail("마이크 재연결 실패")
            }
            return
        }
        status("마이크 다시 연결 중")
        do {
            try rebuildMicrophone()
            microphoneRecoveryCount += 1
        } catch {
            if microphoneRecovery.attempts >= 3 { fail("마이크 재연결 실패") }
        }
    }

    private func rebuildMicrophone() throws {
        setGate(false)
        engine.stop()
        if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
        microphoneReady = false
        // Drain old-format buffers before replacing the sample rate and tap.
        captureQueue.sync {
            self.accepting = false
            self.resetSegment()
            self.voiceGate = AdaptiveVoiceGate()
        }
        // reset() retains the old client format (e.g. Bluetooth 24 kHz).
        // A fresh engine negotiates the newly selected device's hardware format.
        engine = AVAudioEngine()
        try startMicrophone()
    }

    private func startMicrophone() throws {
        let input = engine.inputNode
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { throw NSError(domain: "JarvisMic", code: 1) }
        sampleRate = format.sampleRate
        input.installTap(onBus: 0, bufferSize: 1024, format: nil) { buffer, _ in
            guard self.slots.wait(timeout: .now()) == .success else { return }
            let (epoch, eligible) = self.gateSnapshot()
            guard let channels = buffer.floatChannelData else { self.slots.signal(); return }
            let count = Int(buffer.frameLength)
            var mono = [Float](repeating: 0, count: count)
            for channel in 0..<Int(buffer.format.channelCount) {
                for index in 0..<count { mono[index] += channels[channel][index] / Float(buffer.format.channelCount) }
            }
            let ended = Date().timeIntervalSince1970
            self.captureQueue.async {
                defer { self.slots.signal() }
                self.consume(mono, ended: ended, epoch: epoch, eligible: eligible)
            }
        }
        tapInstalled = true
        engine.prepare()
        if !manuallyPaused { try engine.start() }
        microphoneReady = true
        updateCapture()
    }

    private func consume(_ chunk: [Float], ended: Double, epoch: UInt64, eligible: Bool) {
        guard !chunk.isEmpty else { return }
        let duration = Double(chunk.count) / sampleRate
        let power = chunk.reduce(0.0) { $0 + Double($1 * $1) } / Double(chunk.count)
        let db = 10 * log10(max(power, 1e-12))
        let voiced = voiceGate.isVoiced(db: db, seconds: duration)
        let noiseDB = voiceGate.noiseDB, thresholdDB = voiceGate.thresholdDB
        if ended - lastMeterEmission >= 1 {
            lastMeterEmission = ended
            let peak = 20 * log10(max(Double(chunk.map { abs($0) }.max() ?? 0), 1e-6))
            DispatchQueue.main.async {
                self.latestRMS = db
                self.latestPeak = peak
                self.meterUpdated = ended
                self.latestNoiseDB = noiseDB
                self.latestThresholdDB = thresholdDB
            }
        }
        let (currentEpoch, enabled) = gateSnapshot()
        guard eligible, enabled, epoch == currentEpoch, accepting else { return }
        if samples.isEmpty {
            if !voiced {
                preRoll.append(contentsOf: chunk)
                let limit = Int(sampleRate * preRollSeconds)
                if preRoll.count > limit { preRoll.removeFirst(preRoll.count - limit) }
                return
            }
            speechStarted = ended - duration
            samples = preRoll
            preRoll.removeAll(keepingCapacity: true)
        }
        samples.append(contentsOf: chunk)
        if voiced { voicedSeconds += duration; silentSeconds = 0; speechEnded = ended }
        else { silentSeconds += duration }
        guard silentSeconds >= 0.8 || Double(samples.count) / sampleRate >= 12 else { return }
        guard voicedSeconds >= minimumVoiceSeconds else { resetSegment(); return }
        accepting = false
        setGate(false)
        let captured = samples, started = speechStarted, lastVoice = speechEnded
        resetSegment()
        DispatchQueue.main.async { self.controllerReady = false; self.status("음성 인식 중") }
        var writtenWav: URL?
        do {
            let wav = try writeWav(captured)
            writtenWav = wav
            converterLock.lock()
            let shuttingDown = converterShuttingDown
            pendingWav = wav
            converterLock.unlock()
            guard !shuttingDown, let pipe = inputPipe else { throw NSError(domain: "JarvisAudio", code: 3) }
            let message: [String: Any] = ["wav": wav.path, "speech_started_wall": started,
                                          "speech_ended_wall": lastVoice, "capture_ended_wall": ended]
            var bytes = try JSONSerialization.data(withJSONObject: message)
            bytes.append(10)
            try pipe.fileHandleForWriting.write(contentsOf: bytes)
        } catch {
            if let wav = writtenWav { try? FileManager.default.removeItem(at: wav) }
            DispatchQueue.main.async { if !self.quitting { self.fail("음성 전달 실패") } }
        }
    }

    private func writeWav(_ data: [Float]) throws -> URL {
        let base = stateDir.appendingPathComponent("audio").appendingPathComponent(UUID().uuidString)
        let caf = base.appendingPathExtension("caf"), wav = base.appendingPathExtension("wav")
        var completed = false
        defer {
            try? FileManager.default.removeItem(at: caf)
            if !completed { try? FileManager.default.removeItem(at: wav) }
        }
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate,
                                         channels: 1, interleaved: false),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(data.count))
        else { throw NSError(domain: "JarvisAudio", code: 1) }
        buffer.frameLength = AVAudioFrameCount(data.count)
        data.withUnsafeBufferPointer { source in
            buffer.floatChannelData![0].update(from: source.baseAddress!, count: data.count)
        }
        // Private directory and restrictive process umask cover creation as well as chmod.
        do {
            let file = try AVAudioFile(forWriting: caf, settings: format.settings)
            try file.write(from: buffer)
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: caf.path)
        let converter = Process()
        converter.executableURL = URL(fileURLWithPath: "/usr/bin/afconvert")
        converter.arguments = ["-f", "WAVE", "-d", "LEI16@16000", "-c", "1", caf.path, wav.path]
        converter.standardOutput = FileHandle.nullDevice
        converter.standardError = FileHandle.nullDevice
        // Serialize launch with Quit so a converter cannot appear after shutdown.
        converterLock.lock()
        guard !converterShuttingDown else {
            converterLock.unlock()
            throw NSError(domain: "JarvisAudio", code: 3)
        }
        do { try converter.run(); activeConverter = converter }
        catch { converterLock.unlock(); throw error }
        converterLock.unlock()
        defer {
            converterLock.lock()
            activeConverter = nil
            converterLock.unlock()
        }
        if !waitForExit(converter, seconds: 5) {
            stopOwnedProcess(converter)
            throw NSError(domain: "JarvisAudio", code: 4)
        }
        guard converter.terminationStatus == 0 else {
            try? FileManager.default.removeItem(at: wav)
            throw NSError(domain: "JarvisAudio", code: 2)
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: wav.path)
        completed = true
        return wav
    }

    private func removePendingWav() {
        converterLock.lock()
        let wav = pendingWav
        pendingWav = nil
        converterLock.unlock()
        if let wav = wav { try? FileManager.default.removeItem(at: wav) }
    }
    private func waitForExit(_ process: Process, seconds: Double) -> Bool {
        let deadline = ProcessInfo.processInfo.systemUptime + seconds
        while process.isRunning && ProcessInfo.processInfo.systemUptime < deadline {
            Thread.sleep(forTimeInterval: 0.02)
        }
        if !process.isRunning { process.waitUntilExit(); return true }
        return false
    }
    private func stopOwnedProcess(_ process: Process) {
        if process.isRunning { process.terminate() }
        if !waitForExit(process, seconds: 3), process.isRunning {
            kill(process.processIdentifier, SIGKILL)
            _ = waitForExit(process, seconds: 2)
        }
    }

    @objc private func quitApp() { NSApp.terminate(nil) }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminating { return .terminateNow }
        quitting = true
        setGate(false)
        statusTimer?.invalidate()
        status("종료 중")
        engine.stop()
        writeStatus()
        converterLock.lock()
        converterShuttingDown = true
        let converter = activeConverter
        converterLock.unlock()
        if let converter = converter, converter.isRunning { converter.terminate() }
        let captureStopped = DispatchSemaphore(value: 0)
        captureQueue.async {
            self.accepting = false
            self.resetSegment()
            try? self.inputPipe?.fileHandleForWriting.close()
            captureStopped.signal()
        }
        // Shutdown runs off the UI thread, bounded even for an unresponsive child.
        DispatchQueue.global(qos: .userInitiated).async {
            if let converter = converter { self.stopOwnedProcess(converter) }
            _ = captureStopped.wait(timeout: .now() + 6)
            if let process = self.child, !self.waitForExit(process, seconds: 2) {
                self.stopOwnedProcess(process)
            }
            self.removePendingWav()
            self.outputPipe?.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                self.terminating = true
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
        return .terminateLater
    }
}

#if !VAD_TEST
umask(0o077)
signal(SIGPIPE, SIG_IGN)
let application = NSApplication.shared
let delegate = Listener()
application.delegate = delegate
application.run()

#endif
