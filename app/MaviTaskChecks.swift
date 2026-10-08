import Foundation

enum MaviTaskCheckFailure: Error, LocalizedError {
    case failed(String)
    var errorDescription: String? {
        if case let .failed(message) = self { return message }
        return nil
    }
}

enum MaviTaskChecks {
    private static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        guard condition() else { throw MaviTaskCheckFailure.failed(message) }
    }

    private static func rejects(_ steps: [MaviStep], _ label: String) throws {
        do {
            try MaviStep.validate(steps)
        } catch {
            return
        }
        throw MaviTaskCheckFailure.failed("Unsafe task plan was accepted: \(label)")
    }

    @MainActor static func run() throws {
        let one = MaviStep(id: "step-1", kind: "cad", task: "Create a keyboard holder", dependencies: [])
        let two = MaviStep(id: "step-2", kind: "verify", task: "Inspect the CAD result", dependencies: ["step-1"])
        try MaviStep.validate([one, two])

        try rejects([], "empty plan")
        try rejects((0..<9).map { MaviStep(id: "s\($0)", kind: "chat", task: "Do work") }, "more than eight steps")
        try rejects([MaviStep(id: "bad id", kind: "chat", task: "Do work")], "malformed ID")
        try rejects([MaviStep(id: "same", kind: "chat", task: "First"), MaviStep(id: "same", kind: "verify", task: "Second")], "duplicate IDs")
        try rejects([MaviStep(id: "step-1", kind: "chat", task: "First", dependencies: ["step-2"]), MaviStep(id: "step-2", kind: "verify", task: "Second")], "forward dependency")
        try rejects([MaviStep(id: "step-1", kind: "chat", task: "First", dependencies: ["missing"])], "unknown dependency")
        try rejects([MaviStep(id: "step-1", kind: "chat", task: "First", dependencies: ["step-1"])], "self dependency")
        try rejects([MaviStep(id: "step-1", kind: "chat", task: "First"), MaviStep(id: "step-2", kind: "verify", task: "Second", dependencies: ["step-1", "step-1"])], "duplicate dependency")
        try rejects([MaviStep(id: "step-1", kind: "terminal", task: "Run arbitrary commands")], "unknown kind")
        var invalidStatus = MaviStep(id: "step-1", kind: "chat", task: "Do work")
        invalidStatus.status = "retry"
        try rejects([invalidStatus], "invalid status")
        try rejects([MaviStep(id: "stép-1", kind: "chat", task: "Do work")], "non-ASCII ID")
        try rejects([MaviStep(id: "step-1", kind: "chat", task: "")], "empty task")
        try rejects([MaviStep(id: "step-1", kind: "chat", task: String(repeating: "x", count: 6001))], "oversized task")
        try rejects([MaviStep(id: "step-1", kind: "chat", task: String(repeating: "x", count: 8000)), MaviStep(id: "step-2", kind: "chat", task: String(repeating: "y", count: 8001))], "oversized aggregate task")

        var selectable = MaviTaskRecord(request: "Build and inspect a keyboard holder", steps: [one, two], attachmentNames: [])
        try require(selectable.nextIndex == 0, "first pending step should be selectable")
        selectable.steps[0].status = "completed"
        selectable.steps[0].output = "CAD result evidence"
        try require(selectable.nextIndex == 1, "dependent step should become selectable after completion")
        try require(selectable.completedContext.localizedCaseInsensitiveContains("untrusted"), "completed output must be labeled as untrusted evidence")
        try require(selectable.completedContext.contains("step-1") && selectable.completedContext.contains("cad"), "completed context must identify its source step")

        var uncertain = MaviTaskRecord(request: "Submit a browser form", steps: [MaviStep(id: "browser-1", kind: "browser", task: "Submit the form")], attachmentNames: [])
        uncertain.steps[0].status = "review"
        try require(uncertain.nextIndex == nil, "review step must not be automatically retried")
        uncertain.steps[0].status = "running"
        try require(uncertain.nextIndex == nil, "running step must not be automatically retried")
        var blockedByReview = MaviTaskRecord(request: "Review before continuing", steps: [
            MaviStep(id: "uncertain", kind: "browser", task: "Check submission", status: "review"),
            MaviStep(id: "later", kind: "verify", task: "Inspect result", status: "pending")
        ])
        try require(blockedByReview.nextIndex == nil, "a later pending step must not run past an unresolved review step")
        blockedByReview.steps[0].status = "completed"
        try require(blockedByReview.nextIndex == 1, "later work should become selectable after review is resolved")

        var interrupted = MaviTaskRecord(request: "Create then inspect", steps: [one, two], attachmentNames: [])
        interrupted.steps[0].status = "completed"
        interrupted.steps[0].output = "A completed CAD artifact"
        interrupted.steps[1].status = "running"
        interrupted.interrupt()
        try require(interrupted.status == "interrupted", "interrupted task status should be recorded")
        try require(interrupted.steps[0].status == "completed", "interruption must preserve completed work")
        try require(interrupted.steps[1].status == "review", "interrupted running work must require review")
        try require(interrupted.nextIndex == nil, "interrupted review work must not be retried automatically")

        let root = FileManager.default.temporaryDirectory.appendingPathComponent("mavi-task-checks-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = MaviTaskStore(directory: root)
        var persisted = MaviTaskRecord(request: "Process the attached images", steps: [MaviStep(id: "images", kind: "files", task: "Review images")], attachmentNames: ["front.png", "side.png"])
        persisted.steps[0].status = "completed"
        persisted.steps[0].output = "Image review recorded"
        store.upsert(persisted)
        store.notes = "Use metric units for this project."
        store.saveNotes()
        try require(store.error.isEmpty, "store should save a valid task and user-authored note")

        let invalidRoot = FileManager.default.temporaryDirectory.appendingPathComponent("mavi-task-invalid-checks-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: invalidRoot) }
        let invalidStore = MaviTaskStore(directory: invalidRoot)
        let invalidRequest = MaviTaskRecord(request: String(repeating: "r", count: 16001), steps: [MaviStep(id: "large-request", kind: "chat", task: "Do work")])
        invalidStore.upsert(invalidRequest)
        try require(invalidStore.records.isEmpty && !invalidStore.error.isEmpty, "oversized request must be rejected before persistence")
        let invalidOutput = MaviTaskRecord(request: "Oversized output", steps: [MaviStep(id: "large-output", kind: "chat", task: "Do work", status: "completed", output: String(repeating: "o", count: 8001))])
        invalidStore.upsert(invalidOutput)
        try require(invalidStore.records.isEmpty, "oversized step output must be rejected before persistence")
        let invalidTaskStatus = MaviTaskRecord(request: "Invalid status", steps: [MaviStep(id: "invalid-status", kind: "chat", task: "Do work")], status: "done")
        invalidStore.upsert(invalidTaskStatus)
        try require(invalidStore.records.isEmpty, "unknown task status must be rejected before persistence")

        let reloaded = MaviTaskStore(directory: root)
        reloaded.reload()
        try require(reloaded.error.isEmpty, "valid task store should reload without errors")
        try require(reloaded.records.count == 1, "checkpoint should persist")
        let saved = reloaded.records.first
        try require(saved?.id == persisted.id, "checkpoint identity should round-trip")
        try require(saved?.steps.first?.status == "completed", "completed checkpoint status should round-trip")
        try require(saved?.attachmentNames == ["front.png", "side.png"], "attachment names should round-trip")
        try require(reloaded.notes == "Use metric units for this project.", "user-authored notes should persist separately")
        reloaded.notes = "Unsaved draft"
        try require(reloaded.context == "Use metric units for this project.", "model context must use saved notes, not an unsaved editor draft")
        reloaded.notes = String(repeating: "🧡", count: 5000)
        reloaded.saveNotes()
        try require(reloaded.notes.utf8.count <= 16 * 1024, "saved notes must respect the UTF-8 byte limit")
        try require(reloaded.context == reloaded.notes, "saved note edits should update model context")
        reloaded.notes = ""
        reloaded.saveNotes()
        let notesCleared = MaviTaskStore(directory: root)
        notesCleared.reload()
        try require(notesCleared.notes.isEmpty, "cleared notes should remain cleared after reload")

        reloaded.remove(persisted.id)
        try require(reloaded.records.isEmpty, "remove should delete the selected task")
        let afterRemove = MaviTaskStore(directory: root)
        afterRemove.reload()
        try require(afterRemove.records.isEmpty, "removed task must not reappear from disk")

        var toInterrupt = MaviTaskRecord(request: "Pause this task", steps: [MaviStep(id: "work", kind: "chat", task: "Perform work")], attachmentNames: [])
        toInterrupt.steps[0].status = "running"
        let interruptedStore = MaviTaskStore(directory: root)
        interruptedStore.upsert(toInterrupt)
        let restarted = MaviTaskStore(directory: root)
        restarted.reload()
        try require(restarted.records.first?.steps.first?.status == "review", "reload must convert a running step to review")
        try require(restarted.records.first?.status == "interrupted", "reload must mark a running task interrupted")
        try require(restarted.records.first?.steps.first?.status != "completed", "reload must not invent completion")
        restarted.clear()
        try require(restarted.records.isEmpty, "clear should remove task history")
        let afterClear = MaviTaskStore(directory: root)
        afterClear.reload()
        try require(afterClear.records.isEmpty, "cleared task history must stay deleted")

        let mutationRoot = FileManager.default.temporaryDirectory.appendingPathComponent("mavi-task-mutation-checks-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: mutationRoot) }
        let mutationStore = MaviTaskStore(directory: mutationRoot)
        var retryRecord = MaviTaskRecord(request: "Retry only the uncertain step", steps: [
            MaviStep(id: "done", kind: "files", task: "Create a file", status: "completed", output: "Created file"),
            MaviStep(id: "uncertain", kind: "update", task: "Apply update", status: "review", output: "Old uncertain output")
        ])
        retryRecord.steps[1].artifacts = [MaviArtifact(path: "/tmp/old-artifact", sha256: "stale", size: 1)]
        mutationStore.upsert(retryRecord)
        mutationStore.retryStep(recordID: retryRecord.id, stepID: "done")
        try require(mutationStore.records.first?.steps[0].status == "completed", "retry must not reset completed work")
        mutationStore.retryStep(recordID: retryRecord.id, stepID: "uncertain")
        let retried = mutationStore.records.first
        try require(retried?.steps[0].status == "completed", "retry should preserve completed step status")
        try require(retried?.steps[0].output == "Created file", "retry should preserve completed step evidence")
        try require(retried?.steps[1].status == "pending" && retried?.steps[1].output.isEmpty == true, "retry should reset only the selected uncertain step")
        try require(retried?.steps[1].artifacts == nil && retried?.status == "pending", "retry should discard stale artifact evidence and return task to pending")

        let artifactPath = mutationRoot.appendingPathComponent("result.txt")
        try Data("old artifact".utf8).write(to: artifactPath)
        let oldArtifact = try MaviArtifactEvidence.capture(artifactPath)
        try Data("changed external artifact".utf8).write(to: artifactPath)
        var confirmationRecord = MaviTaskRecord(request: "Confirm the externally handled update", steps: [
            MaviStep(id: "confirm", kind: "update", task: "Inspect the update", status: "review", output: "Stale tool output")
        ])
        confirmationRecord.steps[0].artifacts = [oldArtifact]
        mutationStore.upsert(confirmationRecord)
        mutationStore.confirmStepOutcome(recordID: confirmationRecord.id, stepID: "confirm")
        let confirmed = mutationStore.records.first(where: { $0.id == confirmationRecord.id })?.steps.first
        try require(confirmed?.status == "completed", "explicit external confirmation should complete the reviewed step")
        try require(confirmed?.output.localizedCaseInsensitiveContains("user-confirmed") == true && confirmed?.output.localizedCaseInsensitiveContains("not tool-verified") == true, "confirmed output must distinguish user confirmation from tool verification")
        try require(confirmed?.output != "Stale tool output", "confirmation must discard stale output")
        try require(confirmed?.artifacts?.first?.sha256 != oldArtifact.sha256, "confirmation must recapture changed artifact hashes")

        let repeated = MaviTaskStore(directory: mutationRoot)
        repeated.reload()
        try require(repeated.error.isEmpty && repeated.records.count == 2, "repeated atomic saves should replace an existing checkpoint")
        let checkpointURL = mutationRoot.appendingPathComponent("tasks.json")
        let fileAttributes = try? FileManager.default.attributesOfItem(atPath: checkpointURL.path)
        let dirAttributes = try? FileManager.default.attributesOfItem(atPath: mutationRoot.path)
        let fileMode = (fileAttributes?[.posixPermissions] as? NSNumber)?.intValue
        let dirMode = (dirAttributes?[.posixPermissions] as? NSNumber)?.intValue
        try require(fileMode.map { $0 & 0o777 == 0o600 } == true, "task checkpoint permissions should be 0600")
        try require(dirMode.map { $0 & 0o777 == 0o700 } == true, "task directory permissions should be 0700")

        let corruptRoot = FileManager.default.temporaryDirectory.appendingPathComponent("mavi-task-corrupt-checks-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: corruptRoot) }
        let corruptStore = MaviTaskStore(directory: corruptRoot)
        corruptStore.upsert(MaviTaskRecord(request: "Preserve this checkpoint", steps: [MaviStep(id: "keep", kind: "chat", task: "Keep")], attachmentNames: []))
        let files = (try? FileManager.default.contentsOfDirectory(at: corruptRoot, includingPropertiesForKeys: nil)) ?? []
        guard let checkpoint = files.first(where: { $0.pathExtension == "json" && $0.lastPathComponent.localizedCaseInsensitiveContains("task") })
            ?? files.first(where: { $0.pathExtension == "json" }) else {
            throw MaviTaskCheckFailure.failed("Task store did not create a checkpoint JSON file")
        }
        let corruptBytes = Data("{ definitely not valid JSON".utf8)
        try corruptBytes.write(to: checkpoint, options: .atomic)
        let readCorrupt = MaviTaskStore(directory: corruptRoot)
        readCorrupt.reload()
        try require(!readCorrupt.error.isEmpty, "corrupt checkpoint must produce a visible error")
        try require(readCorrupt.readOnlyCorrupt, "corrupt checkpoint should enter protected read-only state")
        try require((try? Data(contentsOf: checkpoint)) == corruptBytes, "reload must not overwrite a corrupt checkpoint")
        readCorrupt.upsert(MaviTaskRecord(request: "Must not overwrite corruption", steps: [MaviStep(id: "new", kind: "chat", task: "New")]))
        readCorrupt.remove(UUID())
        readCorrupt.clear()
        readCorrupt.save()
        try require((try? Data(contentsOf: checkpoint)) == corruptBytes, "save after corrupt reload must not silently replace the checkpoint")
        try require(readCorrupt.records.isEmpty, "blocked mutations must not change in-memory records")
        readCorrupt.recoverCorruptStore()
        try require(!readCorrupt.readOnlyCorrupt && readCorrupt.error.isEmpty, "explicit recovery should clear read-only corruption state")
        try require((try? JSONDecoder().decode([MaviTaskRecord].self, from: Data(contentsOf: checkpoint)))?.isEmpty == true, "explicit recovery should create an empty valid checkpoint")

        let oversizedRoot = FileManager.default.temporaryDirectory.appendingPathComponent("mavi-task-oversize-checks-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: oversizedRoot) }
        try FileManager.default.createDirectory(at: oversizedRoot, withIntermediateDirectories: true)
        let oversizedURL = oversizedRoot.appendingPathComponent("tasks.json")
        try Data(repeating: 0x20, count: 2 * 1024 * 1024 + 1).write(to: oversizedURL)
        let oversizedStore = MaviTaskStore(directory: oversizedRoot)
        try require(oversizedStore.readOnlyCorrupt, "oversized checkpoint should be protected read-only")
        oversizedStore.clear()
        try require((try? Data(contentsOf: oversizedURL))?.count == 2 * 1024 * 1024 + 1, "clear must not overwrite an oversized checkpoint")

        let evaluationRoot = FileManager.default.temporaryDirectory.appendingPathComponent("mavi-task-eval-checks-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: evaluationRoot) }
        let evaluationStore = MaviTaskStore(directory: evaluationRoot)
        evaluationStore.runChecks()
        try require(evaluationStore.evaluationReport.contains("Offline engine checks — not model capability benchmarks"), "offline evaluation report should be clearly labeled")
        try require(evaluationStore.evaluationReport.contains("PASS: Browser plan pauses for user sign-in without credential submission"), "offline browser fixture should verify pause without credential submission")
        try require(!evaluationStore.evaluationReport.contains("FAIL:"), "offline foundation checks should all pass")

        print("PASS: Mavi task plan validation, dependency scheduling, uncertainty handling, checkpoint and note persistence, attachment names, retry/confirmation, secure atomic writes, removal, clear, and corrupt-store preservation.")
    }
}
