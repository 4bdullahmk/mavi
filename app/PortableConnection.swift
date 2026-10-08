import Foundation

struct PortableDiscordStatus: Sendable {
    enum Availability: Sendable { case available, unknown }

    let availability: Availability
    let connected: Bool?
    let configured: Bool?
    let summary: String

    static let unknown = PortableDiscordStatus(
        availability: .unknown,
        connected: nil,
        configured: nil,
        summary: "Local web Mavi is unavailable; Discord status is unknown."
    )
}

enum PortableConnection {
    static let setupURL = URL(string: "http://127.0.0.1:8769/")!

    /// Reads only portable Discord status over the already authenticated local
    /// session. The portable token and all other workspace fields stay local to
    /// this method and are never returned, logged, persisted, or sent to a model.
    static func status() async -> PortableDiscordStatus {
        guard let cookie = await sessionCookie() else { return .unknown }

        var stateRequest = request(path: "/api/state")
        stateRequest.setValue(cookie, forHTTPHeaderField: "Cookie")
        guard let (stateData, stateResponse) = try? await fetch(stateRequest, limit: 2 * 1024 * 1024),
              isExpected(stateResponse), stateResponse.statusCode == 200,
              let root = try? JSONSerialization.jsonObject(with: stateData) as? [String: Any],
              let discord = root["discord"] as? [String: Any],
              let connected = discord["connected"] as? Bool,
              let configured = discord["configured"] as? Bool,
              let rawStatus = discord["status"] as? String else {
            return .unknown
        }

        // Map the server's free-form text to allowlisted summaries so local
        // paths, IDs, or future diagnostic details cannot leak into native UI.
        let summary: String
        if connected {
            summary = "Connected in local web Mavi."
        } else if configured {
            summary = rawStatus == "Not connected"
                ? "Configured, but not connected in local web Mavi."
                : "Configured; local web Discord is not connected."
        } else {
            summary = "Not configured in local web Mavi."
        }
        return PortableDiscordStatus(availability: .available,
                                     connected: connected,
                                     configured: configured,
                                     summary: summary)
    }

    private static func request(path: String) -> URLRequest {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:8769\(path)")!,
                                 cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: 3)
        request.httpMethod = "GET"
        request.httpShouldHandleCookies = false
        return request
    }

    fileprivate static func isExpected(_ response: HTTPURLResponse) -> Bool {
        guard let url = response.url else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == 8769
    }

    private static func sessionCookie(from response: HTTPURLResponse) -> String? {
        guard let header = response.value(forHTTPHeaderField: "Set-Cookie"),
              let pair = header.split(separator: ";", maxSplits: 1).first.map(String.init),
              pair.hasPrefix("mavi_session="), pair.count <= 512,
              pair.unicodeScalars.allSatisfy({ !CharacterSet.controlCharacters.contains($0) }) else {
            return nil
        }
        return pair
    }

    private static func sessionCookie() async -> String? {
        guard let (_, response) = try? await fetch(request(path: "/"), limit: 256 * 1024),
              isExpected(response), response.statusCode == 200 else { return nil }
        return sessionCookie(from: response)
    }

    private static func fetch(_ request: URLRequest, limit: Int) async throws -> (Data, HTTPURLResponse) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 3
        configuration.timeoutIntervalForResource = 6
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        let receiver = BoundedResponse(limit: limit)
        let session = URLSession(configuration: configuration, delegate: receiver, delegateQueue: nil)
        defer { session.invalidateAndCancel() }

        return try await withCheckedThrowingContinuation { continuation in
            receiver.continuation = continuation
            let task = session.dataTask(with: request)
            task.resume()
        }
    }
}

private final class BoundedResponse: NSObject, URLSessionDataDelegate, URLSessionTaskDelegate {
    private let limit: Int
    private var data = Data()
    private var response: HTTPURLResponse?
    private var tooLarge = false
    var continuation: CheckedContinuation<(Data, HTTPURLResponse), Error>?

    init(limit: Int) { self.limit = limit }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse,
              PortableConnection.isExpected(http),
              (http.expectedContentLength < 0 || http.expectedContentLength <= Int64(limit)) else {
            tooLarge = (response.expectedContentLength > Int64(limit))
            completionHandler(.cancel)
            return
        }
        self.response = http
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive chunk: Data) {
        guard data.count + chunk.count <= limit else {
            tooLarge = true
            dataTask.cancel()
            return
        }
        data.append(chunk)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let continuation else { return }
        self.continuation = nil
        if tooLarge {
            continuation.resume(throwing: URLError(.dataLengthExceedsMaximum))
        } else if let error {
            continuation.resume(throwing: error)
        } else if let response {
            continuation.resume(returning: (data, response))
        } else {
            continuation.resume(throwing: URLError(.badServerResponse))
        }
        data.removeAll(keepingCapacity: false)
    }
}
