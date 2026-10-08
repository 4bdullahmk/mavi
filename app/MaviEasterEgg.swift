import SwiftUI

/// The intentionally public, local-only phrase match and celebration.
enum MaviEasterEgg {
    static let reply = "I love Muzhda"
    private static let sourcePhrase = "i love abdullah"

    static func matches(_ input: String) -> Bool {
        var normalized = input.split(whereSeparator: \.isWhitespace).joined(separator: " ").lowercased()
        while let last = normalized.last, [".", "!", "?", "…"].contains(String(last)) {
            normalized.removeLast()
        }
        return normalized == sourcePhrase
    }
}

struct MaviEasterEggCelebration: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false
    let id: UUID
    let onDismiss: () -> Void

    private let columns = 12
    private let rows = 9
    private let heartCount = 108

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color.black.opacity(0.12).ignoresSafeArea()

                ForEach(0..<heartCount, id: \.self) { index in
                    let column = index % columns
                    let row = index / columns
                    let width = max(geometry.size.width, 1)
                    let height = max(geometry.size.height, 1)
                    let xJitter = CGFloat((index * 17) % 19 - 9)
                    let yJitter = CGFloat((index * 13) % 17 - 8)
                    let x = (CGFloat(column) + 0.5) * width / CGFloat(columns) + xJitter
                    let y = (CGFloat(row) + 0.5) * height / CGFloat(rows) + yJitter
                    let cellSize = min(width / CGFloat(columns), height / CGFloat(rows))
                    let size = max(20, cellSize * CGFloat(0.86 + Double(index % 5) * 0.085))
                    let opacity = 0.60 + Double((index * 11) % 36) / 100

                    Image(systemName: "heart.fill")
                        .font(.system(size: size, weight: .regular))
                        .foregroundStyle(Color.red)
                        .rotationEffect(.degrees(Double((index * 19) % 31 - 15)))
                        .scaleEffect(reduceMotion || appeared ? 1 : 0.15)
                        .opacity(reduceMotion || appeared ? opacity : 0)
                        .position(x: x, y: y)
                        .animation(reduceMotion ? nil : .spring(response: 0.85, dampingFraction: 0.78).delay(Double(index % 6) * 0.045), value: appeared)
                        .accessibilityHidden(true)
                }

                VStack(spacing: 12) {
                    Text(MaviEasterEgg.reply)
                        .font(.system(size: 30, weight: .semibold, design: .rounded))
                    Button("Close", action: onDismiss)
                        .keyboardShortcut(.cancelAction)
                }
                .padding(.horizontal, 38)
                .padding(.vertical, 26)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).stroke(Color.primary.opacity(0.08), lineWidth: 1))
                .shadow(color: .black.opacity(0.15), radius: 24, y: 10)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .contentShape(Rectangle())
            .onExitCommand(perform: onDismiss)
        }
        .accessibilityAddTraits(.isModal)
        .onAppear { appeared = true }
        .task(id: id) {
            do { try await Task.sleep(nanoseconds: 8_500_000_000) } catch { return }
            guard !Task.isCancelled else { return }
            onDismiss()
        }
    }
}
