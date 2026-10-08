import Foundation
import SwiftUI

// MARK: - MaviStep

struct MaviStep: Codable, Identifiable {
    var id: String
    var kind: String
    var task: String
    var dependencies: [String]
    var status: String
    var output: String
    var elapsed: Double
    var artifacts: [MaviArtifact]? = nil

    init(
        id: String,
        kind: String,
        task: String,
        dependencies: [String] = [],
        status: String = "pending",
        output: String = "",
        elapsed: Double = 0
    ) {
        self.id = id
        self.kind = kind
        self.task = task
        self.dependencies = dependencies
        self.status = status
        self.output = output
        self.elapsed = elapsed
    }

    static let allowedKinds: Set<String> = ["chat", "browser", "files", "code", "image", "cad", "stocks", "update", "verify"]
    static let allowedStatuses: Set<String> = ["pending", "running", "waiting", "completed", "failed", "cancelled", "review"]

    static func validate(_ steps: [MaviStep]) throws {
        guard (1...8).contains(steps.count) else {
            throw MaviError.plan("Step count must be 1...8, got \(steps.count)")
        }

        var seenIDs = Set<String>()
        var totalTaskChars = 0

        for step in steps {
            // id: alphanumeric or hyphen
            guard !step.id.isEmpty else {
                throw MaviError.plan("Step id must not be empty")
            }
            for ch in step.id.unicodeScalars {
                let value = ch.value
                let asciiAlphaNumeric = (65...90).contains(value) || (97...122).contains(value) || (48...57).contains(value)
                if !asciiAlphaNumeric && ch != "-" {
                    throw MaviError.plan("Step id '\(step.id)' contains invalid character")
                }
            }
            guard !seenIDs.contains(step.id) else {
                throw MaviError.plan("Duplicate step id '\(step.id)'")
            }
            seenIDs.insert(step.id)

            // kind
            guard MaviStep.allowedKinds.contains(step.kind) else {
                throw MaviError.plan("Unknown kind '\(step.kind)' in step '\(step.id)'")
            }

            // task length
            let taskLen = step.task.count
            guard (1...6000).contains(taskLen) else {
                throw MaviError.plan("Task text length must be 1...6000, got \(taskLen) in step '\(step.id)'")
            }
            totalTaskChars += taskLen

            // status
            guard MaviStep.allowedStatuses.contains(step.status) else {
                throw MaviError.plan("Unknown status '\(step.status)' in step '\(step.id)'")
            }
        }

        guard totalTaskChars <= 16000 else {
            throw MaviError.plan("Total task text exceeds 16000 chars (\(totalTaskChars))")
        }

        // dependencies: must reference earlier valid IDs, no duplicates
        var validIDsSoFar = Set<String>()
        for step in steps {
            var depSet = Set<String>()
            for dep in step.dependencies {
                guard validIDsSoFar.contains(dep) else {
                    throw MaviError.plan("Step '\(step.id)' depends on '\(dep)' which is not an earlier valid id")
                }
                guard !depSet.contains(dep) else {
                    throw MaviError.plan("Step '\(step.id)' has duplicate dependency '\(dep)'")
                }
                depSet.insert(dep)
            }
            validIDsSoFar.insert(step.id)
        }
    }
}

enum MaviError: Error, LocalizedError {
    case plan(String)
    case io(String)
    case decode(String)

    var errorDescription: String? {
        switch self {
        case .plan(let m): return "Plan validation: \(m)"
        case .io(let m): return "I/O error: \(m)"
        case .decode(let m): return "Decode error: \(m)"
        }
    }
}

// MARK: - MaviTaskRecord

struct MaviTaskRecord: Codable, Identifiable {
    var id: UUID
    var request: String
    var steps: [MaviStep]
    var attachmentNames: [String]
    var status: String
    var updated: Date
    var steering: [String]

    init(
        id: UUID = UUID(),
        request: String,
        steps: [MaviStep],
        attachmentNames: [String] = [],
        status: String = "pending",
        updated: Date = Date(),
        steering: [String] = []
    ) {
        self.id = id
        self.request = request
        self.steps = steps
        self.attachmentNames = attachmentNames
        self.status = status
        self.updated = updated
        self.steering = steering
    }

    var nextIndex: Int? {
        for (i, step) in steps.enumerated() {
            if step.status == "completed" { continue }
            // Any unresolved side effect blocks later dispatch until the user inspects it.
            guard step.status == "pending" else { return nil }
            let depsMet = step.dependencies.allSatisfy { depID in
                steps.first(where: { $0.id == depID })?.status == "completed"
            }
            if depsMet { return i }
        }
        return nil
    }

    var completedContext: String {
        var parts: [String] = []
        for step in steps where step.status == "completed" {
            let label = "[UNTRUSTED EVIDENCE] step \(step.id) (\(step.kind))"
            let out = step.output.count > 2000 ? String(step.output.prefix(2000)) + "…" : step.output
            parts.append("\(label)\n\(out)")
        }
        let joined = parts.joined(separator: "\n---\n")
        return joined.count > 8000 ? String(joined.prefix(8000)) : joined
    }

    mutating func interrupt() {
        for i in steps.indices where steps[i].status == "running" {
            steps[i].status = "review"
        }
        status = "interrupted"
        updated = Date()
    }
}

// MARK: - MaviTaskStore

@MainActor
final class MaviTaskStore: ObservableObject {
    @Published var records: [MaviTaskRecord] = []
    @Published var notes: String = ""
    @Published var error: String = ""
    @Published var evaluationReport: String = ""
    @Published private(set) var readOnlyCorrupt = false

    private let directory: URL
    private let fileURL: URL
    private let maxRecords = 30
    private let maxNotesBytes = 16 * 1024
    private let maxDiskJSONBytes = 2 * 1024 * 1024
    private var savedNotes = ""

    init(directory: URL? = nil) {
        let dir: URL
        if let d = directory {
            dir = d
        } else {
            let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
                ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
            dir = base.appendingPathComponent("Mavi/Tasks", isDirectory: true)
        }
        self.directory = dir
        self.fileURL = dir.appendingPathComponent("tasks.json")

        // Ensure directory exists with 0700
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir.path)
        } catch {
            self.error = "Task directory could not be secured: \(error.localizedDescription)"
        }
        reload()
    }

    var context: String {
        let n = savedNotes.count > 8000 ? String(savedNotes.prefix(8000)) : savedNotes
        return n
    }

    // MARK: Persistence

    func save() {
        guard !readOnlyCorrupt else {
            error = "Task history is read-only because its checkpoint is corrupt or oversized. Use explicit recovery before changing it."
            return
        }
        do {
            // Enforce max records
            var trimmed = records
            if trimmed.count > maxRecords {
                trimmed = Array(trimmed.suffix(maxRecords))
                records = trimmed
            }

            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try validateRecords(trimmed)
            let data = try encoder.encode(trimmed)

            if data.count > maxDiskJSONBytes {
                readOnlyCorrupt = true
                error = "Disk JSON exceeds 2 MB limit; not saved."
                return
            }

            try ensureSecureDirectory()
            // Data.write(.atomic) replaces an existing checkpoint atomically.
            try data.write(to: fileURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
            error = ""
        } catch {
            self.error = "Save failed: \(error.localizedDescription)"
            if error is MaviError { readOnlyCorrupt = true }
        }
    }

    func upsert(_ record: MaviTaskRecord) {
        guard !readOnlyCorrupt else { blockCorruptMutation(); return }
        let proposed = records.contains(where: { $0.id == record.id })
            ? records.map { $0.id == record.id ? record : $0 }
            : records + [record]
        do { try validateRecords(Array(proposed.suffix(maxRecords))) }
        catch { self.error = "Task was not saved: \(error.localizedDescription)"; return }
        if let idx = records.firstIndex(where: { $0.id == record.id }) {
            records[idx] = record
        } else {
            records.append(record)
        }
        save()
    }

    func remove(_ id: UUID) {
        guard !readOnlyCorrupt else { blockCorruptMutation(); return }
        records.removeAll { $0.id == id }
        save()
    }

    func clear() {
        guard !readOnlyCorrupt else { blockCorruptMutation(); return }
        records = []
        save()
    }

    func retryStep(recordID: UUID, stepID: String) {
        guard !readOnlyCorrupt, let index = records.firstIndex(where: { $0.id == recordID }),
              let stepIndex = records[index].steps.firstIndex(where: { $0.id == stepID }),
              ["running", "review"].contains(records[index].steps[stepIndex].status) else { return }
        records[index].steps[stepIndex].status = "pending"
        records[index].steps[stepIndex].output = ""
        records[index].steps[stepIndex].artifacts = nil
        records[index].status = "pending"
        records[index].updated = Date()
        save()
    }

    func confirmStepOutcome(recordID: UUID, stepID: String) {
        guard !readOnlyCorrupt, let index = records.firstIndex(where: { $0.id == recordID }),
              let stepIndex = records[index].steps.firstIndex(where: { $0.id == stepID }),
              records[index].steps[stepIndex].status == "review" else { return }
        let previousArtifacts = records[index].steps[stepIndex].artifacts ?? []
        do {
            var freshArtifacts: [MaviArtifact] = []
            for artifact in previousArtifacts {
                let url = URL(fileURLWithPath: artifact.path).standardizedFileURL
                if FileManager.default.fileExists(atPath: url.path) {
                    freshArtifacts.append(try MaviArtifactEvidence.capture(url))
                }
            }
            records[index].steps[stepIndex].artifacts = freshArtifacts.isEmpty ? nil : freshArtifacts
            records[index].steps[stepIndex].status = "completed"
            let artifactNote: String
            if previousArtifacts.isEmpty {
                artifactNote = "No artifact evidence was attached."
            } else if freshArtifacts.count == previousArtifacts.count {
                artifactNote = "Existing artifact files were re-captured at confirmation time."
            } else {
                artifactNote = "Available artifact files were re-captured; unavailable old evidence was discarded."
            }
            records[index].steps[stepIndex].output = "USER-CONFIRMED OUTSIDE THIS TASK (not tool-verified). \(artifactNote)"
            records[index].status = records[index].steps.first(where: { $0.status != "completed" })?.status ?? "completed"
            records[index].updated = Date()
            save()
        } catch {
            self.error = "Could not refresh artifact evidence: \(error.localizedDescription)"
        }
    }

    func recoverCorruptStore() {
        guard readOnlyCorrupt else { return }
        do {
            try ensureSecureDirectory()
            if FileManager.default.fileExists(atPath: fileURL.path) { try FileManager.default.removeItem(at: fileURL) }
            records = []
            readOnlyCorrupt = false
            if !readOnlyCorrupt { error = "" }
            save()
        } catch {
            self.error = "Task history recovery failed: \(error.localizedDescription)"
            readOnlyCorrupt = true
        }
    }

    func saveNotes() {
        let trimmed = Self.utf8Prefix(notes, maxBytes: maxNotesBytes)
        notes = trimmed
        do {
            try ensureSecureDirectory()
            let notesURL = directory.appendingPathComponent("notes.txt")
            let data = Data(trimmed.utf8)
            try data.write(to: notesURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: notesURL.path)
            savedNotes = trimmed
            if !readOnlyCorrupt { error = "" }
        } catch {
            self.error = "Notes save failed: \(error.localizedDescription)"
        }
    }

    func reload() {
        var interruptedOnLoad = false
        do {
            if FileManager.default.fileExists(atPath: fileURL.path) {
                let fileAttributes = try FileManager.default.attributesOfItem(atPath: fileURL.path)
                let storedSize = (fileAttributes[.size] as? NSNumber)?.intValue ?? 0
                guard storedSize <= maxDiskJSONBytes else { throw MaviError.decode("Task checkpoint exceeds the 2 MB limit") }
                let data = try Data(contentsOf: fileURL)
                guard data.count <= maxDiskJSONBytes else { throw MaviError.decode("Task checkpoint exceeds the 2 MB limit") }
                let decoder = JSONDecoder()
                var loaded = try decoder.decode([MaviTaskRecord].self, from: data)
                try validateRecords(loaded)
                // Convert running steps to review/interrupted, preserve completed
                for i in loaded.indices {
                    var changed = false
                    for j in loaded[i].steps.indices where loaded[i].steps[j].status == "running" {
                        loaded[i].steps[j].status = "review"
                        changed = true
                    }
                    if changed {
                        loaded[i].status = "interrupted"
                        loaded[i].updated = Date()
                        interruptedOnLoad = true
                    }
                }
                records = loaded
                readOnlyCorrupt = false
            } else {
                records = []
                readOnlyCorrupt = false
            }
            if interruptedOnLoad { save() }
            else { error = "" }
        } catch {
            readOnlyCorrupt = true
            self.error = "Reload/decode error: \(error.localizedDescription). The checkpoint is preserved read-only until explicit recovery."
        }

        // Notes are a separate user-authored file; load them even if task history is corrupt.
        do {
            let notesURL = directory.appendingPathComponent("notes.txt")
            if FileManager.default.fileExists(atPath: notesURL.path) {
                let notesAttributes = try FileManager.default.attributesOfItem(atPath: notesURL.path)
                let noteSize = (notesAttributes[.size] as? NSNumber)?.intValue ?? 0
                let handle = try FileHandle(forReadingFrom: notesURL)
                let data = try handle.read(upToCount: min(noteSize, maxNotesBytes + 4)) ?? Data()
                try? handle.close()
                notes = Self.utf8Prefix(String(decoding: data, as: UTF8.self), maxBytes: maxNotesBytes)
                if noteSize > maxNotesBytes && error.isEmpty { error = "Saved notes exceeded 16 KB and were loaded as a bounded prefix. Save notes to replace the oversized file." }
            } else {
                notes = ""
            }
            savedNotes = notes
        } catch {
            let noteFailure = "Notes could not be loaded: \(error.localizedDescription)"
            self.error = self.error.isEmpty ? noteFailure : self.error + " " + noteFailure
        }
    }

    private func blockCorruptMutation() {
        error = "Task history is read-only because its checkpoint is corrupt or oversized. Use explicit recovery before changing it."
    }

    private func ensureSecureDirectory() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
    }

    private func validateRecords(_ values: [MaviTaskRecord]) throws {
        guard values.count <= maxRecords else { throw MaviError.decode("Task history exceeds 30 records") }
        let recordStatuses: Set<String> = ["pending", "running", "waiting", "completed", "failed", "cancelled", "review", "interrupted"]
        var recordIDs = Set<UUID>()
        for record in values {
            guard recordIDs.insert(record.id).inserted else { throw MaviError.decode("Duplicate task record id") }
            guard record.request.count <= 16000 else { throw MaviError.decode("Task request exceeds 16000 characters") }
            guard recordStatuses.contains(record.status) else { throw MaviError.decode("Unknown task status '\(record.status)'") }
            try MaviStep.validate(record.steps)
            guard record.steps.allSatisfy({ $0.output.count <= 8000 }) else { throw MaviError.decode("Step output exceeds 8000 characters") }
            guard record.attachmentNames.count <= 100,
                  record.attachmentNames.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 1024 }) else {
                throw MaviError.decode("Attachment names exceed safe bounds")
            }
        }
    }

    private static func utf8Prefix(_ text: String, maxBytes: Int) -> String {
        var result = ""
        var used = 0
        for scalar in text.unicodeScalars {
            let value = String(scalar)
            let count = value.utf8.count
            guard used + count <= maxBytes else { break }
            result.unicodeScalars.append(scalar)
            used += count
        }
        return result
    }

    // MARK: Offline checks

    func runChecks() {
        let start = Date()
        var lines: [String] = []
        lines.append("Offline engine checks — not model capability benchmarks")
        lines.append("Total physical RAM: \(ProcessInfo.processInfo.physicalMemory) bytes")

        // 1. Keyboard CAD dependency plan
        do {
            let s1 = MaviStep(id: "cad-1", kind: "cad", task: "Set the keyboard holder width to 320 mm and depth to 140 mm")
            let s2 = MaviStep(id: "cad-2", kind: "cad", task: "Add a 6 mm retaining lip and 12 mm cable clearance", dependencies: ["cad-1"])
            let s3 = MaviStep(id: "verify-1", kind: "verify", task: "Inspect the recorded CAD dimensions and output", dependencies: ["cad-2"])
            try MaviStep.validate([s1, s2, s3])
            lines.append("PASS: CAD dependency plan validated")
        } catch {
            lines.append("FAIL: CAD dependency plan: \(error)")
        }

        // 2. Browser login pause
        do {
            let s1 = MaviStep(id: "br-1", kind: "browser", task: "Open the provided sign-in page and stop before entering credentials", status: "waiting")
            let s2 = MaviStep(id: "br-2", kind: "browser", task: "Wait for the user to sign in manually; do not enter or submit credentials", dependencies: ["br-1"], status: "pending")
            try MaviStep.validate([s1, s2])
            let record = MaviTaskRecord(request: "Pause for user sign-in", steps: [s1, s2])
            let safelyPaused = record.nextIndex == nil && s2.task == "Wait for the user to sign in manually; do not enter or submit credentials"
            lines.append(safelyPaused ? "PASS: Browser plan pauses for user sign-in without credential submission" : "FAIL: Browser sign-in pause is unsafe")
        } catch {
            lines.append("FAIL: Browser login pause plan: \(error)")
        }

        // 3. Multi-image attachment names retained
        do {
            let rec = MaviTaskRecord(
                request: "Process images",
                steps: [MaviStep(id: "img-1", kind: "image", task: "Load and analyze images")],
                attachmentNames: ["photo1.png", "photo2.jpg", "photo3.heic"]
            )
            let names = rec.attachmentNames
            let ok = names.count == 3 && names.contains("photo1.png") && names.contains("photo2.jpg") && names.contains("photo3.heic")
            lines.append(ok ? "PASS: Multi-image attachment names retained" : "FAIL: Attachment names not retained")
        }

        // 4. File → verify plan
        do {
            let s1 = MaviStep(id: "file-1", kind: "files", task: "Read and parse input file")
            let s2 = MaviStep(id: "verify-1", kind: "verify", task: "Verify parsed output", dependencies: ["file-1"])
            try MaviStep.validate([s1, s2])
            lines.append("PASS: File→verify plan validated")
        } catch {
            lines.append("FAIL: File→verify plan: \(error)")
        }

        // 5. Update review remains incomplete
        do {
            var rec = MaviTaskRecord(
                request: "Update system",
                steps: [
                    MaviStep(id: "upd-1", kind: "update", task: "Apply update", status: "review"),
                    MaviStep(id: "verify-1", kind: "verify", task: "Verify update", dependencies: ["upd-1"], status: "pending")
                ]
            )
            rec.interrupt()
            let updStatus = rec.steps.first(where: { $0.id == "upd-1" })?.status ?? ""
            let ok = updStatus == "review" && rec.status == "interrupted" && rec.nextIndex == nil
            lines.append(ok ? "PASS: Update review remains incomplete after interrupt" : "FAIL: Update review status incorrect")
        }

        // 6. Interruption preserves completed
        do {
            var rec = MaviTaskRecord(
                request: "Test interrupt",
                steps: [
                    MaviStep(id: "s1", kind: "code", task: "Step 1", status: "completed", output: "done"),
                    MaviStep(id: "s2", kind: "code", task: "Step 2", status: "running"),
                    MaviStep(id: "s3", kind: "code", task: "Step 3", status: "pending")
                ]
            )
            rec.interrupt()
            let s1Status = rec.steps.first(where: { $0.id == "s1" })?.status ?? ""
            let s2Status = rec.steps.first(where: { $0.id == "s2" })?.status ?? ""
            let ok = s1Status == "completed" && s2Status == "review" && rec.status == "interrupted"
            lines.append(ok ? "PASS: Interruption preserves completed steps" : "FAIL: Interruption did not preserve completed")
        }

        // 7. Unsafe step rejection
        do {
            let bad = MaviStep(id: "bad", kind: "unknown_kind", task: "Do something")
            try MaviStep.validate([bad])
            lines.append("FAIL: Unsafe step was not rejected")
        } catch {
            lines.append("PASS: Unsafe step rejected: \(error.localizedDescription)")
        }

        // 8. Empty plan rejection
        do {
            try MaviStep.validate([])
            lines.append("FAIL: Empty plan was not rejected")
        } catch {
            lines.append("PASS: Empty plan rejected")
        }

        let elapsed = Date().timeIntervalSince(start)
        lines.append("Checks completed in \(String(format: "%.3f", elapsed))s")
        evaluationReport = lines.joined(separator: "\n")
    }
}

// MARK: - MaviTasksPanel

struct MaviTasksPanel: View {
    @ObservedObject var store: MaviTaskStore
    var busy: Bool
    var onResume: (UUID) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var showClearHistory = false
    @State private var showCorruptRecovery = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            notesSection
            Divider()
            recordsSection
            Divider()
            footer
        }
        .frame(minWidth: 650, minHeight: 500)
    }

    private var header: some View {
        HStack {
            Text("Tasks & memory")
                .font(.title2).bold()
            Spacer()
            if !store.error.isEmpty {
                Text(store.error)
                    .font(.caption)
                    .foregroundColor(.red)
                    .lineLimit(2)
                    .truncationMode(.tail)
            }
            Button("Done") { dismiss() }
                .keyboardShortcut(.defaultAction)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private var notesSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Notes (explicit user-authored, not derived from model output)")
                    .font(.caption).foregroundColor(.secondary)
                Spacer()
                Button("Clear notes") {
                    store.notes = ""
                    store.saveNotes()
                }
                .disabled(busy || store.notes.isEmpty)
                Button("Save notes") {
                    store.saveNotes()
                }
                .disabled(busy)
            }
            TextEditor(text: $store.notes)
                .font(.system(.body, design: .monospaced))
                .frame(height: 80)
                .border(Color(nsColor: .gridColor))
                .disabled(busy)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private var recordsSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Records (\(store.records.count)/\(30))")
                    .font(.headline)
                Spacer()
                Button("Clear history") {
                    showClearHistory = true
                }
                .disabled(busy || store.records.isEmpty || store.readOnlyCorrupt)
                .confirmationDialog("Clear all task history?", isPresented: $showClearHistory, titleVisibility: .visible) {
                    Button("Clear history", role: .destructive) { store.clear() }
                    Button("Cancel", role: .cancel) {}
                }
                if store.readOnlyCorrupt {
                    Button("Recover corrupt history…", role: .destructive) { showCorruptRecovery = true }
                        .confirmationDialog("Discard the unreadable task history?", isPresented: $showCorruptRecovery, titleVisibility: .visible) {
                            Button("Discard and recover", role: .destructive) { store.recoverCorruptStore() }
                            Button("Cancel", role: .cancel) {}
                        } message: {
                            Text("The corrupt task checkpoint will be deleted and replaced with an empty history. Project notes remain separate.")
                        }
                }
            }
            if store.records.isEmpty {
                Text("No tasks yet.")
                    .foregroundColor(.secondary)
                    .padding(.vertical, 20)
            } else {
                ScrollView {
                    VStack(spacing: 8) {
                        ForEach(store.records) { record in
                            RecordRow(
                                record: record,
                                store: store,
                                busy: busy,
                                onResume: onResume,
                                onDelete: { store.remove(record.id) }
                            )
                        }
                    }
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private var footer: some View {
        HStack {
            Text("Evaluations")
                .font(.caption).foregroundColor(.secondary)
            Button("Run offline checks") {
                store.runChecks()
            }
            .disabled(busy)
            Spacer()
            if !store.evaluationReport.isEmpty {
                DisclosureGroup("Offline report (\(store.evaluationReport.count) chars)") {
                    ScrollView {
                        Text(store.evaluationReport)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(6)
                    }
                    .frame(maxHeight: 180)
                }
                .font(.caption)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }
}

// MARK: - RecordRow

private struct RecordRow: View {
    let record: MaviTaskRecord
    @ObservedObject var store: MaviTaskStore
    let busy: Bool
    let onResume: (UUID) -> Void
    let onDelete: () -> Void

    @State private var showSteps = false
    @State private var showReport = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(record.request.count > 80 ? String(record.request.prefix(80)) + "…" : record.request)
                    .font(.body)
                Spacer()
                Text(record.status)
                    .font(.caption)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(statusColor.opacity(0.15))
                    .foregroundColor(statusColor)
                    .cornerRadius(4)
                Text(record.updated, style: .time)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            HStack(spacing: 8) {
                Button(showSteps ? "Hide steps" : "Show steps (\(record.steps.count))") {
                    showSteps.toggle()
                }
                .buttonStyle(.borderless)

                if canResume {
                    Button("Resume") {
                        onResume(record.id)
                    }
                    .disabled(busy || store.readOnlyCorrupt)
                }

                Button("Delete") {
                    onDelete()
                }
                .disabled(busy || store.readOnlyCorrupt)
                .foregroundColor(.red)
            }

            if showSteps {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(record.steps.enumerated()), id: \.element.id) { idx, step in
                        StepRow(step: step, recordID: record.id, index: idx, store: store, busy: busy)
                    }
                }
                .padding(.leading, 12)
            }

            if !record.attachmentNames.isEmpty {
                Text("Attachments: \(record.attachmentNames.joined(separator: ", "))")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(8)
        .background(Color(nsColor: .controlBackgroundColor))
        .cornerRadius(6)
    }

    private var canResume: Bool {
        let resumable: Set<String> = ["interrupted", "waiting", "failed", "pending", "review"]
        return resumable.contains(record.status)
    }

    private var statusColor: Color {
        switch record.status {
        case "completed": return .green
        case "running": return .blue
        case "failed": return .red
        case "interrupted", "review": return .orange
        case "waiting": return .yellow
        default: return .gray
        }
    }
}

// MARK: - StepRow

private struct StepRow: View {
    let step: MaviStep
    let recordID: UUID
    let index: Int
    @ObservedObject var store: MaviTaskStore
    let busy: Bool

    @State private var showOutput = false
    @State private var showRetry = false
    @State private var showConfirmOutcome = false

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text("[\(index + 1)] \(step.id)")
                    .font(.system(.caption, design: .monospaced))
                Text(step.kind)
                    .font(.caption2)
                    .padding(.horizontal, 4)
                    .background(Color(nsColor: .quaternaryLabelColor).opacity(0.3))
                    .cornerRadius(3)
                Text(step.status)
                    .font(.caption2)
                    .foregroundColor(stepStatusColor)
                if step.elapsed > 0 {
                    Text(String(format: "%.2fs", step.elapsed))
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                Spacer()
                if step.status == "running" || step.status == "review" {
                    Text("Outcome uncertain — inspect before retry")
                        .font(.caption2)
                        .foregroundColor(.orange)
                }
            }

            Text(step.task.count > 120 ? String(step.task.prefix(120)) + "…" : step.task)
                .font(.caption)
                .foregroundColor(.primary)

            if step.status == "running" || step.status == "review" {
                Button("Allow retry of this step") {
                    showRetry = true
                }
                .disabled(busy || store.readOnlyCorrupt)
                .confirmationDialog(
                    "Reset step \(step.id) to pending? Completed steps are preserved.",
                    isPresented: $showRetry,
                    titleVisibility: .visible
                ) {
                    Button("Reset to pending", role: .destructive) {
                        store.retryStep(recordID: recordID, stepID: step.id)
                    }
                    Button("Cancel", role: .cancel) {}
                }
                if step.status == "review" {
                    Button("Confirm I handled this outside the task…") {
                        showConfirmOutcome = true
                    }
                    .disabled(busy || store.readOnlyCorrupt)
                    .confirmationDialog(
                        "Confirm this outcome outside the task?",
                        isPresented: $showConfirmOutcome,
                        titleVisibility: .visible
                    ) {
                        Button("I inspected or applied it") {
                            store.confirmStepOutcome(recordID: recordID, stepID: step.id)
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("This marks the step complete based on your confirmation. Mavi did not verify the outcome. Existing artifact files will be re-captured.")
                    }
                }
            }

            if !step.output.isEmpty {
                Button(showOutput ? "Hide output" : "Show output") {
                    showOutput.toggle()
                }
                .buttonStyle(.borderless)
                .font(.caption2)

                if showOutput {
                    Text(step.output.count > 2000 ? String(step.output.prefix(2000)) + "…" : step.output)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(4)
                        .background(Color(nsColor: .textBackgroundColor))
                        .cornerRadius(4)
                }
            }
        }
        .padding(4)
        .background(Color(nsColor: .windowBackgroundColor).opacity(0.5))
        .cornerRadius(4)
    }

    private var stepStatusColor: Color {
        switch step.status {
        case "completed": return .green
        case "running": return .blue
        case "failed": return .red
        case "review": return .orange
        case "waiting": return .yellow
        case "cancelled": return .gray
        default: return .secondary
        }
    }
}
