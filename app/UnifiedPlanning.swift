import Foundation

struct UnifiedPlan: Decodable {
    let kind: String
    let task: String
    let url: String
    let format: String
    let filename: String
    let question: String
}

enum UnifiedPlanning {
    /// Recognizes direct requests to change Mavi itself. Discussion, quoted requests,
    /// capability questions, and edits to outside projects stay on normal routing.
    static func directMaviUpdatePlan(_ text: String) -> UnifiedPlan? {
        let request = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = request.lowercased()
        guard !request.isEmpty else { return nil }
        if request.hasPrefix(">") || request.contains("\"") || request.contains("“") || request.contains("”") { return nil }
        if ["tell me about", "explain", "what if", "someone said", "a user said", "the user said", "the user asked", "they asked", "quoted request"].contains(where: lower.hasPrefix) { return nil }
        let changeVerbs = #"(?:edit|edidt|update|change|modify|improve|redesign|customi[sz]e)"#
        if contains(#"\b(?:do\s+not|don't|dont|never|must\s+not|should\s+not|cannot|can't)\s+(?:(?:please|just|ever)\s+)*"# + changeVerbs + #"\b|\bnot\s+(?:asking|telling|requesting)\s+you\s+to\s+"# + changeVerbs + #"\b"#, in: lower) { return nil }
        if contains(#"\b(?:my|our|client's|customers?)\s+(?:external\s+)?(?:[\w.-]+\s+){0,2}(?:app|project|repo|codebase|website)\b"#, in: lower),
           !contains(#"\bmy\s+mavi\s+app\b"#, in: lower) { return nil }
        let capabilityQuestion = contains(#"^\s*(?:can|could|would|are)\s+you\b"#, in: lower)
        let requestsConcreteChange = contains(#"\b(?:to\s+add|to\s+let|so\s+users|let\s+users|allow\s+users|add|instead\s+of|so\s+that|make\s+it)\b"#, in: lower)
        if capabilityQuestion && !requestsConcreteChange { return nil }

        let targets = matches(#"\b(yourself|your\s+(?:ui|interface|theme|appearance|accent\s+colou?r|design\s+system|app)|this\s+app|mavi(?:\s+app)?)\b"#, in: lower)
        let verbs = matches(#"\b(edit|edidt|update|change|modify|improve|redesign|customi[sz]e)\b"#, in: lower)
        guard verbs.contains(where: { verb in targets.contains(where: { abs(verb.range.location - $0.range.location) <= 60 }) }) else { return nil }

        return UnifiedPlan(kind: "update", task: request, url: "", format: "", filename: "", question: "")
    }

    static func requestsVisualAdvice(_ text: String) -> Bool {
        if DesignAdvisor.shouldConsult(text) { return true }
        let lower = text.lowercased()
        return ["accent color", "accent colour", "soft ui", "softer ui", "comfortable ui", "bulky elements", "square elements", "rounded corners", "rounded elements", "visual style", "appearance picker", "color picker", "colour picker"].contains(where: lower.contains)
    }

    private static func contains(_ pattern: String, in text: String) -> Bool {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return false }
        return regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }

    private static func matches(_ pattern: String, in text: String) -> [NSTextCheckingResult] {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        return regex.matches(in: text, range: NSRange(text.startIndex..., in: text))
    }
}
