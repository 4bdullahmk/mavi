import SwiftUI

/// A neutral progress skeleton, not a preview of the image being generated.
struct MaviImageProgressPlaceholder: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shimmering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "sparkles")
                    .foregroundStyle(.secondary)
                Text("Generating image locally")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.secondary)
                Spacer()
                ProgressView()
                    .controlSize(.small)
                    .accessibilityLabel("Image generation in progress")
            }

            GeometryReader { geometry in
                ZStack(alignment: .topLeading) {
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(Color.primary.opacity(0.045))

                    VStack(alignment: .leading, spacing: 12) {
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color.primary.opacity(0.07))
                            .frame(width: geometry.size.width * 0.54, height: 10)
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color.primary.opacity(0.055))
                            .frame(width: geometry.size.width * 0.34, height: 8)
                        Spacer()
                        Text("Progress placeholder · not an image preview")
                            .font(.system(size: 10))
                            .foregroundStyle(.tertiary)
                    }
                    .padding(18)

                    if !reduceMotion {
                        LinearGradient(
                            colors: [.clear, Color.primary.opacity(0.055), .clear],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                        .frame(width: max(geometry.size.width * 0.45, 100))
                        .offset(x: shimmering ? geometry.size.width : -geometry.size.width * 0.45)
                        .clipped()
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(Color.primary.opacity(0.07), lineWidth: 1)
                }
            }
            .frame(height: 150)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Image generation progress placeholder")
        }
        .padding(12)
        .background(Color.primary.opacity(0.025), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .onAppear { shimmering = !reduceMotion }
        .onChange(of: reduceMotion) { _, reduced in
            shimmering = !reduced
        }
        .animation(reduceMotion ? nil : .linear(duration: 1.8).repeatForever(autoreverses: false), value: shimmering)
    }
}

