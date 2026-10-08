import SwiftUI
import AppKit

struct MaviReleaseDownloads: View {
    @AppStorage("mavi.releaseURL") private var savedURL = ""
    private var configuredURL: String {
        savedURL.isEmpty ? (Bundle.main.object(forInfoDictionaryKey: "MaviReleaseURL") as? String ?? "") : savedURL
    }
    static func validatedURL(_ text: String) -> URL? {
        guard let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme == "https", url.host == "github.com", url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil, url.port == nil else { return nil }
        let parts = url.path.split(separator: "/")
        guard parts.count == 3, parts[2] == "releases",
              parts.prefix(2).allSatisfy({ $0.range(of: "^[A-Za-z0-9_.-]+$", options: .regularExpression) != nil && $0 != "." && $0 != ".." }) else { return nil }
        return url
    }
    var body: some View {
        GroupBox("Mavi releases") {
            VStack(alignment: .leading, spacing: 10) {
                Text("Installed version \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—")")
                    .font(.headline)
                TextField("https://github.com/owner/repository/releases", text: $savedURL)
                    .textFieldStyle(.roundedBorder)
                Button("Download updates…") {
                    if let url = Self.validatedURL(configuredURL) { NSWorkspace.shared.open(url) }
                }.disabled(Self.validatedURL(configuredURL) == nil)
                Text("Opens Releases in your browser. For a private repository, sign in with an account the owner has invited. Download the latest version, quit Mavi, and replace Mavi.app in ~/Applications. Your local chats, preferences and models stay on this Mac.")
                    .font(.caption).foregroundStyle(.secondary)
                Text("This opens the download page; it does not install updates automatically.")
                    .font(.caption2).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
