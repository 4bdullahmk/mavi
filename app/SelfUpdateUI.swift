import SwiftUI
import AppKit

@MainActor final class SelfUpdateState: ObservableObject {
    @Published var model = "qwen3.8:27b"
    @Published var prompt = ""
    @Published var log = ""
    @Published var status = "Describe a change to Mavi. Qwen will build a candidate locally."
    @Published var busy = false
    @Published var runPath = ""
    @Published var candidatePath = ""
    @Published var automaticInstall = false
    var process: Process?
    init() {
        let saved=UserDefaults.standard.string(forKey:"WorkDeskUpdateCandidate") ?? ""
        if FileManager.default.fileExists(atPath:saved+"/manifest.json") { runPath=saved; candidatePath=saved+"/Mavi.app" }
    }
    func event(_ data:Data) {
        guard let value=(try? JSONSerialization.jsonObject(with:data)) as? [String:String],let type=value["type"] else { return }
        if type == "token" || type == "log" { log=String((log+(value["text"] ?? "")).suffix(80000)) }
        if type == "ready" {
            runPath=value["run"] ?? ""; candidatePath=value["app"] ?? ""; status="Build and baseline tests passed"
            UserDefaults.standard.set(runPath,forKey:"WorkDeskUpdateCandidate")
        }
        if type == "error" { status="Failed: "+(value["text"] ?? "Unknown error") }
    }
    func start() {
        guard !busy,!prompt.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else { return }
        guard let resources=Bundle.main.resourceURL,let script=Bundle.main.url(forResource:"SelfUpdate",withExtension:"py") else { status="Self-update tools are missing";return }
        let source=resources.appendingPathComponent("Source")
        guard FileManager.default.fileExists(atPath:source.path) else { status="Bundled source is missing";return }
        busy=true;status="Local Qwen is preparing the update…";log="";runPath="";candidatePath=""
        UserDefaults.standard.removeObject(forKey:"WorkDeskUpdateCandidate")
        let p=Process();let pipe=Pipe();p.executableURL=URL(fileURLWithPath:"/usr/bin/python3");p.arguments=[script.path,source.path,prompt];p.standardOutput=pipe;p.standardError=FileHandle.nullDevice
        do { var environment=ProcessInfo.processInfo.environment;environment["MAVI_CODER_MODEL"]=model;p.environment=environment
        try p.run();process=p } catch { busy=false;status=error.localizedDescription;return }
        Task {
            await Task.detached {
                var buffer=Data()
                while true {
                    let chunk=pipe.fileHandleForReading.availableData
                    if chunk.isEmpty { break }
                    buffer.append(chunk)
                    while let newline=buffer.firstIndex(of:10) {
                        let line=Data(buffer[..<newline]);buffer.removeSubrange(...newline)
                        await self.event(line)
                    }
                }
                if !buffer.isEmpty { await self.event(buffer) }
                p.waitUntilExit()
            }.value
            process=nil;busy=false
            if p.terminationStatus != 0 && !status.hasPrefix("Failed") { status="Stopped or failed; active app unchanged" }
            else if p.terminationStatus == 0 && runPath.isEmpty { status="No tested candidate was produced" }
            if p.terminationStatus == 0 && !runPath.isEmpty && automaticInstall { install() }
        }
    }
    func cancel() { process?.terminate();status="Cancelling…" }
    func install() { prepare(rollback:false) }
    func rollback() { prepare(rollback:true) }
    func prepare(rollback:Bool) {
        guard !busy,rollback || !runPath.isEmpty,let script=Bundle.main.url(forResource:"UpdateInstaller",withExtension:"py") else { return }
        busy=true;status="Signing update. Approve the macOS keychain prompt if shown…"
        let args=rollback ? ["rollback",Bundle.main.bundleURL.path,String(ProcessInfo.processInfo.processIdentifier)] : ["prepare",runPath,Bundle.main.bundleURL.path,String(ProcessInfo.processInfo.processIdentifier)]
        Task {
            do {
                let p=Process();let pipe=Pipe();p.executableURL=URL(fileURLWithPath:"/usr/bin/python3");p.arguments=[script.path]+args;p.standardOutput=pipe;p.standardError=FileHandle.nullDevice
                try p.run();process=p
                let data=await Task.detached { let data=pipe.fileHandleForReading.readDataToEndOfFile();p.waitUntilExit();return data }.value
                let result=try JSONSerialization.jsonObject(with:data) as? [String:String] ?? [:]
                if let message=result["error"] { throw DeskError(message) }
                guard p.terminationStatus==0,let ticket=result["ticket"] else { throw DeskError("Installer did not produce a valid ticket") }
                // Copy the trusted installer outside the app before replacing that bundle.
                let helper=URL(fileURLWithPath:ticket).deletingLastPathComponent().appendingPathComponent("CommitInstaller.py")
                try Data(contentsOf:script).write(to:helper,options:.atomic)
                let commit=Process();commit.executableURL=URL(fileURLWithPath:"/usr/bin/python3");commit.arguments=[helper.path,"commit",ticket];commit.standardInput=FileHandle.nullDevice;commit.standardOutput=FileHandle.nullDevice;commit.standardError=FileHandle.nullDevice
                try commit.run();UserDefaults.standard.removeObject(forKey:"WorkDeskUpdateCandidate");NSApp.terminate(nil)
            } catch { status="Failed: "+error.localizedDescription;busy=false;process=nil }
        }
    }
}
struct SelfUpdateUI: View {
    @ObservedObject var desk:Desk
    @ObservedObject var state:SelfUpdateState
    var body:some View {
        VStack(alignment:.leading,spacing:12) {
            Text("Improve Mavi").font(.headline)
            MaviReleaseDownloads()
            Text("Describe a focused change. Local Qwen edits a copy, compiles it, runs the original tests, and can repair one failed build.").font(.caption).foregroundStyle(.secondary)
            GroupBox("Request") { 
                VStack(alignment:.leading,spacing:12) {
                    TextField("Example: add a keyboard holder preset…",text:$state.prompt,axis:.vertical).lineLimit(2...5).textFieldStyle(.roundedBorder).disabled(state.busy)
                    DictationButton(target:$state.prompt).disabled(state.busy)
                    Picker("Local builder",selection:$state.model) {Text("Qwen 3.8 27B").tag(desk.researchModel);Text("Qwen Coder 30B").tag(desk.coderModel)}.pickerStyle(.menu).disabled(state.busy)
                    Toggle("Install automatically after passing checks",isOn:$state.automaticInstall).disabled(state.busy)
                }
            }
            GroupBox("Build activity") { 
                VStack(alignment:.leading,spacing:12) {
                    HStack { Button("Build update") { state.start() }.disabled(state.busy || desk.busy || state.prompt.isEmpty);if state.busy { Button("Stop") { state.cancel() } } }
                    Text(state.status).font(.caption).textSelection(.enabled)
                    ScrollView { Text(state.log.isEmpty ? "Requests, generated patches and build output appear here." : state.log).font(.system(size:10,design:.monospaced)).frame(maxWidth:.infinity,alignment:.leading).textSelection(.enabled) }.frame(minHeight:160,maxHeight:320).padding(8).background(Color(nsColor:.textBackgroundColor)).cornerRadius(8)
                }
            }
            GroupBox("Installation") { 
                VStack(alignment:.leading,spacing:12) {
                    HStack {
                        Button("Validation report") { NSWorkspace.shared.open(URL(fileURLWithPath:state.runPath+"/validation-report.json")) }.disabled(state.runPath.isEmpty || !FileManager.default.fileExists(atPath:state.runPath+"/validation-report.json"))
                        Button("Changes") { NSWorkspace.shared.open(URL(fileURLWithPath:state.runPath+"/diff.patch")) }.disabled(state.runPath.isEmpty)
                        Button("Build folder") { NSWorkspace.shared.selectFile(state.runPath,inFileViewerRootedAtPath:"") }.disabled(state.runPath.isEmpty)
                    }
                    HStack { Button("Install & restart") { state.install() }.disabled(state.busy || desk.busy || state.runPath.isEmpty);Button("Restore previous build") { state.rollback() }.disabled(state.busy || desk.busy || !FileManager.default.fileExists(atPath:FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Mavi/Updates/last-install.json").path)) }
                }
            }
            Text("The previous build is saved separately. Failed startup restores it automatically. macOS may request signing-key access. Build tests cover checked behaviors, not visual UI correctness. One recovery build is retained after cleanup.").font(.caption2).foregroundStyle(.secondary)
        }
    }
}
