import SwiftUI
import AppKit
import WebKit
import UniformTypeIdentifiers
import Darwin
import Dispatch

@main
struct MaviWebEntry {
    @MainActor
    static func main() {
        if CommandLine.arguments.contains("--mavi-automation") {
            Task { @MainActor in
                let status = await runMacAutomationCommand()
                Darwin.exit(status)
            }
            dispatchMain()
        }
        MaviWebApp.main()
    }
}

struct MaviWebApp: App {
    @NSApplicationDelegateAdaptor(MaviWebAppDelegate.self) private var appDelegate
    @StateObject private var runtime = MaviWebRuntime.shared

    var body: some Scene {
        WindowGroup("Mavi") {
            MaviWebWindow(runtime: runtime)
                .frame(minWidth: 920, minHeight: 640)
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}

@MainActor
final class MaviWebAppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        MaviWebRuntime.shared.stop()
    }
}

@MainActor
final class MaviWebRuntime: ObservableObject {
    static let shared = MaviWebRuntime()
    @Published private(set) var address: URL?
    @Published private(set) var startupMessage = "Starting Mavi…"
    private var server: Process?
    private var healthTask: Task<Void, Never>?
    private var selectedPort: UInt16?
    private var stopped = false

    private var dataDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Mavi/data", isDirectory: true)
    }

    private var runtimeDirectory: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("MaviRuntime/portable", isDirectory: true)
    }

    func start() {
        guard server == nil, !stopped else { return }
        do {
            let data = dataDirectory
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: data.path)
            guard let runtime = runtimeDirectory,
                  FileManager.default.fileExists(atPath: runtime.appendingPathComponent("server.py").path) else {
                throw RuntimeError("The Mavi runtime is missing from this app bundle. Rebuild or reinstall Mavi.")
            }
            let python = try resolvePython()
            try checkDependencies(python: python, runtime: runtime)
            let port = try reserveLoopbackPort()
            selectedPort = port

            let process = Process()
            process.executableURL = python
            process.arguments = [runtime.appendingPathComponent("server.py").path,
                                "--port", String(port), "--no-browser"]
            process.currentDirectoryURL = runtime
            process.environment = [
                "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "MAVI_DATA_DIR": data.path,
                "MAVI_MAC_HELPER": Bundle.main.executableURL?.path ?? "",
                "PYTHONDONTWRITEBYTECODE": "1"
            ]
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            process.terminationHandler = { [weak self] ended in
                Task { @MainActor in
                    guard let self, !self.stopped else { return }
                    self.server = nil
                    self.healthTask?.cancel()
                    self.healthTask = nil
                    self.startupMessage = "Mavi's local service stopped (exit \(ended.terminationStatus)). Reopen the app to try again."
                }
            }
            try process.run()
            server = process
            startupMessage = "Connecting to Mavi's local service…"
            beginHealthChecks()
        } catch {
            startupMessage = error.localizedDescription
        }
    }

    func stop() {
        stopped = true
        healthTask?.cancel()
        healthTask = nil
        if let server, server.isRunning {
            server.terminate()
            server.waitUntilExit()
        }
        server = nil
    }

    private func resolvePython() throws -> URL {
        let fm = FileManager.default
        var candidates: [URL] = []
        let dataPython = dataDirectory.deletingLastPathComponent()
            .appendingPathComponent("runtime/bin/python3")
        candidates.append(dataPython)
        if let resources = Bundle.main.resourceURL {
            candidates.append(resources.appendingPathComponent("MaviRuntime/venv/bin/python3"))
        }
        if let configured = ProcessInfo.processInfo.environment["MAVI_PYTHON3"], !configured.isEmpty {
            candidates.insert(URL(fileURLWithPath: configured), at: 0)
        }
        candidates += ["/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3.11",
                      "/usr/local/bin/python3.12", "/usr/local/bin/python3.11", "/usr/bin/python3"]
            .map(URL.init(fileURLWithPath:))
        for candidate in candidates where fm.isExecutableFile(atPath: candidate.path) {
            if pythonIsSupported(candidate) { return candidate }
        }
        throw RuntimeError("Python 3.11 or newer was not found. Install Python 3.11+ from python.org, then run the setup instructions in README-Mac.md. You can set MAVI_PYTHON3 to a Python executable before launching Mavi.")
    }

    private func pythonIsSupported(_ python: URL) -> Bool {
        let task = Process()
        task.executableURL = python
        task.arguments = ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"]
        task.environment = ["HOME": FileManager.default.homeDirectoryForCurrentUser.path,
                            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]
        task.standardInput = FileHandle.nullDevice
        task.standardOutput = FileHandle.nullDevice
        task.standardError = FileHandle.nullDevice
        do { try task.run(); task.waitUntilExit(); return task.terminationStatus == 0 }
        catch { return false }
    }

    private func checkDependencies(python: URL, runtime: URL) throws {
        let task = Process()
        let result = Pipe()
        task.executableURL = python
        task.arguments = ["-c", "import PIL, pypdf, reportlab, docx, openpyxl, pptx, numpy, trimesh"]
        task.currentDirectoryURL = runtime
        task.environment = ["HOME": FileManager.default.homeDirectoryForCurrentUser.path,
                            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                            "PYTHONDONTWRITEBYTECODE": "1"]
        task.standardInput = FileHandle.nullDevice
        task.standardOutput = FileHandle.nullDevice
        task.standardError = result
        try task.run()
        task.waitUntilExit()
        guard task.terminationStatus == 0 else {
            let detail = String(data: result.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw RuntimeError("Mavi's Python packages are not ready. Follow README-Mac.md to create the local environment and install portable/requirements.txt.\n\n\(detail.trimmingCharacters(in: .whitespacesAndNewlines).suffix(500))")
        }
    }

    private func reserveLoopbackPort() throws -> UInt16 {
        let descriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw RuntimeError("Could not allocate a local Mavi port.") }
        defer { close(descriptor) }
        var reuseAddress: Int32 = 1
        let reuseResult = withUnsafePointer(to: &reuseAddress) {
            setsockopt(descriptor, SOL_SOCKET, SO_REUSEADDR, $0,
                       socklen_t(MemoryLayout<Int32>.size))
        }
        guard reuseResult == 0 else {
            throw RuntimeError("Could not prepare Mavi's local port for a safe restart.")
        }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = UInt16(8772).bigEndian
        address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0 else { throw RuntimeError("Mavi's local port 8772 is already in use. Close the other local service and reopen Mavi.") }
        var actual = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let result = withUnsafeMutablePointer(to: &actual) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(descriptor, $0, &length) }
        }
        guard result == 0 else { throw RuntimeError("Could not read the reserved local Mavi port.") }
        return UInt16(bigEndian: actual.sin_port)
    }

    private func beginHealthChecks() {
        let deadline = Date().addingTimeInterval(30)
        healthTask?.cancel()
        healthTask = Task { @MainActor [weak self] in
            guard let self, let port = self.selectedPort else { return }
            while !Task.isCancelled, Date() < deadline {
                guard self.server?.isRunning == true else { return }
                var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!, timeoutInterval: 1)
                request.httpMethod = "GET"
                do {
                    let (data, response) = try await URLSession.shared.data(for: request)
                    let isHTML = String(data: data, encoding: .utf8)?.lowercased().contains("<html") == true
                    if (response as? HTTPURLResponse)?.statusCode == 200, isHTML {
                        self.address = URL(string: "http://127.0.0.1:\(port)/")
                        self.startupMessage = "Mavi is running locally on this Mac."
                        self.reportInstallerHealthIfRequested()
                        self.healthTask = nil
                        return
                    }
                } catch { }
                if Task.isCancelled { return }
                try? await Task.sleep(nanoseconds: 250_000_000)
            }
            if !Task.isCancelled {
                self.startupMessage = "Mavi's local service did not become ready. Check that Python dependencies are installed, then reopen the app."
                self.healthTask = nil
            }
        }
    }

    private func reportInstallerHealthIfRequested() {
        guard let index = CommandLine.arguments.firstIndex(of: "--update-health-check"),
              CommandLine.arguments.indices.contains(index + 1) else { return }
        let marker = URL(fileURLWithPath: CommandLine.arguments[index + 1]).standardizedFileURL
        let installFolder = marker.deletingLastPathComponent()
        let applicationSupport = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support", isDirectory: true)
            .standardizedFileURL
        let markerParent = installFolder.deletingLastPathComponent().standardizedFileURL
        guard marker.lastPathComponent == "healthy",
              installFolder.lastPathComponent.hasPrefix("install-"),
              markerParent.path.hasPrefix(applicationSupport.path + "/") else { return }
        do {
            try Data("Mavi local service ready\n".utf8).write(to: marker, options: .atomic)
        } catch {
            startupMessage = "Mavi is running, but its update health marker could not be written."
        }
    }
}

private struct RuntimeError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct MaviWebWindow: View {
    @ObservedObject var runtime: MaviWebRuntime

    var body: some View {
        Group {
            if let address = runtime.address {
                MaviWebView(address: address)
                    .ignoresSafeArea(.container, edges: .bottom)
            } else {
                VStack(spacing: 14) {
                    Image(nsImage: NSApp.applicationIconImage)
                        .resizable().scaledToFit().frame(width: 72, height: 72)
                        .accessibilityHidden(true)
                    Text("Mavi").font(.largeTitle.weight(.semibold))
                    Text(runtime.startupMessage).multilineTextAlignment(.center).foregroundStyle(.secondary)
                    Button("Try again") { runtime.start() }
                        .disabled(runtime.startupMessage.hasPrefix("Starting") || runtime.startupMessage.hasPrefix("Connecting"))
                    Link("Setup instructions", destination: URL(string: "https://github.com/4bdullahmk/mavi/blob/main/README-Mac.md")!)
                }.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .onAppear { runtime.start() }
    }
}

private struct MaviWebView: NSViewRepresentable {
    let address: URL
    func makeCoordinator() -> Coordinator { Coordinator(allowedPort: address.port) }
    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.load(URLRequest(url: address))
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {
        guard view.url?.absoluteString != address.absoluteString else { return }
        view.load(URLRequest(url: address))
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        private let allowedHost = "127.0.0.1"
        private let allowedPort: Int?

        init(allowedPort: Int?) {
            self.allowedPort = allowedPort
            super.init()
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
            if url.scheme == "http", url.host == allowedHost, url.port == allowedPort {
                decisionHandler(.allow)
            } else if url.scheme == "https", navigationAction.navigationType == .linkActivated {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
            } else {
                decisionHandler(.cancel)
            }
        }

        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = navigationAction.request.url, url.scheme == "https" { NSWorkspace.shared.open(url) }
            return nil
        }

        func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
            let panel = NSOpenPanel()
            panel.allowsMultipleSelection = parameters.allowsMultipleSelection
            panel.canChooseDirectories = false
            panel.canChooseFiles = true
            panel.begin { response in completionHandler(response == .OK ? panel.urls : nil) }
        }

        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction,
                     didBecome download: WKDownload) {
            download.delegate = self
        }

        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse,
                     didBecome download: WKDownload) {
            download.delegate = self
        }

        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                      suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
            let panel = NSSavePanel()
            panel.nameFieldStringValue = suggestedFilename.isEmpty ? "Mavi-download" : URL(fileURLWithPath: suggestedFilename).lastPathComponent
            panel.canCreateDirectories = true
            panel.begin { result in
                guard result == .OK, let chosen = panel.url else { completionHandler(nil); return }
                var destination = chosen
                if FileManager.default.fileExists(atPath: destination.path) {
                    let stem = destination.deletingPathExtension().lastPathComponent
                    let ext = destination.pathExtension
                    var suffix = 1
                    repeat {
                        let name = ext.isEmpty ? "\(stem) (\(suffix))" : "\(stem) (\(suffix)).\(ext)"
                        destination = chosen.deletingLastPathComponent().appendingPathComponent(name)
                        suffix += 1
                    } while FileManager.default.fileExists(atPath: destination.path)
                }
                completionHandler(destination)
            }
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) { }
    }
}
