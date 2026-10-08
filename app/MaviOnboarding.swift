import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct MaviOnboardingPanel: View {
    private enum Step { case welcome, personalize }

    @ObservedObject var desk: Desk
    @Environment(\.dismiss) private var dismiss
    @State private var step: Step = .welcome
    @State private var draft = ""
    @State private var copiedPrompt = false
    @State private var error = ""

    private let profileLimit = 4_500
    private let fileByteLimit = 32_768
    private static let completionKey = "MaviPersonalizationOnboardingComplete"
    private static let summaryPrompt = """
    Create a short preference summary for a local assistant called Mavi using only conversation history you can actually see in this chat. Do not assume you can access all of my ChatGPT history. If your context is limited, say so. Capture stable preferences for response style, formatting, accessibility, and recurring workflows. Exclude names, contact details, account or project identifiers, credentials, and sensitive personal details. Do not quote conversations or add facts that are not supported. Return only the concise summary, under 4,500 characters, so I can review and edit it before saving locally.
    """

    var body: some View {
        Group {
            switch step {
            case .welcome: welcome
            case .personalize: personalize
            }
        }
        .padding(28)
        .frame(width: 640, height: step == .welcome ? 500 : 700)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 12) {
                Image(systemName: "sparkles").font(.system(size: 26)).foregroundStyle(.tint)
                Text("Welcome to Mavi").font(.system(size: 25, weight: .semibold))
            }
            Text("Choose how Mavi should start.").font(.headline)
            Text("Continue clean keeps saved preference context and the personal adapter turned off. Mavi does not inspect your ChatGPT history or train itself in the background.")
                .font(.body).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("Personalize lets you bring in a short preference summary that you create and review. It is saved on this Mac and can be edited or deleted later.")
                .font(.body).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            HStack {
                Button("Continue clean") { continueClean() }
                    .buttonStyle(.bordered)
                Spacer()
                Button("Personalize") { draft = desk.profileText; step = .personalize }
                    .buttonStyle(.borderedProminent)
            }
        }
    }

    private var personalize: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Button("Back") { error = ""; step = .welcome }.buttonStyle(.plain)
                Spacer()
                Text("Personalize Mavi").font(.system(size: 21, weight: .semibold))
                Spacer()
                Button("Continue clean") { continueClean() }.buttonStyle(.bordered)
            }

            Text("Ask ChatGPT for a brief from the history it can see, then bring that summary here. ChatGPT may not have access to every conversation. Review it for private details before saving.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)

            Text("Copy this prompt into ChatGPT").font(.headline)
            ScrollView {
                Text(Self.summaryPrompt).font(.system(size: 12, design: .monospaced))
                    .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(12)
            }
            .frame(height: 112).background(Color.secondary.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 10))

            HStack {
                Button(copiedPrompt ? "Prompt copied" : "Copy prompt") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(Self.summaryPrompt, forType: .string)
                    copiedPrompt = true
                }.buttonStyle(.bordered)
                Button("Import .txt or .md") { importPreferences() }.buttonStyle(.bordered)
                Spacer()
                Text("\(draft.count)/\(profileLimit) characters").font(.caption.monospacedDigit()).foregroundStyle(draft.count > profileLimit ? .red : .secondary)
            }

            Text("Review and edit before saving. This preference note is prompt context only; it does not train a model. Training requires a separate dataset import and a separate action.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextEditor(text: $draft).font(.system(size: 13)).scrollContentBackground(.hidden)
                .padding(8).background(Color.secondary.opacity(0.06)).clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(alignment: .topLeading) {
                    if draft.isEmpty {
                        Text("Paste a summary or import a text file, then edit it here…")
                            .font(.system(size: 13)).foregroundStyle(.tertiary).padding(.horizontal, 13).padding(.vertical, 16).allowsHitTesting(false)
                    }
                }
                .accessibilityLabel("Editable local preference summary")

            if !error.isEmpty { Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                Text("Saved locally in Mavi’s personal preferences folder. No history or network import.")
                    .font(.caption2).foregroundStyle(.tertiary).fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button("Save local preferences") { savePreferences() }
                    .buttonStyle(.borderedProminent)
                    .disabled(desk.busy || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draft.count > profileLimit)
            }
        }
    }

    private func continueClean() {
        desk.profileText = ""
        desk.personalEnabled = false
        UserDefaults.standard.set(true, forKey: Self.completionKey)
        dismiss()
    }

    private func importPreferences() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.plainText, UTType(filenameExtension: "md") ?? .plainText]
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Preview text"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        guard ["txt", "md"].contains(url.pathExtension.lowercased()) else {
            error = "Choose a .txt or .md file."
            return
        }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let values = try url.resourceValues(forKeys: [.fileSizeKey])
            guard let size = values.fileSize, size <= fileByteLimit else {
                throw OnboardingImportError.tooLarge
            }
            let handle = try FileHandle(forReadingFrom: url)
            defer { try? handle.close() }
            guard let data = try handle.read(upToCount: fileByteLimit + 1),
                  data.count <= fileByteLimit, let text = String(data: data, encoding: .utf8) else {
                throw OnboardingImportError.invalidText
            }
            draft = text
            error = ""
            copiedPrompt = false
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func savePreferences() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, text.count <= profileLimit, text.utf8.count <= fileByteLimit else {
            error = "Keep the preference summary under 4,500 characters and 32 KB."
            return
        }
        do {
            try FileManager.default.createDirectory(at: desk.personalURL, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let url = desk.personalURL.appendingPathComponent("profile.txt")
            try Data(text.utf8).write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            desk.profileText = text
            desk.personalEnabled = true
            UserDefaults.standard.set(true, forKey: Self.completionKey)
            dismiss()
        } catch {
            self.error = "Could not save local preferences: \(error.localizedDescription)"
        }
    }
}

private enum OnboardingImportError: LocalizedError {
    case tooLarge
    case invalidText

    var errorDescription: String? {
        switch self {
        case .tooLarge: return "Choose a .txt or .md file no larger than 32 KB."
        case .invalidText: return "The selected file must be UTF-8 text no larger than 32 KB."
        }
    }
}
