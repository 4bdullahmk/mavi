import SwiftUI

enum MaviAccent: String, CaseIterable, Identifiable {
    case graphite, blue, purple, rose, orange, green, teal
    
    var id: String { rawValue }
    
    var label: String {
        switch self {
        case .graphite: return "Graphite"
        case .blue: return "Blue"
        case .purple: return "Purple"
        case .rose: return "Rose"
        case .orange: return "Orange"
        case .green: return "Green"
        case .teal: return "Teal"
        }
    }
    
    var color: Color {
        switch self {
        case .graphite: return .primary
        case .blue: return .blue
        case .purple: return .purple
        case .rose: return .pink
        case .orange: return .orange
        case .green: return .green
        case .teal: return .teal
        }
    }
    
    static func resolved(_ value: String) -> MaviAccent {
        MaviAccent.allCases.first { $0.rawValue == value } ?? .graphite
    }
}

struct MaviAccentPicker: View {
    @AppStorage("MaviAccent") private var selection = "graphite"
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Accent color").font(.headline)
            Picker("Accent color", selection: $selection) {
                ForEach(MaviAccent.allCases) { choice in
                    Text(choice.label).tag(choice.rawValue)
                }
            }
            .pickerStyle(.menu)
            
            Text("Applied to controls and highlights. Graphite keeps the monochrome look.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
