import SwiftUI

struct FleetJob: Identifiable {
    var id: UUID
    var role: String
    var model: String
    var assignment: String
    var state: String
    var detail: String
    var started: Date
    var finished: Date?
    var taskGroupID: UUID
    var parentID: UUID?
}

struct FleetProgress: Identifiable {
    let id: UUID
    let taskGroupID: UUID
    let date: Date
    let text: String
    let state: String
}

@MainActor final class AgentFleet: ObservableObject {
    @Published var jobs: [FleetJob] = []
    @Published private(set) var progress: [FleetProgress] = []
    private(set) var currentTaskGroupID: UUID?

    private let terminalStates: Set<String> = ["complete", "failed", "stopped"]
    private let activeStates: Set<String> = ["waiting", "running", "queued"]

    func begin(role: String, model: String, assignment: String, taskGroupID: UUID? = nil, parentID: UUID? = nil) -> UUID {
        let id = UUID()
        let startsTask = isRouter(role) || role.lowercased() == "calculator"
        let groupID = taskGroupID ?? (startsTask ? UUID() : currentTaskGroupID ?? UUID())
        currentTaskGroupID = groupID

        let inferredParent = parentID ?? inferredParent(for: role, groupID: groupID)
        let job = FleetJob(id: id, role: role, model: model, assignment: assignment, state: "running", detail: "", started: Date(), finished: nil, taskGroupID: groupID, parentID: inferredParent)
        jobs.append(job)
        appendProgress(groupID: groupID, text: progressText(for: role, state: "running"), state: "running")
        trimHistory()
        return id
    }

    func update(_ id: UUID, state: String, detail: String) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        guard !terminalStates.contains(jobs[index].state) else { return }
        let priorState = jobs[index].state
        jobs[index].state = state
        jobs[index].detail = String(detail.prefix(8000))
        if terminalStates.contains(state) {
            jobs[index].finished = Date()
        }
        if priorState != state {
            appendProgress(groupID: jobs[index].taskGroupID, text: progressText(for: jobs[index].role, state: state), state: state)
        }
    }

    func finish(_ id: UUID, detail: String) {
        update(id, state: "complete", detail: detail)
    }

    func stopActive() {
        for index in jobs.indices where activeStates.contains(jobs[index].state) {
            jobs[index].state = "stopped"
            jobs[index].finished = Date()
            appendProgress(groupID: jobs[index].taskGroupID, text: "\(jobs[index].role) stopped", state: "stopped")
        }
    }

    var taskGroups: [UUID: [FleetJob]] {
        Dictionary(grouping: jobs, by: \.taskGroupID)
    }

    var recentTaskGroupIDs: [UUID] {
        taskGroups.keys.sorted { lhs, rhs in
            (taskGroups[lhs]?.map(\.started).max() ?? .distantPast) > (taskGroups[rhs]?.map(\.started).max() ?? .distantPast)
        }
    }

    func jobs(in groupID: UUID) -> [FleetJob] {
        (taskGroups[groupID] ?? []).sorted { $0.started < $1.started }
    }

    func taskTitle(for groupID: UUID) -> String {
        let group = jobs(in: groupID)
        let lead = group.first(where: { isLead($0.role) })
        let source = group.first(where: { $0.assignment.localizedCaseInsensitiveContains("Execute the approved") }) ?? lead ?? group.first
        guard let source else { return "Task" }
        let text = source.assignment.trimmingCharacters(in: .whitespacesAndNewlines)
        return String(text.prefix(100))
    }

    func progress(for groupID: UUID) -> [FleetProgress] {
        Array(progress.filter { $0.taskGroupID == groupID }.suffix(8).reversed())
    }

    private func inferredParent(for role: String, groupID: UUID) -> UUID? {
        let group = jobs(in: groupID)
        if isLead(role) {
            return group.last(where: { isRouter($0.role) })?.id
        }
        if isRouter(role) { return nil }
        return group.last(where: { isLead($0.role) })?.id ?? group.last?.id
    }

    private func isRouter(_ role: String) -> Bool {
        let value = role.lowercased()
        return value.contains("jev") || value.contains("router") || value.contains("dispatcher")
    }

    private func isLead(_ role: String) -> Bool {
        let value = role.lowercased()
        return value.contains("lead planner") || value.contains("lead responder") || value.contains("lead synthesizer")
    }

    private func progressText(for role: String, state: String) -> String {
        switch state {
        case "running": return "\(role) started"
        case "waiting": return "\(role) is waiting for your input"
        case "queued": return "\(role) was queued"
        case "complete": return "\(role) finished"
        case "failed": return "\(role) hit an error"
        case "stopped": return "\(role) stopped"
        default: return "\(role) · \(state)"
        }
    }

    private func appendProgress(groupID: UUID, text: String, state: String) {
        progress.append(FleetProgress(id: UUID(), taskGroupID: groupID, date: Date(), text: text, state: state))
        if progress.count > 120 { progress.removeFirst(progress.count - 120) }
    }

    private func trimHistory() {
        while jobs.count > 60, let expired = jobs.firstIndex(where: { terminalStates.contains($0.state) }) {
            let removed = jobs.remove(at: expired)
            progress.removeAll { $0.taskGroupID == removed.taskGroupID && !jobs.contains(where: { $0.taskGroupID == removed.taskGroupID }) }
        }
    }
}

struct AgentFleetPanel: View {
    @ObservedObject var fleet: AgentFleet
    @ObservedObject var narrator: ProgressNarrator
    var compact = false
    @State private var selectedJobID: UUID?
    @State private var selectedGroupID: UUID?

    private let activeStates: Set<String> = ["running", "waiting", "queued"]

    private var groupID: UUID? {
        if let selectedGroupID, fleet.taskGroups[selectedGroupID] != nil { return selectedGroupID }
        return fleet.recentTaskGroupIDs.first
    }

    private var groupJobs: [FleetJob] {
        guard let groupID else { return [] }
        return fleet.jobs(in: groupID)
    }

    private var activeCount: Int { groupJobs.filter { activeStates.contains($0.state) }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 12 : 18) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Agent team").font(.system(size: compact ? 16 : 21, weight: .semibold, design: .rounded))
                    Text(groupJobs.isEmpty ? "Ready when you are" : (activeCount > 0 ? "\(activeCount) working now" : "Latest task"))
                        .font(.system(size: 11, weight: .medium)).foregroundStyle(activeCount > 0 ? accent : .secondary)
                }
                Spacer()
                if !fleet.recentTaskGroupIDs.isEmpty {
                    Menu {
                        ForEach(fleet.recentTaskGroupIDs, id: \.self) { id in
                            Button(fleet.taskTitle(for: id)) { selectedGroupID = id; selectedJobID = nil }
                        }
                    } label: {
                        Label("History", systemImage: "clock.arrow.circlepath").font(.system(size: 11, weight: .medium))
                    }.menuStyle(.borderlessButton)
                }
            }

            if let groupID, !groupJobs.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text(fleet.taskTitle(for: groupID))
                        .font(.system(size: 13, weight: .medium)).lineLimit(compact ? 2 : 3).foregroundStyle(.primary)
                    FleetNeuralMap(jobs: groupJobs, selectedJobID: $selectedJobID, compact: compact)
                        .frame(height: compact ? min(400, CGFloat(max(groupJobs.count, 1)) * 54 + 60) : min(460, max(290, CGFloat(max(groupJobs.count, 1)) * 54 + 60)))
                    if let selectedJob = groupJobs.first(where: { $0.id == selectedJobID }) {
                        JobDetail(job: selectedJob)
                    } else if !compact, let latest = groupJobs.last {
                        JobDetail(job: latest)
                    }
                }
            } else {
                EmptyFleetState(compact: compact)
            }

            if groupID != nil {
                ProgressFeed(narrator: narrator, since: groupJobs.first?.started ?? .distantPast, compact: compact)
            }

            if !compact {
                Divider().opacity(0.5)
                Text("Recent tasks").font(.system(size: 13, weight: .semibold))
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(fleet.recentTaskGroupIDs, id: \.self) { id in
                            Button { selectedGroupID = id; selectedJobID = nil } label: {
                                HStack(alignment: .top, spacing: 10) {
                                    Circle().fill(groupStatusColor(fleet.jobs(in: id))).frame(width: 7, height: 7).padding(.top, 5)
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(fleet.taskTitle(for: id)).font(.system(size: 12, weight: .medium)).lineLimit(2)
                                        Text(fleet.jobs(in: id).last?.started.formatted(date: .abbreviated, time: .shortened) ?? "")
                                            .font(.system(size: 10)).foregroundStyle(.secondary)
                                    }
                                    Spacer(minLength: 0)
                                    Text("\(fleet.jobs(in: id).count) steps").font(.system(size: 10)).foregroundStyle(.tertiary)
                                }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
                                    .background(selectedGroupID == id ? accent.opacity(0.1) : panel.opacity(0.65))
                                    .clipShape(RoundedRectangle(cornerRadius: 9))
                            }.buttonStyle(.plain)
                        }
                    }
                }.frame(minHeight: 70, maxHeight: 150)
            }
            Spacer(minLength: 0)
        }
        .onChange(of: fleet.currentTaskGroupID) { _, value in
            if let value { selectedGroupID = value; selectedJobID = nil }
        }
        .onChange(of: groupID) { _, _ in selectedJobID = nil }
        .task(id: fleet.currentTaskGroupID) {
            guard let taskGroupID = fleet.currentTaskGroupID else { return }
            while !Task.isCancelled {
                await narrator.refresh(jobs: fleet.jobs(in: taskGroupID))
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
            }
        }
    }

    private func groupStatusColor(_ jobs: [FleetJob]) -> Color {
        if jobs.contains(where: { $0.state == "failed" }) { return .red }
        if jobs.contains(where: { activeStates.contains($0.state) }) { return accent }
        return .secondary
    }
}

private struct EmptyFleetState: View {
    let compact: Bool
    var body: some View {
        Text("Agent activity will appear here when a task runs.")
            .font(.callout).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, compact ? 12 : 20)
    }
}

private struct FleetNeuralMap: View {
    let jobs: [FleetJob]
    @Binding var selectedJobID: UUID?
    let compact: Bool

    private var root: FleetJob? {
        jobs.first(where: { $0.role.lowercased().contains("lead planner") || $0.role.lowercased().contains("lead responder") }) ?? jobs.first
    }
    private var branches: [FleetJob] {
        guard let root else { return [] }
        return jobs.filter { $0.id != root.id && $0.parentID == root.id }
            .filter { !$0.role.lowercased().contains("jev") && !$0.role.lowercased().contains("router") }
    }
    private var additional: [FleetJob] {
        guard let root else { return [] }
        let shown = Set(([root] + branches).map(\.id))
        return jobs.filter { !shown.contains($0.id) && !$0.role.lowercased().contains("jev") && !$0.role.lowercased().contains("router") }
    }

    var body: some View {
        GeometryReader { geo in
            let rootX = compact ? geo.size.width * 0.34 : geo.size.width * 0.30
            let childX = compact ? geo.size.width * 0.78 : geo.size.width * 0.76
            let rootY = geo.size.height / 2
            ZStack(alignment: .topLeading) {
                Path { path in
                    if !children.isEmpty {
                        for (index, _) in children.enumerated() {
                            let y = nodeY(index: index, count: max(branches.count + additional.count, 1), height: geo.size.height)
                            path.move(to: CGPoint(x: rootX + nodeWidth / 2, y: rootY))
                            path.addCurve(to: CGPoint(x: childX - nodeWidth / 2, y: y), control1: CGPoint(x: geo.size.width * 0.53, y: rootY), control2: CGPoint(x: geo.size.width * 0.53, y: y))
                        }
                    }
                }.stroke(Color.primary.opacity(0.18), style: StrokeStyle(lineWidth: 1.2, lineCap: .round))

                if let root {
                    FleetGraphNode(job: root, selected: selectedJobID == root.id, compact: compact, isMaster: true)
                        .frame(width: nodeWidth, height: 58)
                        .position(x: rootX, y: rootY)
                        .onTapGesture { selectedJobID = root.id }
                    Text("LEAD").font(.system(size: 9, weight: .bold)).tracking(1.1).foregroundStyle(accent)
                        .position(x: rootX, y: max(10, rootY - 39))
                }
                ForEach(children.indices, id: \.self) { index in
                    let job = children[index]
                    let y = nodeY(index: index, count: max(children.count, 1), height: geo.size.height)
                    FleetGraphNode(job: job, selected: selectedJobID == job.id, compact: compact, isMaster: false)
                        .frame(width: nodeWidth, height: 47)
                        .position(x: childX, y: y)
                        .onTapGesture { selectedJobID = job.id }
                }
                if let router = jobs.first(where: { ($0.role.lowercased().contains("jev") || $0.role.lowercased().contains("router")) && $0.id != root?.id }) {
                    Button { selectedJobID = router.id } label: {
                        HStack(spacing: 7) {
                            Image(systemName: "arrow.triangle.branch")
                            Text("Routed by \(router.role)").lineLimit(1)
                            Circle().fill(statusColor(router.state)).frame(width: 6, height: 6)
                        }.font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                            .padding(.horizontal, 10).frame(minHeight: 44)
                            .background(Color.primary.opacity(0.035)).clipShape(Capsule())
                        .overlay(Capsule().stroke(selectedJobID == router.id ? Color.primary.opacity(0.55) : Color(nsColor: .separatorColor), lineWidth: 1))
                    }.buttonStyle(.plain)
                        .position(x: geo.size.width / 2, y: 22)
                }
            }
        }
        .padding(.vertical, 3)
        .background(
            RoundedRectangle(cornerRadius: 14)
                .fill(Color.primary.opacity(0.025))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(nsColor: .separatorColor), lineWidth: 0.7))
        )
    }

    private var nodeWidth: CGFloat { compact ? 116 : 176 }
    private var children: [FleetJob] { branches + additional }
    private func nodeY(index: Int, count: Int, height: CGFloat) -> CGFloat {
        if count <= 1 { return height / 2 }
        let top: CGFloat = height < 180 ? 32 : 38
        let bottom: CGFloat = height - 26
        return top + (bottom - top) * CGFloat(index) / CGFloat(count - 1)
    }
    private func statusColor(_ state: String) -> Color {
        switch state { case "running": .primary; case "waiting", "queued", "complete", "stopped": .secondary; case "failed": .red; default: .secondary }
    }
}

private struct FleetGraphNode: View {
    let job: FleetJob
    let selected: Bool
    let compact: Bool
    let isMaster: Bool
    private var active: Bool { ["running", "waiting", "queued"].contains(job.state) }

    var body: some View {
        HStack(spacing: 7) {
            ZStack {
                Circle().fill(roleColor(job.role).opacity(0.18)).frame(width: 25, height: 25)
                Circle().stroke(roleColor(job.role).opacity(active ? 0.85 : 0.38), lineWidth: active ? 1.5 : 1).frame(width: 25, height: 25)
                if active {
                    Circle().fill(roleColor(job.role).opacity(0.28)).frame(width: 34, height: 34).blur(radius: 5)
                }
                Image(systemName: roleIcon(job.role)).font(.system(size: 10, weight: .semibold)).foregroundStyle(roleColor(job.role))
            }
            VStack(alignment: .leading, spacing: 3) {
                    Text(job.role).font(.system(size: compact ? 11 : 12, weight: .semibold)).lineLimit(1)
                HStack(spacing: 4) {
                    Circle().fill(statusColor(job.state)).frame(width: 5, height: 5)
                    Text(job.state.capitalized).font(.system(size: 9, weight: .medium)).foregroundStyle(.secondary)
                    if !compact { Text("· \(job.model)").font(.system(size: 8)).foregroundStyle(.tertiary).lineLimit(1) }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 8).padding(.vertical, 7)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .background(isMaster ? Color.primary.opacity(0.075) : panel.opacity(0.85))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(selected ? Color.primary.opacity(0.7) : (isMaster ? Color.primary.opacity(0.35) : Color(nsColor: .separatorColor)), lineWidth: selected ? 1.3 : 0.8))
        .shadow(color: active ? Color.primary.opacity(0.05) : .clear, radius: 7)
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .help(job.assignment)
    }

    private func statusColor(_ state: String) -> Color {
        switch state { case "running": .primary; case "waiting", "queued", "complete", "stopped": .secondary; case "failed": .red; default: .secondary }
    }
    private func roleColor(_ role: String) -> Color { .primary }
    private func roleIcon(_ role: String) -> String {
        switch role.lowercased() {
        case let value where value.contains("planner") || value.contains("responder"): "sparkles"
        case let value where value.contains("research") || value.contains("review"): "magnifyingglass"
        case let value where value.contains("browser"): "globe"
        case let value where value.contains("coder"): "chevron.left.forwardslash.chevron.right"
        case let value where value.contains("visual"): "eye"
        case let value where value.contains("verif"): "checkmark.shield"
        case let value where value.contains("calculator"): "function"
        default: "circle.grid.2x2"
        }
    }
}

private struct JobDetail: View {
    let job: FleetJob
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text("\(job.role) · \(job.model)").font(.system(size: 10, weight: .semibold)).lineLimit(1)
                Spacer()
                if let finished = job.finished {
                    Text(String(format: "%.1fs", finished.timeIntervalSince(job.started))).font(.system(size: 9, design: .monospaced)).foregroundStyle(.tertiary)
                } else {
                    Text(job.started, style: .timer).font(.system(size: 9, design: .monospaced)).foregroundStyle(.tertiary)
                }
            }
                    Text(job.assignment).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(3)
            if !job.detail.isEmpty {
                DisclosureGroup("Details") {
                    ScrollView { Text(job.detail).font(.system(size: 10)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(maxHeight: 105)
                }.font(.system(size: 10, weight: .medium))
            }
        }.padding(11).frame(maxWidth: .infinity, alignment: .leading)
            .background(panel.opacity(0.7)).clipShape(RoundedRectangle(cornerRadius: 11))
    }
}

private struct ProgressFeed: View {
    @ObservedObject var narrator: ProgressNarrator
    let since: Date
    let compact: Bool
    private var items: [ProgressNote] { Array(narrator.updates.filter { $0.date >= since }.suffix(compact ? 3 : 6).reversed()) }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Updates").font(.system(size: 12, weight: .semibold))
                Spacer()
                if let latest = items.first { Text(latest.date, style: .relative).font(.system(size: 9)).foregroundStyle(.tertiary) }
            }
            if items.isEmpty {
                Text("Updates will appear as the team makes progress.").font(.system(size: 11)).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(items) { item in
                        HStack(alignment: .top, spacing: 8) {
                            Circle().fill(accent).frame(width: 5, height: 5).padding(.top, 4)
                            Text(item.text).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(2)
                            Spacer(minLength: 0)
                            Text(item.date, style: .relative).font(.system(size: 10, design: .monospaced)).foregroundStyle(.tertiary).lineLimit(1)
                        }
                    }
                }
            }
        }.padding(compact ? 11 : 13).frame(maxWidth: .infinity, alignment: .leading)
            .background(panel.opacity(0.55)).clipShape(RoundedRectangle(cornerRadius: 11))
    }
}
