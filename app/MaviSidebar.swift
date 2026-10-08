import SwiftUI

extension ContentView {
    var sidebar: some View {
        List(selection: workspaceSelection) {
            Section {
                Button { desk.reset() } label: { Label("New conversation", systemImage: "square.and.pencil") }
                    .disabled(!canStartConversation)
            }
            Section("Workspace") {
                Label("Chat", systemImage: "bubble.left.and.bubble.right").tag(0)
                DisclosureGroup("Tools", isExpanded: $showTools) {
                    ForEach(Self.destinations.filter { $0.mode != 0 }) { destination in
                        Label(destination.title, systemImage: destination.symbol)
                            .tag(destination.mode)
                            .disabled(workspaceSwitchLocked)
                    }
                }
            }.disabled(workspaceSwitchLocked)
            Section("Library") {
                Button { showDiscord = true } label: { Label("Discord remote", systemImage: "bubble.left.and.text.bubble.right") }
                Button { showTaskMemory = true } label: { Label("Tasks & memory", systemImage: "list.bullet.clipboard") }
                Button { showAgentInspector = true } label: { Label("Agent team", systemImage: "person.2") }
                    .help("Show the agent team inspector")
                Button { desk.showHistory = true } label: { Label("Chat history", systemImage: "clock.arrow.circlepath") }
            }
            if !desk.conversations.isEmpty {
                Section("Recent") {
                    ForEach(desk.conversations.prefix(5)) { conversation in
                        Button { desk.openConversation(conversation) } label: {
                            Text(conversation.title).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                        }.disabled(!canStartConversation)
                    }
                }
            }
            Section("More") {
                Button { desk.showSkills = true } label: { Label("Skill library", systemImage: "sparkles") }
                Button("Verify last answer") { desk.verifyLastAnswer() }
                    .disabled(desk.workInProgress || desk.lines.isEmpty || !desk.balancedReady)
                Button("Sort draft text with Tev1") { desk.quickSort() }
                    .disabled(desk.workInProgress || !desk.companionReady || desk.draft.isEmpty)
                Button { desk.showIntelligence = true } label: { Label("Models & training", systemImage: "brain.head.profile") }
            }
            Section {
                DisclosureGroup("On this Mac", isExpanded: $showSystemStatus) {
                    StatusDot(good: desk.modelReady, text: desk.modelReady ? "Screen reading ready" : "Screen reading unavailable")
                    StatusDot(good: desk.coderReady, text: desk.coderReady ? "Coding model ready" : "Coding model unavailable")
                    StatusDot(good: desk.balancedReady, text: desk.balancedReady ? "Analysis model ready" : "Analysis model unavailable")
                    StatusDot(good: desk.imageReady, text: desk.imageReady ? "Image model ready" : "Image model unavailable")
                }
            }
            Section {
                Button { desk.settings = true } label: { Label("Connection & permissions…", systemImage: "gearshape") }
            }
        }
        .listStyle(.sidebar).buttonStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(MaviSidebarMaterial())
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(NSFullUserName()).font(.caption).foregroundStyle(.secondary)
                        Text("M").font(.caption2).foregroundStyle(.tertiary).help("Mavi").accessibilityLabel("Mavi")
                    }
                    Text("Local workspace").font(.caption2).foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
            }
    }
}
