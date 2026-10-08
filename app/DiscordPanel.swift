import AppKit
import SwiftUI

struct DiscordPanel: View {
    @ObservedObject var remote: DiscordRemote
    @ObservedObject var workspace: DiscordWorkspace

    @State private var confirmRemoteTasks = false
    @State private var portableStatus = PortableDiscordStatus.unknown
    @State private var checkingPortableStatus = false

    private var settingsLocked: Bool { remote.connected || remote.busy }
    private var inviteURL: URL? {
        guard DiscordRequestPolicy.validSnowflake(remote.applicationID) else { return nil }
        var parts = URLComponents(string: "https://discord.com/oauth2/authorize")
        parts?.queryItems = [
            URLQueryItem(name: "client_id", value: remote.applicationID),
            URLQueryItem(name: "scope", value: "bot"),
            URLQueryItem(name: "permissions", value: "101376")
        ]
        return parts?.url
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Text("M")
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .frame(width: 40, height: 40)
                    .background(Color.primary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Discord workspace").font(.title3.bold())
                    Text("Private channel front end for Mavi.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            setupGuide
            portableConnectionStatus
            connectionSettings
            accessOptions
            connectionStatus

            commandExamples

            Text("Discord requires an internet connection. This Mac must be awake and Mavi must remain open. Connections do not start automatically. Sign-ins and macOS permission prompts stay on the Mac. The bot token is stored in Keychain and is never included in activity or GitHub setup instructions.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(12)
        .confirmationDialog("Allow Discord task commands?", isPresented: $confirmRemoteTasks, titleVisibility: .visible) {
            Button("Enable remote tasks", role: .destructive) { remote.allowTasks = true }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("An allowed Discord user can start, steer, and stop Mavi tasks in this private channel. Tasks may use the local tools and workspaces currently available in Mavi, subject to each tool's existing safety checks. Sign-ins and macOS permission prompts stay on the Mac.")
        }
    }

    @State private var showSetupGuide = false

    private var portableConnectionStatus: some View {
        GroupBox("Local web Mavi · separate Discord connection") {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text(portableStatus.summary).font(.callout)
                    Spacer()
                    Button(checkingPortableStatus ? "Checking…" : "Refresh") {
                        refreshPortableStatus()
                    }
                    .disabled(checkingPortableStatus)
                }
                HStack(spacing: 10) {
                    Button("Open local web Discord setup") {
                        NSWorkspace.shared.open(PortableConnection.setupURL)
                    }
                    Text("Select Discord in the web app to view or configure its connection.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("This is separate from the native Mac bot connection below. Mavi reads only local connection status; it never copies the web bot token into Keychain or native settings.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .onAppear { refreshPortableStatus() }
    }

    @MainActor
    private func refreshPortableStatus() {
        guard !checkingPortableStatus else { return }
        checkingPortableStatus = true
        Task { @MainActor in
            portableStatus = await PortableConnection.status()
            checkingPortableStatus = false
        }
    }

    private var setupGuide: some View {
        DisclosureGroup(isExpanded: $showSetupGuide) {
            GroupBox("Set up a private bot") {
                VStack(alignment: .leading, spacing: 10) {
                Text("1. Create a Discord application and bot, then enable Message Content Intent in the Bot settings.")
                Link("Open Discord Developer Portal", destination: URL(string: "https://discord.com/developers/applications")!)
                Text("2. Copy the application ID, server ID, channel ID, and your user ID. In Discord, enable Developer Mode under Advanced, then use Copy ID on each item.")
                Text("3. Paste those IDs below and save the bot token directly into Keychain. Mavi never displays the saved token again.")
                Text("4. Invite the bot with only View Channel, Send Messages, Read Message History, and Attach Files. Administrator is not requested.")
                if let inviteURL {
                    Link("Generate least-privilege bot invite", destination: inviteURL)
                } else {
                    Text("Enter the application ID to generate the least-privilege invite link.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("Use a private channel. Anyone who can read it can see commands and Mavi's replies. Avoid secrets and confidential work unless the channel is appropriate for them.")
                    .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } label: {
            Text("Set up a private bot").font(.callout.bold())
        }
        .onAppear { showSetupGuide = !remote.hasToken }
    }

    private var connectionSettings: some View {
        GroupBox("Native Mac bot and private channel") {
            VStack(alignment: .leading, spacing: 10) {
                idField("Application ID", value: $remote.applicationID, help: "Used only to build the invite link")
                idField("Server ID", value: $remote.serverID, help: "Discord server (guild) snowflake")
                idField("Channel ID", value: $remote.channelID, help: "One channel only; earlier messages are ignored when connecting")
                VStack(alignment: .leading, spacing: 4) {
                    Text("Allowed user IDs").font(.caption.bold())
                    TextField("Your Discord user ID(s), comma separated", text: $remote.userIDsText)
                        .textFieldStyle(.roundedBorder).disabled(settingsLocked)
                    Text("Only these user IDs can issue commands in the selected server and channel.")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Bot token").font(.caption.bold())
                    SecureField("Paste bot token to save it in Keychain", text: $remote.tokenDraft)
                        .textFieldStyle(.roundedBorder).disabled(settingsLocked)
                    HStack {
                        Button("Save token to Keychain") { remote.storeToken() }
                            .disabled(settingsLocked || remote.tokenDraft.isEmpty)
                        Button("Remove saved token", role: .destructive) { remote.removeToken() }
                            .disabled(settingsLocked || !remote.hasToken)
                        Text(remote.hasToken ? "Saved in Keychain" : "No saved token")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack {
                    if remote.connected || remote.busy {
                        Button("Disconnect") { remote.disconnect() }
                    } else {
                        Button("Connect") { remote.connect() }
                            .disabled(!remote.hasToken || settingsLocked)
                    }
                    Text("Commands start with `!mavi`, such as `!mavi ask …`.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var accessOptions: some View {
        GroupBox("Remote access options") {
            VStack(alignment: .leading, spacing: 12) {
                Toggle(isOn: Binding(get: { remote.allowTasks }, set: { enabled in
                    if enabled && !remote.allowTasks { confirmRemoteTasks = true }
                    else {
                        remote.allowTasks = enabled
                    }
                })) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Allow remote Mavi tasks")
                        Text("Off by default. `ask` remains a separate one-turn call; task commands use Mavi's existing local tools and workspace permissions.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .disabled(settingsLocked)

                Text("Remote task commands control only the task started from this Discord session. They cannot expose, steer, or stop unrelated local Mavi work.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var connectionStatus: some View {
        GroupBox("Native Mac connection and activity") {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Circle().fill(remote.connected ? .green : (remote.busy ? .orange : .secondary)).frame(width: 8, height: 8)
                    Text(remote.status).font(.callout)
                    Spacer()
                }
                Text(workspace.status).font(.caption).foregroundStyle(.secondary)
                Text("Remote task: \(workspace.remoteTaskStatus)").font(.caption).foregroundStyle(.secondary)
                if remote.activity.isEmpty {
                    Text("No activity yet.").font(.caption).foregroundStyle(.tertiary)
                } else {
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(Array(remote.activity.suffix(8).enumerated()), id: \.offset) { _, item in
                            Text("• \(String(item.prefix(100)))").font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
                Text("Activity shows safe event summaries only; it does not display tokens or message contents.")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var commandExamples: some View {
        GroupBox("Command examples") {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(commandSamples, id: \.command) { sample in
                    HStack(spacing: 8) {
                        Text(sample.command)
                            .font(.system(size: 12, design: .monospaced))
                            .textSelection(.enabled)
                        Spacer()
                        Button("Copy") { copy(sample.command) }
                            .buttonStyle(.borderless)
                    }
                }
            }
        }
    }

    private var commandSamples: [(command: String, description: String)] {
        [
            ("!mavi ask …", "One-turn question."),
            ("!mavi task …", "Start a local task."),
            ("!mavi status", "Show current task status."),
            ("!mavi steer …", "Adjust the active task."),
            ("!mavi stop", "Stop the active task."),
            ("!mavi answer …", "Answer a pending prompt."),
            ("!mavi help", "List available commands.")
        ]
    }

    private func idField(_ title: String, value: Binding<String>, help: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption.bold())
            HStack {
                TextField(title, text: value).textFieldStyle(.roundedBorder).disabled(settingsLocked)
                Button("Copy") { copy(value.wrappedValue) }
                    .disabled(value.wrappedValue.isEmpty)
            }
            Text(help).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func copy(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }
}
