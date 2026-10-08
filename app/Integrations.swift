import SwiftUI
import AppKit
struct IntegrationsPanel:View {
    @ObservedObject var desk:Desk
    @ObservedObject var remote:DiscordRemote
    @ObservedObject var remoteWorkspace:DiscordWorkspace
    func installed(_ relative:String) -> Bool {FileManager.default.fileExists(atPath:desk.imageRuntime.appendingPathComponent(relative).path)}
    var body:some View {
        VStack(alignment:.leading,spacing:18) {
            Text("Integrations & local tools").font(.title2.bold())
            Text("These tools are connected to Mavi’s workspaces. External account connections require their own sign-in.").foregroundStyle(.secondary)
            DiscordPanel(remote:remote,workspace:remoteWorkspace)
            row("Visible browser","Open links, navigate, fill forms and download files with local Qwen.",ready:installed("browser-venv/bin/python"),mode:7)
            row("Offline dictation","Whisper Turbo on Apple Silicon. Dictate into task fields and edit before sending.",ready:installed("whisper-turbo/config.json"),mode:0)
            row("Documents & spreadsheets","Read/write PDF, DOCX, XLSX, CSV and text; export and share files.",ready:installed("files-venv/bin/python"),mode:4)
            row("CAD & Bambu Studio","Parametric OpenSCAD / CadQuery, mesh checks, STL export and Bambu launch.",ready:installed("cad-venv/bin/python"),mode:5)
            row("Local developer","Read a selected project and propose code patches; choose a coding model.",ready:desk.coderReady,mode:2)
            row("Stocks & thinkorswim exports","CSV analysis, local source review and hypothetical trade plans. Broker API not connected.",ready:true,mode:8)
            HStack {Link("Hugging Face model catalog",destination:URL(string:"https://huggingface.co/models")!);Link("Schwab API setup",destination:URL(string:"https://developer.schwab.com/products/trader-api--individual")!)}
            Button("Recheck tools & models") {Task {await desk.start()}}
            Text("Plugins installed in Codex are separate from this app. Mavi currently uses the integrations listed here; it does not inherit Codex account access.").font(.caption).foregroundStyle(.secondary)
        }.padding(12)
    }
    func row(_ title:String,_ detail:String,ready:Bool,mode:Int)->some View {
        HStack(alignment:.top) {Image(systemName:ready ? "checkmark.circle.fill" : "circle.dashed").foregroundStyle(ready ? .green : .orange);VStack(alignment:.leading) {Text(title).bold();Text(detail).font(.caption).foregroundStyle(.secondary)};Spacer();Button("Open") {desk.mode=mode}}.padding(12).background(Color(nsColor:.controlBackgroundColor)).cornerRadius(10)
    }
}
