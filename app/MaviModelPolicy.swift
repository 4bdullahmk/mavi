import Foundation

/// Shared, non-authorizing response policy for local model calls.
/// This only shapes prompts and token budgets; it never changes OS or app confirmations.
enum MaviModelPolicy {
    static func systemBrief(role: String) -> String {
        let common = "Be direct and concise. Do the requested work instead of only describing steps. Make sensible low-risk, reversible choices and continue until the request is complete. Ask only for essential missing information or a required approval. Avoid repeating the request, completed work, and routine play-by-play. State uncertainty plainly. Follow required output formats exactly. Never claim a file, message, or other change was saved, sent, or applied until the tool confirms success or a fresh verification confirms it. This guidance does not change OS/security checks, confirmation requirements, or tool authorization."
        switch role.lowercased() {
        case "chat":
            return common + " Answer the user's question directly and match the requested level of detail."
        case "code", "coder", "developer":
            return common + " Inspect the relevant project context, make the requested focused change, and verify the affected behavior. Preserve unrelated work."
        case "file", "files", "document", "documents", "spreadsheet", "presentation":
            return common + " Use the supplied source material, create the requested artifact, and report its saved location. Do not invent source facts."
        case "research", "analysis", "unified-analysis", "unified-synthesis":
            return common + " Separate evidence from inference and identify material uncertainty."
        case "browser", "computer", "vision":
            return common + " Use only the current task context. Existing confirmations and focus checks remain required."
        default:
            return common
        }
    }

    /// Conservative generation budgets by task. File/code work keeps 4K–8K tokens.
    static func outputBudget(role: String, lightMode: Bool) -> Int {
        switch role.lowercased() {
        case "file", "files", "document", "documents", "spreadsheet", "presentation",
             "code", "coder", "developer":
            return lightMode ? 4_096 : 8_192
        case "unified-parallel-review":
            return 1_200
        case "unified-review":
            return 2_400
        case "research", "analysis", "unified-analysis", "unified-synthesis":
            return lightMode ? 4_096 : 6_000
        case "structured", "vision", "browser", "computer":
            return 450
        default:
            return lightMode ? 1_024 : 2_048
        }
    }

    /// Remove Ollama's hidden `<think>…</think>` blocks from user-facing text.
    static func stripReasoning(_ text: String) -> String {
        let complete = try? NSRegularExpression(pattern: #"(?is)<think\b[^>]*>.*?</think\s*>"#)
        var result = complete?.stringByReplacingMatches(
            in: text,
            range: NSRange(text.startIndex..., in: text),
            withTemplate: ""
        ) ?? text

        // If a truncated response left an unclosed reasoning block, do not expose its tail.
        if let opening = result.range(of: #"(?is)<think\b[^>]*>"#, options: .regularExpression) {
            result.removeSubrange(opening.lowerBound..<result.endIndex)
        } else if let regex = try? NSRegularExpression(pattern: #"(?i)</think\s*>"#),
                  let match = regex.matches(in: result, range: NSRange(result.startIndex..., in: result)).last,
                  let closing = Range(match.range, in: result) {
            // Some Ollama responses contain only an orphan closing tag after raw reasoning.
            result.removeSubrange(result.startIndex..<closing.upperBound)
        }
        result = (try? NSRegularExpression(pattern: #"(?i)</think\s*>"#))?.stringByReplacingMatches(
            in: result,
            range: NSRange(result.startIndex..., in: result),
            withTemplate: ""
        ) ?? result
        return result.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
