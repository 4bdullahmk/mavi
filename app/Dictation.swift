import SwiftUI
import AVFoundation

@MainActor final class DictationState: NSObject, ObservableObject {
    @Published private(set) var recording = false
    @Published private(set) var busy = false
    @Published private(set) var permissionPending = false
    @Published var status = "Ready to dictate"
    @Published private(set) var recordingSeconds: TimeInterval = 0

    var onTranscription: ((String) -> Void)?
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var startedAt: Date?
    private var recordingURL: URL?
    private var process: Process?
    private var activeID = UUID()
    private var permissionID = UUID()

    func toggle() {
        if recording { stop() } else { start() }
    }

    func start() {
        guard !recording, !busy, !permissionPending else { return }
        let requestID = UUID()
        permissionID = requestID
        permissionPending = true
        status = "Requesting microphone access…"
        AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
            Task { @MainActor in
                guard let self else { return }
                guard self.permissionPending, self.permissionID == requestID else { return }
                self.permissionPending = false
                guard granted else { self.status = "Microphone access was denied. Enable it in System Settings > Privacy & Security > Microphone."; return }
                self.beginRecording()
            }
        }
    }

    private func beginRecording() {
        do {
            let folder = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
                .appendingPathComponent("Mavi/Dictation", isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let url = folder.appendingPathComponent("dictation-\(UUID().uuidString).wav")
            recordingURL = url
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: 16_000,
                AVNumberOfChannelsKey: 1,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false
            ]
            let audioRecorder = try AVAudioRecorder(url: url, settings: settings)
            audioRecorder.delegate = self
            guard audioRecorder.record() else { throw DictationError.recordingFailed }
            recorder = audioRecorder
            recordingURL = url
            startedAt = Date()
            recordingSeconds = 0
            recording = true
            status = "Recording…"
            timer?.invalidate()
            timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    guard let self, let start = self.startedAt else { return }
                    self.recordingSeconds = Date().timeIntervalSince(start)
                }
            }
        } catch {
            removeRecording()
            status = "Could not start recording: \(error.localizedDescription)"
        }
    }

    func stop() {
        guard recording else { return }
        timer?.invalidate(); timer = nil
        recording = false
        recorder?.stop()
        recorder = nil
        startedAt = nil
        guard let url = recordingURL else { status = "Recording file is missing."; return }
        guard FileManager.default.fileExists(atPath: url.path) else { removeRecording(); status = "Recording file was not created."; return }
        transcribe(url)
    }

    func cancel() {
        activeID = UUID()
        permissionID = UUID()
        permissionPending = false
        timer?.invalidate(); timer = nil
        recording = false
        busy = false
        recorder?.stop()
        recorder = nil
        startedAt = nil
        if process?.isRunning == true { process?.terminate() }
        process = nil
        removeRecording()
        status = "Dictation cancelled"
    }

    private func transcribe(_ url: URL) {
        guard let script = Bundle.main.url(forResource: "Dictation", withExtension: "py") else {
            removeRecording(); status = "Dictation helper is missing from the app bundle."; return
        }
        let python = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Mavi/Runtime/personal-venv/bin/python").path
        guard FileManager.default.isExecutableFile(atPath: python) else {
            removeRecording(); status = "Dictation Python runtime is missing."; return
        }
        let id = UUID()
        activeID = id
        busy = true
        status = "Transcribing offline…"
        let task = Process()
        let pipe = Pipe()
        task.executableURL = URL(fileURLWithPath: python)
        task.arguments = [script.path, url.path]
        task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice
        do {
            try task.run()
            process = task
        } catch {
            busy = false
            process = nil
            removeRecording()
            status = "Could not launch offline transcription: \(error.localizedDescription)"
            return
        }

        Task {
            let output = await Task.detached {
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                task.waitUntilExit()
                return data
            }.value
            guard activeID == id else { return }
            process = nil
            busy = false
            removeRecording()
            guard task.terminationStatus == 0 else {
                let response = (try? JSONSerialization.jsonObject(with: output)) as? [String: String]
                status = response?["error"] ?? "Offline transcription failed (exit \(task.terminationStatus))."
                return
            }
            guard let response = (try? JSONSerialization.jsonObject(with: output)) as? [String: Any],
                  let text = response["text"] as? String, !text.isEmpty else {
                status = "Transcription returned an invalid response."
                return
            }
            status = String(format: "Dictation ready (%.1f s)", response["seconds"] as? Double ?? 0)
            onTranscription?(text)
        }
    }

    private func removeRecording() {
        if let recordingURL { try? FileManager.default.removeItem(at: recordingURL) }
        recordingURL = nil
    }
}

extension DictationState: AVAudioRecorderDelegate {
    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor in
            guard self.recorder === recorder else { return }
            self.recording = false
            self.timer?.invalidate(); self.timer = nil
            self.recorder = nil
            self.removeRecording()
            self.status = "Recording failed: \(error?.localizedDescription ?? "audio encoding error")"
        }
    }
}

private enum DictationError: LocalizedError {
    case recordingFailed
    var errorDescription: String? { "The audio recorder could not begin recording." }
}

struct DictationButton: View {
    @Binding var target: String
    var compact = false
    @StateObject private var state = DictationState()

    var body: some View {
        Group {
            if compact {
                HStack(spacing: 5) {
                    Button(action: toggleRecording) {
                        Image(systemName: state.recording ? "stop.fill" : "mic")
                            .frame(width: 26, height: 24)
                    }
                    .buttonStyle(.borderless).controlSize(.regular)
                    .disabled(state.busy || state.permissionPending)
                    .help(state.status)
                    .accessibilityLabel(state.recording ? "Stop dictation" : (state.permissionPending ? "Checking microphone" : "Dictate"))

                    if state.recording { Text(String(format: "%.1f s", state.recordingSeconds)).font(.caption.monospacedDigit()) }
                    if state.busy { ProgressView().controlSize(.small) }
                    if state.recording || state.permissionPending || state.busy {
                        Button { state.cancel() } label: { Image(systemName: "xmark") }
                            .buttonStyle(.borderless).controlSize(.small).help("Cancel dictation")
                    }
                }
            } else {
                HStack(spacing: 8) {
                    Button(action: toggleRecording) {
                        Label(state.recording ? "Stop dictation" : (state.permissionPending ? "Checking microphone…" : "Dictate"), systemImage: state.recording ? "stop.circle.fill" : "mic")
                    }
                    .disabled(state.busy || state.permissionPending)
                    if state.recording { Text(String(format: "%.1f s", state.recordingSeconds)).monospacedDigit() }
                    if state.busy { ProgressView().controlSize(.small) }
                    if state.recording || state.permissionPending || state.busy { Button("Cancel") { state.cancel() } }
                    Text(state.status).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
        }
        .onAppear { state.onTranscription = { target += ($0.isEmpty || target.isEmpty ? "" : " ") + $0 } }
        .onChange(of: target) { _,_ in state.onTranscription = { target += (target.isEmpty ? "" : " ") + $0 } }
        .onDisappear { state.cancel() }
    }

    private func toggleRecording() {
        if state.recording { state.stop() } else { state.start() }
    }
}
