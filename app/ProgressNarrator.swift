import SwiftUI

struct ProgressNote: Identifiable {
    let id = UUID()
    let text: String
    let date = Date()
}

@MainActor final class ProgressNarrator: ObservableObject {
    @Published private(set) var updates: [ProgressNote] = []
    private var lastUpdate = Date.distantPast
    private var lastSignature = ""
    private var hadWork = false

    func refresh(jobs: [FleetJob]) async {
        let current = Array(jobs.suffix(12))
        guard !current.isEmpty else { return }
        let active = current.filter { ["running", "queued", "waiting"].contains($0.state) }
        let signature = current.map { "\($0.id):\($0.state)" }.joined(separator: "|")
        guard signature != lastSignature, Date().timeIntervalSince(lastUpdate) >= 60 else { return }
        guard !active.isEmpty || hadWork else { return }
        hadWork = !active.isEmpty
        let candidates = Self.candidates(for: current)
        guard !candidates.isEmpty else { return }
        // Status notes derive only from recorded state; routine narration never calls a model.
        let selected = candidates[0]
        lastSignature=signature;lastUpdate=Date()
        if updates.last?.text != selected {
            updates.append(ProgressNote(text:selected));updates=Array(updates.suffix(8))
        }
    }
    static func candidates(for jobs:[FleetJob]) -> [String] {
        if jobs.contains(where:{$0.state == "waiting"}) { return ["A worker has paused for your input.", "Your response is needed before the task can continue."] }
        let active=jobs.filter { ["running","queued"].contains($0.state) }
        if active.isEmpty {
            if jobs.contains(where:{$0.state == "failed"}) { return ["A worker reported a problem. Check its details in the agent map."] }
            if jobs.last?.state == "stopped" { return ["The task has stopped."] }
            return ["The local team has finished this task."]
        }
        if active.contains(where:{$0.role.localizedCaseInsensitiveContains("synthes")}) { return ["The lead has started combining the workers’ findings."] }
        if active.contains(where:{$0.role.localizedCaseInsensitiveContains("verif") || $0.role.localizedCaseInsensitiveContains("analyst")}) { return ["Independent checks have started.", "The team has begun comparing the evidence."] }
        if active.contains(where:{$0.role.localizedCaseInsensitiveContains("browser")}) { return ["The browser worker has started working through your request."] }
        if active.contains(where:{$0.role.localizedCaseInsensitiveContains("file") || $0.role.localizedCaseInsensitiveContains("code")}) { return ["A local worker has started preparing the requested changes."] }
        return ["The local team has started working on your request."]
    }
}
