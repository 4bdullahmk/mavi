import Foundation
import Security
import Combine

struct DiscordRemoteRequest: Sendable {
    let id: String
    let channelID: String
    let authorID: String
    let command: String
    let text: String
    let attachments: [DiscordRemoteAttachment]
    let sessionID: UUID
}

struct DiscordRemoteAttachment: Sendable {
    let id: String
    let url: String
    let filename: String
    let size: Int
    let contentType: String?
}

enum DiscordRequestPolicy {
    static func validSnowflake(_ value: String) -> Bool {
        value.range(of: #"^[0-9]{17,20}$"#, options: .regularExpression) != nil
    }

    static func snowflakeLess(_ lhs: String, _ rhs: String) -> Bool {
        lhs.count == rhs.count ? lhs < rhs : lhs.count < rhs.count
    }

    static func isAfterCursor(_ messageID: String, cursor: String?) -> Bool {
        guard let cursor else { return true }
        return snowflakeLess(cursor, messageID)
    }

    /// Parses commands from the configured user and channel. `nil` means ignore.
    static func parse(content: String, authorID: String, allowedUserIDs: Set<String>,
                      channelID: String, expectedChannelID: String, guildID: String?,
                      expectedGuildID: String?, isBot: Bool, webhookID: String?,
                      attachments: [DiscordRemoteAttachment] = []) -> (command: String, text: String)? {
        guard !isBot, webhookID == nil,
              channelID == expectedChannelID, guildID == nil || guildID == expectedGuildID,
              allowedUserIDs.contains(authorID), content.count <= 4000,
              content.hasPrefix("!mavi ") else { return nil }
        let body = String(content.dropFirst(6)).trimmingCharacters(in: .whitespacesAndNewlines)
        let split = body.split(maxSplits: 1, whereSeparator: { $0.isWhitespace })
        guard let first = split.first else { return nil }
        let command = String(first).lowercased()
        guard ["ask", "task", "status", "steer", "stop", "answer", "help"].contains(command) else { return nil }
        let text = split.count > 1 ? String(split[1]).trimmingCharacters(in: .whitespacesAndNewlines) : ""
        if ["ask", "task", "steer", "answer"].contains(command) && text.isEmpty { return nil }
        if command == "stop" || command == "status" || command == "help", !text.isEmpty { return nil }
        guard attachments.count <= 6, attachments.allSatisfy({ $0.size >= 0 }),
              attachments.reduce(0, { min($0 + min($1.size, 20 * 1024 * 1024 + 1), 20 * 1024 * 1024 + 1) }) <= 20 * 1024 * 1024 else { return nil }
        if !attachments.isEmpty && !(["ask", "task", "steer"].contains(command) && !text.isEmpty) { return nil }
        return (command, text)
    }
}

@MainActor
final class DiscordRemote: ObservableObject {
    @Published var serverID: String { didSet { persistSettings(); configurationChanged(oldValue != serverID) } }
    @Published var channelID: String { didSet { persistSettings(); configurationChanged(oldValue != channelID) } }
    @Published var userIDsText: String { didSet { persistSettings(); configurationChanged(oldValue != userIDsText) } }
    @Published var applicationID: String { didSet { persistSettings(); configurationChanged(oldValue != applicationID) } }
    @Published var tokenDraft = ""
    @Published private(set) var status = "Disconnected"
    @Published private(set) var connected = false
    @Published private(set) var busy = false
    @Published private(set) var activity: [String] = []
    @Published var allowTasks: Bool { didSet { UserDefaults.standard.set(allowTasks, forKey: "discord.remote.allowTasks") } }
    @Published private(set) var sessionID = UUID()

    /// Parent-owned dispatcher. It should return a short acceptance/result string promptly.
    var handler: ((DiscordRemoteRequest) async -> String)?

    private let defaults = UserDefaults.standard
    private let service = "local.mavi.discord"
    private let account = "bot-token"
    private let baseURL = URL(string: "https://discord.com/api/v10")!
    private var session = URLSession(configuration: .ephemeral)
    private var pollTask: Task<Void, Never>?
    private var generation = UUID()
    private var botID = ""
    private var queue: [DiscordRemoteRequest] = []
    private var workerTask: Task<Void, Never>?
    private var retryDelay: UInt64 = 2
    private var activeConfig: Config?
    private var redirectDelegate: DiscordRedirectDelegate?

    private struct Config: Equatable {
        let serverID: String
        let channelID: String
        let allowedUserIDs: Set<String>
        let applicationID: String
    }

    init() {
        serverID = defaults.string(forKey: "discord.remote.serverID") ?? ""
        channelID = defaults.string(forKey: "discord.remote.channelID") ?? ""
        userIDsText = defaults.string(forKey: "discord.remote.userIDs") ?? ""
        applicationID = defaults.string(forKey: "discord.remote.applicationID") ?? ""
        allowTasks = defaults.bool(forKey: "discord.remote.allowTasks")
    }

    var hasToken: Bool { readToken() != nil }
    var allowedUserIDs: Set<String> {
        Set(userIDsText.split(whereSeparator: { $0 == "," || $0.isWhitespace }).map(String.init).filter(DiscordRequestPolicy.validSnowflake))
    }

    private var currentConfig: Config {
        Config(serverID: serverID, channelID: channelID, allowedUserIDs: allowedUserIDs, applicationID: applicationID)
    }

    func storeToken() {
        let token = tokenDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty, token.unicodeScalars.allSatisfy({ !CharacterSet.controlCharacters.contains($0) }) else {
            status = "Enter a valid bot token"; return
        }
        let data = Data(token.utf8)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service, kSecAttrAccount as String: account]
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let update = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        let result = update == errSecItemNotFound ? SecItemAdd(add as CFDictionary, nil) : update
        tokenDraft = ""
        status = result == errSecSuccess ? "Token saved securely" : "Could not save token to Keychain"
    }

    func removeToken() {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                       kSecAttrService as String: service, kSecAttrAccount as String: account] as CFDictionary)
        tokenDraft = ""
        if connected { disconnect(message: "Token removed") } else { status = "Token removed" }
    }

    func connect() {
        guard !connected, !busy else { return }
        guard DiscordRequestPolicy.validSnowflake(serverID), DiscordRequestPolicy.validSnowflake(channelID),
              !allowedUserIDs.isEmpty, allowedUserIDs.count == userIDsText.split(whereSeparator: { $0 == "," || $0.isWhitespace }).filter({ DiscordRequestPolicy.validSnowflake(String($0)) }).count,
              let token = readToken() else {
            status = "Add a valid server, channel, allowed user ID, and bot token"; return
        }
        busy = true; status = "Checking Discord settings…"
        let config = currentConfig
        activeConfig = config
        sessionID = UUID()
        let run = sessionID
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        configuration.httpShouldSetCookies = false
        redirectDelegate = DiscordRedirectDelegate()
        session = URLSession(configuration: configuration, delegate: redirectDelegate, delegateQueue: nil)
        generation = run
        pollTask = Task { [weak self] in
            guard let self else { return }
            do {
                let channel: Channel = try await self.request("/channels/\(config.channelID)", token: token)
                guard channel.guild_id == config.serverID else { throw RemoteError.validation("That channel is not in the selected server") }
                let user: DiscordUser = try await self.request("/users/@me", token: token)
                guard DiscordRequestPolicy.validSnowflake(user.id), user.bot == true else { throw RemoteError.validation("The token does not belong to a bot") }
                self.botID = user.id
                let latest: [Message] = try await self.request("/channels/\(config.channelID)/messages?limit=1", token: token)
                guard self.generation == run else { return }
                // Every new connection discards messages sent while offline.
                let seed = latest.first?.id ?? Self.currentSnowflake()
                self.saveCursor(seed, config: config, botID: user.id)
                self.connected = true; self.busy = false; self.status = "Connected · listening for new commands"
                self.addActivity("Connected; earlier messages ignored")
                self.retryDelay = 2
                await self.pollLoop(token: token, generation: run, config: config)
            } catch {
                guard self.generation == run else { return }
                self.busy = false
                self.disconnect(message: self.safeError(error))
            }
        }
    }

    func disconnect(message: String = "Disconnected") {
        generation = UUID()
        sessionID = generation
        pollTask?.cancel(); pollTask = nil
        workerTask?.cancel(); workerTask = nil; queue.removeAll()
        session.invalidateAndCancel()
        session = URLSession(configuration: .ephemeral)
        connected = false; busy = false; botID = ""; activeConfig = nil
        status = message
    }

    /// Send a result only into the session and channel that accepted the request.
    @discardableResult
    func sendReply(_ text: String, sessionID expectedSession: UUID, channelID targetChannelID: String) async -> Bool {
        guard connected, sessionID == expectedSession, let config = activeConfig,
              config.channelID == targetChannelID, let token = readToken(),
              DiscordRequestPolicy.validSnowflake(targetChannelID) else { return false }
        let bounded = String(text.prefix(6000))
        guard !bounded.isEmpty else { return false }
        let pieces = Self.chunks(bounded, maximum: 1800)
        for piece in pieces.prefix(4) {
            guard connected, sessionID == expectedSession, activeConfig?.channelID == targetChannelID else { return false }
            do { try await sendMessage(piece, token: token, channelID: targetChannelID, sessionID: expectedSession) }
            catch RemoteError.unauthorized {
                disconnect(message: "Discord rejected access (401/403); check bot permissions and reconnect")
                return false
            }
            catch { addActivity("Reply could not be sent"); return false }
        }
        return connected && sessionID == expectedSession && activeConfig?.channelID == targetChannelID
    }

    /// Upload caller-authorized job output. Data is bounded in memory and never persisted here.
    @discardableResult
    func sendArtifact(data: Data, filename: String, sessionID expectedSession: UUID,
                      channelID targetChannelID: String) async -> Bool {
        guard connected, sessionID == expectedSession, activeConfig?.channelID == targetChannelID,
              let token = readToken(), !data.isEmpty, data.count <= 20 * 1024 * 1024 else { return false }
        let safeName = Self.safeFilename(filename)
        guard !safeName.isEmpty else { return false }
        let boundary = "MaviDiscord-\(UUID().uuidString)"
        var body = Data()
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n{\"allowed_mentions\":{\"parse\":[]}}\r\n".utf8))
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"\(safeName)\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        var request = URLRequest(url: endpoint("channels/\(targetChannelID)/messages"))
        request.httpMethod = "POST"
        request.setValue("Bot \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        var attempts = 0
        while connected && sessionID == expectedSession {
            do {
                let _: Message = try await perform(request)
                return connected && sessionID == expectedSession
            } catch RemoteError.rateLimited(let seconds) where attempts < 2 {
                attempts += 1
                do { try await Task.sleep(nanoseconds: UInt64(min(max(seconds, 1), 60) * 1_000_000_000)) }
                catch { return false }
            } catch RemoteError.unauthorized {
                disconnect(message: "Discord rejected access (401/403); check bot permissions and reconnect")
                return false
            } catch {
                addActivity("Artifact upload could not be completed")
                return false
            }
        }
        return false
    }

    private func pollLoop(token: String, generation run: UUID, config: Config) async {
        while !Task.isCancelled && connected && generation == run {
            do {
                let cursor = loadCursor(config: config, botID: botID)
                let suffix = cursor.map { "?after=\($0)&limit=50" } ?? "?limit=50"
                let messages: [Message] = try await request("/channels/\(config.channelID)/messages\(suffix)", token: token)
                guard connected && generation == run else { return }
                let chronological = messages.sorted { DiscordRequestPolicy.snowflakeLess($0.id, $1.id) }
                if chronological.count == 50, let cursor, let oldest = chronological.first?.id,
                   DiscordRequestPolicy.snowflakeLess(cursor, oldest) {
                    disconnect(message: "Stopped safely: message gap may exceed the polling limit; review Discord before reconnecting")
                    return
                }
                for message in chronological {
                    guard connected && generation == run else { return }
                    if !DiscordRequestPolicy.isAfterCursor(message.id, cursor: loadCursor(config: config, botID: botID)) { continue }
                    await consume(message, token: token, generation: run, config: config)
                    guard connected && generation == run else { return }
                }
                retryDelay = 2
                try await Task.sleep(nanoseconds: 5_000_000_000)
            } catch is CancellationError { return }
            catch RemoteError.unauthorized {
                disconnect(message: "Discord rejected access (401/403); check bot permissions and reconnect")
                return
            } catch RemoteError.rateLimited(let seconds) {
                do { try await Task.sleep(nanoseconds: UInt64(min(max(seconds, 1), 60) * 1_000_000_000)) } catch { return }
            } catch {
                status = "Discord connection interrupted; retrying"
                addActivity("Temporary Discord request failure")
                do { try await Task.sleep(nanoseconds: retryDelay * 1_000_000_000) } catch { return }
                retryDelay = min(retryDelay * 2, 60)
            }
        }
    }

    private func consume(_ message: Message, token: String, generation run: UUID, config: Config) async {
        let attachments = (message.attachments ?? []).map {
            DiscordRemoteAttachment(id: $0.id, url: $0.url, filename: $0.filename, size: $0.size, contentType: $0.content_type)
        }
        let authorID = message.author?.id ?? ""
        let parsed = DiscordRequestPolicy.parse(content: message.content ?? "", authorID: authorID,
            allowedUserIDs: config.allowedUserIDs, channelID: message.channel_id, expectedChannelID: config.channelID,
            guildID: message.guild_id, expectedGuildID: config.serverID,
            isBot: (message.author?.bot ?? false) || authorID == botID,
            webhookID: message.webhook_id, attachments: attachments)
        // Ignored messages are durably consumed. Accepted commands are recorded in the queue first.
        guard let parsed else { saveCursor(message.id, config: config, botID: botID); return }
        if ["task", "steer", "stop", "answer"].contains(parsed.command), !allowTasks {
            saveCursor(message.id, config: config, botID: botID)
            addActivity("Task command ignored; remote tasks are disabled")
            _ = await sendReply("Remote task commands are disabled in Mavi settings.", sessionID: run, channelID: config.channelID)
            return
        }
        guard queue.count < 5 else {
            saveCursor(message.id, config: config, botID: botID)
            addActivity("Command ignored; remote queue is full")
            _ = await sendReply("Mavi’s remote task queue is full. Try again later.", sessionID: run, channelID: config.channelID)
            return
        }
        let request = DiscordRemoteRequest(id: message.id, channelID: message.channel_id,
            authorID: message.author?.id ?? "", command: parsed.command, text: parsed.text,
            attachments: attachments, sessionID: run)
        addActivity("Accepted \(parsed.command) command")
        queue.append(request)
        saveCursor(message.id, config: config, botID: botID)
        if workerTask == nil { startWorker(token: token, generation: run) }
    }

    private func startWorker(token: String, generation run: UUID) {
        workerTask = Task { [weak self] in
            guard let self else { return }
            while self.connected && self.generation == run && !Task.isCancelled && !self.queue.isEmpty {
                let item = self.queue.removeFirst()
                guard item.sessionID == self.sessionID,
                      self.allowedUserIDs.contains(item.authorID),
                      !["task", "steer", "stop", "answer"].contains(item.command) || self.allowTasks else {
                    self.addActivity("Queued command ignored after settings changed")
                    continue
                }
                guard let handler = self.handler else {
                    _ = await self.sendReply("Mavi’s task handler is unavailable.", sessionID: item.sessionID, channelID: item.channelID)
                    continue
                }
                // The handler owns dispatch. Polling continues independently while it runs.
                let result = await handler(item)
                guard self.connected && self.generation == run else { return }
                _ = await self.sendReply(result, sessionID: item.sessionID, channelID: item.channelID)
            }
            if self.generation == run { self.workerTask = nil }
        }
    }

    private func request<T: Decodable>(_ path: String, token: String) async throws -> T {
        var req = URLRequest(url: endpoint(path))
        req.httpMethod = "GET"; req.setValue("Bot \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await perform(req)
    }

    private func perform<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw RemoteError.transport }
        if http.statusCode == 401 || http.statusCode == 403 { throw RemoteError.unauthorized }
        if http.statusCode == 429 {
            let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let seconds = (payload?["retry_after"] as? NSNumber)?.doubleValue ?? 5
            throw RemoteError.rateLimited(seconds)
        }
        guard (200..<300).contains(http.statusCode) else { throw RemoteError.http(http.statusCode) }
        do { return try JSONDecoder().decode(T.self, from: data) } catch { throw RemoteError.decode }
    }

    private func sendMessage(_ content: String, token: String, channelID targetChannelID: String,
                             sessionID expectedSession: UUID) async throws {
        var req = URLRequest(url: endpoint("channels/\(targetChannelID)/messages"))
        req.httpMethod = "POST"; req.setValue("Bot \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["content": content, "allowed_mentions": ["parse": [String]()]] as [String: Any])
        var attempts = 0
        while connected && sessionID == expectedSession {
            do { let _: Message = try await perform(req); return }
            catch RemoteError.rateLimited(let seconds) where attempts < 2 {
                attempts += 1
                try await Task.sleep(nanoseconds: UInt64(min(max(seconds, 1), 60) * 1_000_000_000))
            }
        }
        throw CancellationError()
    }

    private func endpoint(_ path: String) -> URL {
        let parts = path.split(separator: "?", maxSplits: 1).map(String.init)
        var components = URLComponents(url: baseURL.appendingPathComponent(parts[0]), resolvingAgainstBaseURL: false)!
        if parts.count == 2 {
            components.queryItems = parts[1].split(separator: "&").compactMap { pair in
                let values = pair.split(separator: "=", maxSplits: 1).map(String.init)
                guard values.count == 2 else { return nil }
                return URLQueryItem(name: values[0], value: values[1])
            }
        }
        return components.url!
    }

    private func readToken() -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: account,
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data, let token = String(data: data, encoding: .utf8), !token.isEmpty else { return nil }
        return token
    }

    private func cursorKey(config: Config, botID: String) -> String {
        "discord.remote.cursor.\(config.serverID).\(config.channelID).\(botID)"
    }
    private func loadCursor(config: Config, botID: String) -> String? {
        defaults.string(forKey: cursorKey(config: config, botID: botID))
    }
    private func saveCursor(_ value: String, config: Config, botID: String) {
        defaults.set(value, forKey: cursorKey(config: config, botID: botID))
    }
    private func configurationChanged(_ changed: Bool) {
        if changed && (connected || busy) { disconnect(message: "Settings changed; reconnect to Discord") }
    }
    private func persistSettings() {
        defaults.set(serverID, forKey: "discord.remote.serverID")
        defaults.set(channelID, forKey: "discord.remote.channelID")
        defaults.set(userIDsText, forKey: "discord.remote.userIDs")
        defaults.set(applicationID, forKey: "discord.remote.applicationID")
    }
    private func addActivity(_ value: String) {
        activity.append(String(value.prefix(100)))
        if activity.count > 20 { activity.removeFirst(activity.count - 20) }
    }
    private func safeError(_ error: Error) -> String {
        if let remote = error as? RemoteError {
            switch remote {
            case .validation(let message): return message
            case .unauthorized: return "Discord rejected access (401/403); check token and bot permissions"
            case .rateLimited: return "Discord rate limited setup; retry in a moment"
            case .http(let code): return "Discord setup failed (HTTP \(code))"
            case .decode: return "Discord returned an unexpected response"
            case .transport: return "Could not reach Discord"
            }
        }
        return "Could not connect to Discord"
    }
    private static func chunks(_ text: String, maximum: Int) -> [String] {
        var result: [String] = [], remaining = text
        while !remaining.isEmpty {
            let end = remaining.index(remaining.startIndex, offsetBy: min(maximum, remaining.count))
            result.append(String(remaining[..<end])); remaining = String(remaining[end...])
        }
        return result
    }
    private static func safeFilename(_ filename: String) -> String {
        let leaf = URL(fileURLWithPath: filename).lastPathComponent
        return String(leaf.map { character in
            character.isLetter || character.isNumber || "._-".contains(character) ? character : "_"
        }.prefix(100))
    }
    private static func currentSnowflake() -> String {
        let milliseconds = max(0, Int64(Date().timeIntervalSince1970 * 1000) - 1_420_070_400_000)
        return String(UInt64(milliseconds) << 22)
    }

    private enum RemoteError: Error {
        case validation(String), unauthorized, rateLimited(Double), http(Int), decode, transport
    }
    private struct Channel: Decodable { let guild_id: String? }
    private struct DiscordUser: Decodable { let id: String; let bot: Bool? }
    private struct Author: Decodable { let id: String; let bot: Bool? }
    private struct Attachment: Decodable {
        let id: String
        let url: String
        let filename: String
        let size: Int
        let content_type: String?
    }
    private struct Message: Decodable {
        let id: String
        let channel_id: String
        let guild_id: String?
        let content: String?
        let author: Author?
        let webhook_id: String?
        let attachments: [Attachment]?
    }
}

private final class DiscordRedirectDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        guard request.url?.scheme == "https", request.url?.host == "discord.com" else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }
}
