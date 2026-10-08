import AppKit
import Combine
import Foundation
import ImageIO

/// Owns the narrow bridge between Discord and Mavi's existing local workspaces.
/// A remote task is tracked by its agent run ID, Discord session and channel; commands
/// can never steer, stop, or read the output of some other local task.
@MainActor
final class DiscordWorkspace: ObservableObject {
    @Published private(set) var status = "Set up Discord to begin"
    @Published private(set) var remoteTaskStatus = "No remote task is active"
    private weak var desk: Desk?
    private weak var agent: UnifiedAgent?
    private weak var remote: DiscordRemote?
    private weak var updates: SelfUpdateState?
    private weak var studio: ModelingStudio?
    private weak var browser: BrowserState?
    private var monitor: Task<Void, Never>?
    private var remoteJob: RemoteJob?
    private var isAsking = false

    private struct FileFingerprint: Equatable {
        let size: UInt64
        let modified: Date
        let inode: UInt64
    }

    private struct RemoteJob {
        var agentID: UUID
        var recordID: UUID?
        let sessionID: UUID
        let channelID: String
        let startFingerprints: [String: FileFingerprint]
        let startedAt: Date
        var lastActivity = ""
        var lastStatusSent = Date.distantPast
        var sentQuestionIDs: Set<String> = []
    }

    init() {}

    func configure(desk: Desk, agent: UnifiedAgent, remote: DiscordRemote,
                   updates: SelfUpdateState, studio: ModelingStudio, browser: BrowserState) {
        monitor?.cancel()
        remoteJob = nil
        self.desk = desk
        self.agent = agent
        self.remote = remote
        self.updates = updates
        self.studio = studio
        self.browser = browser
        remote.handler = { [weak self] request in
            guard let self else { return "Mavi's Discord handler is unavailable. Reopen the Discord panel." }
            return await self.handle(request)
        }
        status = "Ready · Discord commands stay in the selected server and channel"
    }

    private func handle(_ request: DiscordRemoteRequest) async -> String {
        guard let remote, let desk, let agent,
              remote.connected, remote.sessionID == request.sessionID,
              remote.channelID == request.channelID else { return "Discord disconnected before Mavi accepted this command." }
        switch request.command {
        case "help":
            return "Commands: `!mavi ask …` for a one-turn answer; `!mavi task …` to start a Mavi task; `!mavi status`; `!mavi steer …`, `!mavi stop`, or `!mavi answer <question ID> …` for your active remote task. Replies and files go only to this channel."
        case "status":
            guard let job = ownedJob(for: request), agent.activeID == job.agentID else { return "No active remote-owned task. Local Mavi work is not exposed to Discord." }
            return String("Remote task status: \(agent.activity)".prefix(1500))
        case "ask":
            return await ask(request, desk: desk, agent: agent)
        case "task":
            return await startTask(request, desk: desk, agent: agent)
        case "steer":
            return await steer(request, agent: agent)
        case "stop":
            return stop(request, agent: agent)
        case "answer":
            return answer(request, agent: agent)
        default:
            return "Unsupported Mavi command. Use `!mavi help`."
        }
    }

    private func ask(_ request: DiscordRemoteRequest, desk: Desk, agent: UnifiedAgent) async -> String {
        guard !desk.workInProgress, !agent.isStopping, !isAsking else {
            return "Mavi is already working. `ask` is a separate one-turn model call and won't join or inspect that work."
        }
        isAsking = true
        desk.busy = true
        desk.status = "Answering a one-turn Discord ask…"
        defer { isAsking = false; desk.busy = false }
        do {
            let files = try await prepareAttachments(request.attachments, desk: desk)
            guard let remote, remote.connected, remote.sessionID == request.sessionID else {
                return "Discord disconnected before the answer was ready."
            }
            var content = String(request.text.prefix(4000))
            var images: [String] = []
            var remaining = 12_000
            for file in files {
                if let imageData = file.imageData {
                    if images.count < 2 { images.append(imageData.base64EncodedString()) }
                    content += "\n\nAttached image: \(safeFilename(file.name))"
                } else {
                    let excerpt = String(file.text.prefix(max(0, remaining)))
                    remaining -= excerpt.count
                    content += "\n\nAttached file: \(safeFilename(file.name))\n\(excerpt)"
                    if excerpt.count < file.text.count { content += "\n[File excerpt truncated]" }
                }
            }
            var message: [String: Any] = ["role": "user", "content": content]
            if !images.isEmpty { message["images"] = images }
            // Deliberately only the caller's current request. No chat history, saved memory,
            // account references, task state, or other local context is included.
            let result = try await desk.callModel([message], structured: false, stageOverride: "discord-ask")
            status = "Answered a stateless Discord ask"
            desk.status = "Discord one-turn ask complete"
            return String(result.prefix(5500))
        } catch {
            status = "Discord ask failed"
            desk.status = "Discord one-turn ask failed"
            return "Mavi could not answer this request: \(String(error.localizedDescription.prefix(500)))"
        }
    }

    private func startTask(_ request: DiscordRemoteRequest, desk: Desk, agent: UnifiedAgent) async -> String {
        guard let remote else { return "Mavi's Discord workspace is unavailable." }
        guard remote.allowTasks else { return "Remote Mavi tasks are disabled in Discord settings." }
        guard !desk.workInProgress, !agent.isStopping, !isAsking, remoteJob == nil else {
            return "Mavi is already working. Discord can start a task when Mavi is idle."
        }
        do {
            let files = try await prepareAttachments(request.attachments, desk: desk)
            guard remote.connected, remote.sessionID == request.sessionID,
                  !desk.workInProgress, !agent.isStopping else { return "Mavi became busy before the remote task could start." }
            let startFingerprints = Self.outputFingerprints(desk: desk, studio: studio)
            guard agent.send(request.text, files: files), let agentID = agent.activeID else {
                return "Mavi did not accept the remote task. Check Mavi's local status and try again."
            }
            remoteJob = RemoteJob(agentID: agentID, recordID: agent.currentRecord?.id,
                                  sessionID: request.sessionID, channelID: request.channelID,
                                  startFingerprints: startFingerprints, startedAt: Date())
            remoteTaskStatus = "Accepted · waiting for Mavi"
            status = "Started one remote-owned Mavi task"
            startMonitoring()
            return "Mavi accepted this task. Use `!mavi status`, `!mavi steer …`, or `!mavi stop` here to manage this remote-owned run."
        } catch {
            return "Mavi could not prepare the attached files: \(String(error.localizedDescription.prefix(500)))"
        }
    }

    private func steer(_ request: DiscordRemoteRequest, agent: UnifiedAgent) async -> String {
        guard let job = ownedJob(for: request), agent.activeID == job.agentID, agent.isBusy else {
            return "There is no active task owned by this Discord session to steer."
        }
        do {
            let files = try await prepareAttachments(request.attachments, desk: desk)
            guard let current = remoteJob, current.sessionID == request.sessionID,
                  agent.isBusy, agent.activeID == current.agentID else {
                return "The remote task ended while the instruction was being prepared."
            }
            guard agent.steer(request.text, files: files) else { return "Mavi could not apply that instruction to this remote task." }
            if let updatedID = agent.activeID { remoteJob?.agentID = updatedID }
            remoteTaskStatus = "Steering instruction accepted"
            return "Mavi accepted the steering instruction for this remote task."
        } catch {
            return "Mavi could not prepare the steering attachments: \(String(error.localizedDescription.prefix(500)))"
        }
    }

    private func stop(_ request: DiscordRemoteRequest, agent: UnifiedAgent) -> String {
        guard let job = ownedJob(for: request), agent.activeID == job.agentID,
              agent.isBusy else { return "There is no active task owned by this Discord session to stop." }
        agent.stop()
        remoteTaskStatus = "Stopping the remote-owned task…"
        return "Mavi is stopping the task started from this Discord session."
    }

    private func answer(_ request: DiscordRemoteRequest, agent: UnifiedAgent) -> String {
        guard let job = ownedJob(for: request), agent.activeID == job.agentID,
              agent.isBusy else { return "There is no active task owned by this Discord session to answer." }
        let parts = request.text.split(maxSplits: 1, whereSeparator: { $0.isWhitespace }).map(String.init)
        guard parts.count == 2 else { return "Use `!mavi answer <question ID> your answer`." }
        let questionID = parts[0]
        let text = String(parts[1].prefix(1500)).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return "Include an answer after the question ID." }
        guard !Self.looksLikeSecret(text) else {
            return "Don't send passwords, tokens, verification codes, or other credentials through Discord. Provide this information locally on the Mac."
        }
        if let prompt = agent.promptState, prompt.id.uuidString.caseInsensitiveCompare(questionID) == .orderedSame {
            guard !Self.isSensitivePrompt(prompt.text) else {
                return "This question needs local-only sign-in, credential, permission, or approval. Respond in Mavi on the Mac."
            }
            agent.promptAnswer = text
            agent.submitPromptAnswer()
            remoteTaskStatus = "Clarification received"
            return "Mavi received the clarification for this remote task."
        }
        if let prompt = browser?.reviewPrompt, prompt.requestID == questionID,
           prompt.kind == "question", agent.activeKind == "browser" {
            guard !Self.isSensitivePrompt(prompt.text) else {
                return "This browser question needs local-only sign-in, credential, permission, or approval. Respond on the Mac."
            }
            guard browser?.respond(text: text) == true else { return "The browser question is no longer waiting for an answer." }
            remoteTaskStatus = "Browser clarification received"
            return "Mavi passed the clarification to this remote-owned browser task."
        }
        return "That question ID is not the current clarification for this remote-owned task."
    }

    private func ownedJob(for request: DiscordRemoteRequest) -> RemoteJob? {
        guard let job = remoteJob, job.sessionID == request.sessionID,
              job.channelID == request.channelID,
              remote?.sessionID == request.sessionID,
              remote?.channelID == request.channelID else { return nil }
        return job
    }

    private func startMonitoring() {
        monitor?.cancel()
        monitor = Task { @MainActor [weak self] in
            guard let self else { return }
            while !Task.isCancelled, let job = self.remoteJob {
                guard self.remote?.connected == true,
                      self.remote?.sessionID == job.sessionID,
                      self.remote?.channelID == job.channelID else {
                    self.finishMonitoring(disconnected: true)
                    return
                }
                guard let agent = self.agent else { self.finishMonitoring(disconnected: true); return }

                if let activeID = agent.activeID, activeID != job.agentID {
                    await self.sendOwnedStatus("Remote task ownership ended; local task details were withheld.", job: job)
                    self.finishMonitoring(disconnected: false)
                    return
                }
                if agent.currentRecord != nil, self.remoteJob?.recordID == nil {
                    self.remoteJob?.recordID = agent.currentRecord?.id
                }
                await self.forwardPendingQuestion(job: job, agent: agent)

                if !agent.isBusy {
                    let finalStatus = agent.activity
                    var output = String(agent.lastOutput.trimmingCharacters(in: .whitespacesAndNewlines).prefix(4500))
                    if agent.activeKind == "browser" {
                        output = String((self.browser?.status ?? "Browser task ended").prefix(400))
                    }
                    await self.sendCompletion(status: finalStatus, output: output, job: job)
                    await self.sendVerifiedArtifact(job: job)
                    self.finishMonitoring(disconnected: false)
                    return
                }

                let activity = String(agent.activity.prefix(500))
                if !activity.isEmpty,
                   self.remoteJob?.lastActivity != activity,
                   Date().timeIntervalSince(self.remoteJob?.lastStatusSent ?? .distantPast) >= 60 {
                    self.remoteTaskStatus = activity
                    if await self.sendOwnedStatus("Mavi task status: \(activity)", job: job) {
                        self.remoteJob?.lastActivity = activity
                        self.remoteJob?.lastStatusSent = Date()
                    }
                }
                try? await Task.sleep(nanoseconds: 750_000_000)
            }
        }
    }

    private func forwardPendingQuestion(job: RemoteJob, agent: UnifiedAgent) async {
        if let prompt = agent.promptState {
            let id = prompt.id.uuidString
            guard !Self.isSensitivePrompt(prompt.text), remoteJob?.sentQuestionIDs.contains(id) != true else {
                if Self.isSensitivePrompt(prompt.text), remoteJob?.sentQuestionIDs.contains("sensitive-\(id)") != true,
                   await sendOwnedStatus("Mavi needs local-only sign-in, credential, permission, or approval. Respond on the Mac.", job: job) {
                    remoteJob?.sentQuestionIDs.insert("sensitive-\(id)")
                }
                return
            }
            let message = "Mavi needs clarification (ID `\(id)`): \(String(prompt.text.prefix(800)))\nReply with `!mavi answer \(id) your answer`."
            if await sendOwnedStatus(message, job: job) { remoteJob?.sentQuestionIDs.insert(id) }
            return
        }
        if agent.activeKind == "browser", let prompt = browser?.reviewPrompt,
           prompt.kind == "question" {
            let id = prompt.requestID
            guard !Self.isSensitivePrompt(prompt.text), remoteJob?.sentQuestionIDs.contains(id) != true else {
                if Self.isSensitivePrompt(prompt.text), remoteJob?.sentQuestionIDs.contains("sensitive-\(id)") != true,
                   await sendOwnedStatus("The browser needs local-only sign-in, credential, permission, or approval. Respond on the Mac.", job: job) {
                    remoteJob?.sentQuestionIDs.insert("sensitive-\(id)")
                }
                return
            }
            let message = "The browser needs a clarification (ID `\(id)`): \(String(prompt.text.prefix(800)))\nReply with `!mavi answer \(id) your answer`."
            if await sendOwnedStatus(message, job: job) { remoteJob?.sentQuestionIDs.insert(id) }
        } else if agent.activeKind == "browser", let prompt = browser?.reviewPrompt,
                  prompt.kind == "login" || prompt.kind == "action" {
            let id = prompt.requestID
            guard remoteJob?.sentQuestionIDs.contains(id) != true else { return }
            let message = prompt.kind == "login"
                ? "The browser needs you to sign in locally on the Mac. Discord cannot handle sign-in."
                : "The browser needs a local review or approval on the Mac. Discord cannot approve this action."
            if await sendOwnedStatus(message, job: job) { remoteJob?.sentQuestionIDs.insert(id) }
        }
    }

    @discardableResult
    private func sendOwnedStatus(_ text: String, job: RemoteJob) async -> Bool {
        guard let remote, remote.connected, remote.sessionID == job.sessionID,
              remote.channelID == job.channelID else { return false }
        return await remote.sendReply(String(text.prefix(1800)), sessionID: job.sessionID, channelID: job.channelID)
    }

    private func sendCompletion(status: String, output: String, job: RemoteJob) async {
        let summary = output.isEmpty ? "No final text output was available." : output
        let message = "Mavi task ended. Status: \(String(status.prefix(400)))\n\n\(summary)"
        if await sendOwnedStatus(message, job: job) {
            remoteJob?.lastActivity = status
            remoteJob?.lastStatusSent = Date()
        }
    }

    private func sendVerifiedArtifact(job: RemoteJob) async {
        guard let agent, let desk,
              remoteJob?.agentID == job.agentID,
              agent.activeID == nil || agent.activeID == job.agentID,
              let (outputURL, artifact) = Self.verifiedOutput(job: job, agent: agent, desk: desk, studio: studio),
              let data = try? Data(contentsOf: outputURL), UInt64(data.count) == artifact.size,
              data.count <= 20 * 1024 * 1024,
              let remote, remote.connected, remote.sessionID == job.sessionID,
              remote.channelID == job.channelID else { return }
        _ = await remote.sendArtifact(data: data, filename: outputURL.lastPathComponent,
                                      sessionID: job.sessionID, channelID: job.channelID)
    }

    private func finishMonitoring(disconnected: Bool) {
        monitor?.cancel()
        monitor = nil
        remoteJob = nil
        if disconnected { remoteTaskStatus = "Discord disconnected; the local task was left alone" }
        else { remoteTaskStatus = "No remote task is active" }
    }

    private static func outputFingerprints(desk: Desk, studio: ModelingStudio?) -> [String: FileFingerprint] {
        let urls = [desk.selectedWorkspaceFile, desk.generatedImageURL, studio?.path].compactMap { $0 }
        return Dictionary(urls.compactMap { url -> (String, FileFingerprint)? in
            guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
                  let size = attrs[.size] as? NSNumber,
                  let modified = attrs[.modificationDate] as? Date else { return nil }
            let inode = (attrs[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
            return (url.standardizedFileURL.path, FileFingerprint(size: size.uint64Value, modified: modified, inode: inode))
        }, uniquingKeysWith: { _, latest in latest })
    }

    private static func outputURL(for kind: String, desk: Desk, studio: ModelingStudio?) -> URL? {
        switch kind {
        case "files": return desk.selectedWorkspaceFile
        case "image": return desk.generatedImageURL
        case "cad": return studio?.path
        default: return nil
        }
    }

    private static func verifiedOutput(job: RemoteJob, agent: UnifiedAgent, desk: Desk,
                                       studio: ModelingStudio?) -> (URL, MaviArtifact)? {
        // Prefer persisted completion evidence. Any completed artifact-producing step may
        // yield the final file in a multi-step task; later verify steps are irrelevant.
        let record = job.recordID.flatMap { id in agent.taskStore.records.first { $0.id == id } }
            ?? agent.currentRecord.flatMap { current in agent.taskStore.records.first { $0.id == current.id } }
        if let record {
            for step in record.steps where step.status == "completed" && ["files", "image", "cad"].contains(step.kind) {
                guard let outputURL = outputURL(for: step.kind, desk: desk, studio: studio),
                      let artifact = step.artifacts?.first(where: {
                          URL(fileURLWithPath: $0.path).standardizedFileURL.path == outputURL.standardizedFileURL.path
                      }),
                      artifact.size > 0, artifact.size <= 20 * 1024 * 1024,
                      changedSinceStart(outputURL, fingerprints: job.startFingerprints, startedAt: job.startedAt),
                      (try? MaviArtifactEvidence.matches(artifact)) == true else { continue }
                return (outputURL, artifact)
            }
            return nil
        }

        // Ordinary single-route file/image/CAD runs have no journal record. Capture the
        // output only when filesystem evidence shows it was created or changed after start.
        guard ["files", "image", "cad"].contains(agent.activeKind),
              let url = outputURL(for: agent.activeKind, desk: desk, studio: studio),
              changedSinceStart(url, fingerprints: job.startFingerprints, startedAt: job.startedAt),
              let artifact = try? MaviArtifactEvidence.capture(url),
              artifact.size > 0, artifact.size <= 20 * 1024 * 1024 else { return nil }
        return (url, artifact)
    }

    private static func changedSinceStart(_ url: URL, fingerprints: [String: FileFingerprint], startedAt: Date) -> Bool {
        let path = url.standardizedFileURL.path
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
              let size = attrs[.size] as? NSNumber,
              let modified = attrs[.modificationDate] as? Date else { return false }
        let inode = (attrs[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
        let current = FileFingerprint(size: size.uint64Value, modified: modified, inode: inode)
        if let previous = fingerprints[path] { return previous != current }
        // A path absent from the start snapshot is safe only when its file timestamp
        // proves creation after acceptance; a pre-existing newly selected file is rejected.
        return modified >= startedAt
    }

    private static func isSensitivePrompt(_ value: String) -> Bool {
        let text = value.lowercased()
        return ["password", "passcode", "credential", "sign in", "log in", "login", "one-time code",
                "verification code", "authenticator", "api key", "bot token", "keychain",
                "permission", "approve", "authorization", "two-factor", "2fa", "mfa"]
            .contains(where: text.contains)
    }

    private static func looksLikeSecret(_ value: String) -> Bool {
        let text = value.lowercased()
        if ["password is", "passcode is", "token is", "api key is", "verification code is",
            "my password", "my token", "my api key", "one-time code", "otp:", "bearer "]
            .contains(where: text.contains) { return true }
        if value.range(of: #"\b[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"#, options: .regularExpression) != nil { return true }
        return false
    }

    private func prepareAttachments(_ attachments: [DiscordRemoteAttachment], desk: Desk?) async throws -> [DeskAttachment] {
        guard let desk else { throw DeskError("Mavi's file tools are unavailable.") }
        guard attachments.count <= 6 else { throw DeskError("Attach up to six files.") }
        let advertisedTotal = attachments.reduce(0) { $0 + max(0, $1.size) }
        guard advertisedTotal <= 20 * 1024 * 1024 else { throw DeskError("Attachments exceed 20 MB total.") }
        var totalDownloaded = 0
        var output: [DeskAttachment] = []
        for attachment in attachments {
            try Task.checkCancellation()
            let (data, filename) = try await download(attachment)
            totalDownloaded += data.count
            guard totalDownloaded <= 20 * 1024 * 1024 else { throw DeskError("Attachments exceed 20 MB total.") }
            let ext = URL(fileURLWithPath: filename).pathExtension.lowercased()
            if ["png", "jpg", "jpeg"].contains(ext) {
                guard output.filter({ $0.imageData != nil }).count < 2,
                      let image = Self.normalizedImage(data) else {
                    throw DeskError("Mavi accepts up to two readable PNG/JPEG images per request.")
                }
                output.append(DeskAttachment(name: filename, text: "Image attachment; bytes are not retained in chat history.", imageData: image))
            } else {
                let temp = FileManager.default.temporaryDirectory.appendingPathComponent("mavi-discord-\(UUID().uuidString)-\(safeFilename(filename))")
                defer { try? FileManager.default.removeItem(at: temp) }
                try data.write(to: temp, options: .atomic)
                let result = try await desk.runFileTool(["action": "read", "path": temp.path])
                let text = result["text"] as? String ?? ""
                output.append(DeskAttachment(name: filename, text: String(text.prefix(12_000)) + (text.count > 12_000 || result["truncated"] as? Bool == true ? "\n[Excerpt truncated]" : ""), imageData: nil))
            }
        }
        return output
    }

    /// Decode only a bounded thumbnail so a compressed image with enormous pixel
    /// dimensions cannot exhaust memory during Discord attachment handling.
    private static func normalizedImage(_ data: Data) -> Data? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let type = CGImageSourceGetType(source) as String?,
              ["public.png", "public.jpeg"].contains(type),
              let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 1024,
                kCGImageSourceShouldCacheImmediately: true
              ] as CFDictionary) else { return nil }
        return NSBitmapImageRep(cgImage: thumbnail).representation(using: .jpeg, properties: [.compressionFactor: 0.8])
    }

    private func download(_ attachment: DiscordRemoteAttachment) async throws -> (Data, String) {
        guard let url = URL(string: attachment.url), url.scheme?.lowercased() == "https",
              let host = url.host?.lowercased(), Self.allowedCDNHosts.contains(host),
              url.user == nil, url.password == nil else { throw DeskError("An attachment URL was outside Discord's allowed media CDN hosts.") }
        var request = URLRequest(url: url, timeoutInterval: 25)
        request.httpMethod = "GET"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.urlCache = nil; config.httpShouldSetCookies = false
        let session = URLSession(configuration: config, delegate: DiscordMediaRedirectDelegate(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        guard let responseURL = response.url, responseURL.scheme?.lowercased() == "https",
              Self.allowedCDNHosts.contains(responseURL.host?.lowercased() ?? ""),
              let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw DeskError("Discord attachment download was rejected.")
        }
        if response.expectedContentLength > 20 * 1024 * 1024 { throw DeskError("An attachment exceeds 20 MB.") }
        var data = Data(); data.reserveCapacity(min(max(attachment.size, 0), 1_000_000))
        for try await byte in bytes {
            guard data.count < 20 * 1024 * 1024 else { throw DeskError("An attachment exceeds 20 MB.") }
            data.append(byte)
        }
        guard !data.isEmpty else { throw DeskError("Discord returned an empty attachment.") }
        let safe = safeFilename(attachment.filename)
        return (data, safe)
    }

    private static let allowedCDNHosts: Set<String> = ["cdn.discordapp.com", "media.discordapp.net"]
    private func safeFilename(_ filename: String) -> String {
        let leaf = URL(fileURLWithPath: filename).lastPathComponent
        let cleaned = String(leaf.unicodeScalars.filter { CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_. ")).contains($0) }.prefix(100))
        return cleaned.isEmpty || cleaned == "." || cleaned == ".." ? "attachment" : cleaned
    }
}

private final class DiscordMediaRedirectDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        let hosts: Set<String> = ["cdn.discordapp.com", "media.discordapp.net"]
        guard request.url?.scheme?.lowercased() == "https",
              hosts.contains(request.url?.host?.lowercased() ?? ""),
              request.url?.user == nil, request.url?.password == nil else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }
}
