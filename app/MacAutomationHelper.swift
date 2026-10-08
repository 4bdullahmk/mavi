import AppKit
import ApplicationServices
import ScreenCaptureKit
import ImageIO
import UniformTypeIdentifiers

@MainActor
private enum MacAutomation {
    static let apps: [String: String] = [
        "com.brave.Browser": "Brave", "com.google.Chrome": "Google Chrome",
        "com.apple.Safari": "Safari", "org.mozilla.firefox": "Firefox",
        "com.microsoft.edgemac": "Microsoft Edge", "Cisco-Systems.Spark": "Webex",
        "us.zoom.xos": "Zoom", "com.microsoft.teams2": "Microsoft Teams",
        "com.apple.finder": "Finder", "com.apple.TextEdit": "TextEdit",
        "com.apple.Preview": "Preview", "com.microsoft.Word": "Microsoft Word",
        "com.microsoft.Excel": "Microsoft Excel", "com.hnc.Discord": "Discord"
    ]

    static func dispatch(_ input: [String: Any]) async throws -> [String: Any] {
        guard let op = input["op"] as? String else { throw SafeError("Invalid helper request.") }
        switch op {
        case "capability":
            return ["screen_recording": CGPreflightScreenCaptureAccess(),
                    "accessibility": AXIsProcessTrusted(), "apps": apps.map { ["bundle_id": $0.key, "name": $0.value] }]
        case "open_app":
            let bundle = try bundleID(input)
            guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) else {
                throw SafeError("\(apps[bundle] ?? "The selected app") is not installed.")
            }
            let configuration = NSWorkspace.OpenConfiguration(); configuration.activates = true
            _ = try await NSWorkspace.shared.openApplication(at: url, configuration: configuration)
            return ["bundle_id": bundle, "name": apps[bundle] ?? "App"]
        case "default_browser":
            guard let probe = URL(string: "https://example.com"),
                  let appURL = NSWorkspace.shared.urlForApplication(toOpen: probe),
                  let bundle = Bundle(url: appURL)?.bundleIdentifier, apps[bundle] != nil else {
                throw SafeError("Your default browser is not supported for app control. Set Brave, Chrome, Safari, Firefox, or Edge as the macOS default browser.")
            }
            return ["bundle_id": bundle, "name": apps[bundle] ?? "Browser"]
        case "open_url":
            guard let raw = input["url"] as? String, raw.count <= 2048,
                  let components = URLComponents(string: raw), components.scheme?.lowercased() == "https",
                  let host = components.host, !host.isEmpty, components.user == nil, components.password == nil,
                  let url = components.url else { throw SafeError("Only a direct HTTPS link from your request can be opened.") }
            let bundle: String?
            if let requestedBundle = input["bundle_id"] as? String {
                let supported = try bundleID(["bundle_id": requestedBundle])
                guard let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: supported) else {
                    throw SafeError("\(apps[supported] ?? "The selected app") is not installed.")
                }
                let configuration = NSWorkspace.OpenConfiguration(); configuration.activates = true
                _ = try await NSWorkspace.shared.open([url], withApplicationAt: appURL, configuration: configuration)
                bundle = supported
            } else {
                guard NSWorkspace.shared.open(url) else { throw SafeError("macOS could not open the supplied HTTPS link.") }
                bundle = NSWorkspace.shared.urlForApplication(toOpen: url).flatMap { Bundle(url: $0)?.bundleIdentifier }
            }
            return ["url": url.absoluteString, "bundle_id": bundle ?? ""]
        case "windows":
            let bundle = try bundleID(input)
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
            let list = content.windows.filter { $0.owningApplication?.bundleIdentifier == bundle && $0.windowLayer == 0 && $0.frame.width >= 120 && $0.frame.height >= 80 }
            return ["windows": list.prefix(20).map { w in
                ["window_id": Int(w.windowID), "title": String((w.title ?? "Window").prefix(240)),
                 "width": Int(w.frame.width), "height": Int(w.frame.height)]
            }]
        case "focus":
            let (bundle, window) = try await resolveWindow(input)
            guard AXIsProcessTrusted() else { throw SafeError("Allow Accessibility for Mavi in System Settings before using computer control.") }
            guard let owner = window.owningApplication,
                  let app = NSRunningApplication(processIdentifier: owner.processID) else { throw SafeError("The selected app is no longer running.") }
            _ = app.activate(options: [.activateAllWindows])
            let axApp = AXUIElementCreateApplication(owner.processID)
            var value: CFTypeRef?
            guard AXUIElementCopyAttributeValue(axApp, kAXWindowsAttribute as CFString, &value) == .success,
                  let axWindows = value as? [AXUIElement] else { throw SafeError("The app does not expose windows for reliable focus.") }
            let matches = axWindows.filter { element in axBounds(element).map { sameFrame($0, window.frame) } ?? false }
            guard matches.count == 1, AXUIElementPerformAction(matches[0], kAXRaiseAction as CFString) == .success else {
                throw SafeError("Could not uniquely focus the selected window. No input was sent.")
            }
            try await Task.sleep(nanoseconds: 350_000_000)
            guard isFocused(window, bundle: bundle) else { throw SafeError("The selected window could not be focused. No input was sent.") }
            return ["focused": true, "window_id": Int(window.windowID)]
        case "capture":
            let (bundle, window) = try await resolveWindow(input)
            guard CGPreflightScreenCaptureAccess() else { throw SafeError("Allow Screen Recording for Mavi in System Settings, then try again.") }
            let config = SCStreamConfiguration()
            let scale = min(1.5, 1600 / max(window.frame.width, 1))
            config.width = max(1, Int(window.frame.width * scale)); config.height = max(1, Int(window.frame.height * scale))
            config.showsCursor = false; config.ignoreShadowsSingleWindow = true
            let cg = try await SCScreenshotManager.captureImage(contentFilter: SCContentFilter(desktopIndependentWindow: window), configuration: config)
            let rep = NSBitmapImageRep(cgImage: cg)
            guard let data = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.82]), data.count <= 8_000_000 else {
                throw SafeError("The selected app window screenshot is too large to send to the local model.")
            }
            let targets = accessibilityTargets(window: window, bundle: bundle)
            return ["bundle_id": bundle, "window_id": Int(window.windowID), "title": String((window.title ?? "Window").prefix(240)),
                    "width": cg.width, "height": cg.height, "image_base64": data.base64EncodedString(), "targets": targets]
        case "action":
            let (bundle, window) = try await resolveWindow(input)
            guard AXIsProcessTrusted() else { throw SafeError("Allow Accessibility for Mavi in System Settings before using computer control.") }
            guard isFocused(window, bundle: bundle) else { throw SafeError("The selected app window lost focus. No input was sent; focus it and retry.") }
            guard let action = input["action"] as? [String: Any], let kind = action["kind"] as? String else { throw SafeError("Invalid reviewed action.") }
            try perform(kind: kind, action: action, window: window, bundle: bundle)
            return ["performed": kind, "window_id": Int(window.windowID)]
        default: throw SafeError("Unsupported helper operation.")
        }
    }

    private static func bundleID(_ input: [String: Any]) throws -> String {
        guard let bundle = input["bundle_id"] as? String, apps[bundle] != nil else { throw SafeError("That app is not on Mavi's supported app list.") }
        return bundle
    }

    private static func resolveWindow(_ input: [String: Any]) async throws -> (String, SCWindow) {
        let bundle = try bundleID(input)
        guard let raw = input["window_id"] as? Int, raw > 0, raw <= Int(UInt32.max) else { throw SafeError("Select a current window first.") }
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
        guard let window = content.windows.first(where: { Int($0.windowID) == raw && $0.owningApplication?.bundleIdentifier == bundle && $0.windowLayer == 0 }) else {
            throw SafeError("The selected window changed or closed. Refresh the window list before continuing.")
        }
        return (bundle, window)
    }

    private static func axBounds(_ element: AXUIElement) -> CGRect? {
        var position: CFTypeRef?; var size: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &position) == .success,
              AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &size) == .success,
              let position, let size, CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
        var p = CGPoint.zero; var s = CGSize.zero
        guard AXValueGetValue(position as! AXValue, .cgPoint, &p), AXValueGetValue(size as! AXValue, .cgSize, &s) else { return nil }
        return CGRect(origin: p, size: s)
    }

    private static func sameFrame(_ a: CGRect, _ b: CGRect) -> Bool {
        abs(a.minX-b.minX) < 4 && abs(a.minY-b.minY) < 4 && abs(a.width-b.width) < 4 && abs(a.height-b.height) < 4
    }

    private static func isFocused(_ window: SCWindow, bundle: String) -> Bool {
        guard let owner = window.owningApplication, owner.bundleIdentifier == bundle,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == owner.processID else { return false }
        var focused: CFTypeRef?
        guard AXUIElementCopyAttributeValue(AXUIElementCreateApplication(owner.processID), kAXFocusedWindowAttribute as CFString, &focused) == .success,
              let focused, CFGetTypeID(focused) == AXUIElementGetTypeID(), let frame = axBounds(unsafeBitCast(focused, to: AXUIElement.self)) else { return false }
        return sameFrame(frame, window.frame)
    }

    private static let actionableRoles: Set<String> = ["AXLink", "AXButton", "AXMenuItem"]

    private static func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value
    }

    private static func stringAttribute(_ element: AXUIElement, _ name: String) -> String? {
        guard let value = attribute(element, name) else { return nil }
        return value as? String
    }

    private static func role(_ element: AXUIElement) -> String? {
        stringAttribute(element, kAXRoleAttribute as String)
    }

    private static func childElements(_ element: AXUIElement) -> [AXUIElement] {
        (attribute(element, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
    }

    private static func selectedAXWindow(window: SCWindow, bundle: String) -> AXUIElement? {
        guard let owner = window.owningApplication, owner.bundleIdentifier == bundle else { return nil }
        let app = AXUIElementCreateApplication(owner.processID)
        guard let windows = attribute(app, kAXWindowsAttribute as String) as? [AXUIElement] else { return nil }
        let matches = windows.filter { element in axBounds(element).map { sameFrame($0, window.frame) } ?? false }
        return matches.count == 1 ? matches[0] : nil
    }

    private static func elementLabel(_ element: AXUIElement) -> String? {
        for key in [kAXTitleAttribute as String, kAXDescriptionAttribute as String, kAXHelpAttribute as String] {
            if let value = stringAttribute(element, key), !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return String(value.trimmingCharacters(in: .whitespacesAndNewlines).prefix(180))
            }
        }
        // AXValue is read only from static text nodes. Editable and secure fields are never queried.
        for child in childElements(element).prefix(80) where role(child) == (kAXStaticTextRole as String) {
            if let value = stringAttribute(child, kAXValueAttribute as String), !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return String(value.trimmingCharacters(in: .whitespacesAndNewlines).prefix(180))
            }
        }
        return nil
    }

    private static func isVisible(_ rect: CGRect, in window: SCWindow) -> Bool {
        guard rect.width > 0, rect.height > 0, rect.minX.isFinite, rect.minY.isFinite,
              rect.width.isFinite, rect.height.isFinite else { return false }
        let intersection = rect.intersection(window.frame)
        guard !intersection.isNull, intersection.width >= 2, intersection.height >= 2 else { return false }
        return NSScreen.screens.contains { screen in
            // AX and ScreenCaptureKit use global top-left display coordinates;
            // NSScreen.frame uses bottom-left coordinates and can reject every
            // target on a secondary display above or below the main display.
            guard let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else { return false }
            let visibleIntersection = intersection.intersection(CGDisplayBounds(CGDirectDisplayID(number.uint32Value)))
            return !visibleIntersection.isNull && visibleIntersection.width >= 2 && visibleIntersection.height >= 2
        }
    }

    private static func normalizedBounds(_ rect: CGRect, window: CGRect) -> [String: Int] {
        func norm(_ value: CGFloat, _ origin: CGFloat, _ extent: CGFloat) -> Int {
            Int(max(0, min(1000, ((value - origin) / max(extent, 1)) * 1000)).rounded())
        }
        let clipped = rect.intersection(window)
        return ["x": norm(clipped.minX, window.minX, window.width), "y": norm(clipped.minY, window.minY, window.height),
                "width": Int(max(0, min(1000, clipped.width / max(window.width, 1) * 1000)).rounded()),
                "height": Int(max(0, min(1000, clipped.height / max(window.height, 1) * 1000)).rounded())]
    }

    private static func accessibilityTargets(window: SCWindow, bundle: String) -> [[String: Any]] {
        guard let root = selectedAXWindow(window: window, bundle: bundle) else { return [] }
        var results: [[String: Any]] = []
        var visited = 0
        var stack: [(AXUIElement, [Int], Int)] = [(root, [], 0)]
        while let (element, path, depth) = stack.popLast(), visited < 2500, results.count < 100 {
            visited += 1
            if let currentRole = role(element), actionableRoles.contains(currentRole),
               let rect = axBounds(element), isVisible(rect, in: window), let label = elementLabel(element), !label.isEmpty {
                results.append(["id": path.map(String.init).joined(separator: "."), "role": currentRole,
                                "label": label, "bounds": normalizedBounds(rect, window: window.frame)])
            }
            guard depth < 24 else { continue }
            let children = childElements(element)
            for index in children.indices.reversed() where stack.count + visited < 2500 {
                stack.append((children[index], path + [index], depth + 1))
            }
        }
        return results
    }

    private static func resolveTarget(_ action: [String: Any], window: SCWindow, bundle: String) throws -> (AXUIElement, CGRect) {
        guard let targetID = action["target_id"] as? String, targetID.count <= 160,
              let expectedRole = action["expected_role"] as? String, actionableRoles.contains(expectedRole),
              let expectedLabel = action["expected_label"] as? String, !expectedLabel.isEmpty, expectedLabel.count <= 180 else {
            throw SafeError("Invalid accessibility target. Refresh the window and select a listed target.")
        }
        guard let root = selectedAXWindow(window: window, bundle: bundle) else {
            throw SafeError("The selected window no longer has a unique accessibility tree. Refresh the window list.")
        }
        let components = targetID.split(separator: ".", omittingEmptySubsequences: false)
        guard !components.isEmpty, components.count <= 24,
              components.allSatisfy({ Int($0).map { $0 >= 0 && $0 < 2500 } ?? false }) else {
            throw SafeError("The accessibility target path is invalid. Refresh the window and select a listed target.")
        }
        var element = root
        for component in components {
            let index = Int(component)!
            let children = childElements(element)
            guard index < children.count else { throw SafeError("The accessibility target changed. Refresh the window and select it again.") }
            element = children[index]
        }
        guard role(element) == expectedRole, elementLabel(element) == expectedLabel,
              let rect = axBounds(element), isVisible(rect, in: window) else {
            throw SafeError("The accessibility target label, role, or position changed. Refresh the window and select it again.")
        }
        if let expectedBounds = action["expected_bounds"] as? [String: Any] {
            let actual = normalizedBounds(rect, window: window.frame)
            for key in ["x", "y", "width", "height"] {
                if let expected = expectedBounds[key] as? Int, abs((actual[key] ?? 0) - expected) > 20 {
                    throw SafeError("The accessibility target moved. Refresh the window and select it again.")
                }
            }
        }
        return (element, rect)
    }

    private static func perform(kind: String, action: [String: Any], window: SCWindow, bundle: String) throws {
        let bounds = window.frame
        switch kind {
        case "target":
            let (element, rect) = try resolveTarget(action, window: window, bundle: bundle)
            var names: CFArray?
            if AXUIElementCopyActionNames(element, &names) == .success,
               let actions = names as? [String], actions.contains(kAXPressAction as String) {
                guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
                    throw SafeError("macOS could not activate the selected accessibility target.")
                }
                return
            }
            // The exact element was re-resolved and validated above; click only its current visible center.
            let visible = rect.intersection(window.frame)
            let center = CGPoint(x: visible.midX, y: visible.midY)
            postMouse(.mouseMoved, center); postMouse(.leftMouseDown, center); postMouse(.leftMouseUp, center)
        case "click":
            guard let x = action["x"] as? Int, let y = action["y"] as? Int, (0...1000).contains(x), (0...1000).contains(y) else { throw SafeError("Invalid click coordinates.") }
            let point = CGPoint(x: bounds.minX + bounds.width * CGFloat(x) / 1000, y: bounds.minY + bounds.height * CGFloat(y) / 1000)
            postMouse(.mouseMoved, point); postMouse(.leftMouseDown, point); postMouse(.leftMouseUp, point)
        case "type":
            guard let text = action["text"] as? String, text.count <= 1000,
                  !text.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { throw SafeError("Typed text is invalid or too long.") }
            for character in text {
                guard isFocused(window, bundle: window.owningApplication?.bundleIdentifier ?? "") else { throw SafeError("Focus changed while typing. Mavi stopped immediately.") }
                var chars = Array(String(character).utf16)
                let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)
                down?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars); down?.post(tap: .cghidEventTap)
                let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
                up?.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: &chars); up?.post(tap: .cghidEventTap)
            }
        case "key":
            guard let key = action["key"] as? String else { throw SafeError("Invalid navigation key.") }
            let codes: [String: (CGKeyCode, CGEventFlags)] = ["TAB":(48,[]), "SHIFT+TAB":(48,.maskShift), "ENTER":(36,[]), "ESC":(53,[]), "UP":(126,[]), "DOWN":(125,[]), "LEFT":(123,[]), "RIGHT":(124,[]), "HOME":(115,[]), "END":(119,[]), "PAGEUP":(116,[]), "PAGEDOWN":(121,[])]
            guard let (code, flags) = codes[key.uppercased()] else { throw SafeError("That key is not allowed.") }
            let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true), up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
            down?.flags = flags; up?.flags = flags; down?.post(tap: .cghidEventTap); up?.post(tap: .cghidEventTap)
        case "scroll":
            guard let direction = action["direction"] as? String, ["up", "down"].contains(direction),
                  let amount = action["amount"] as? Int, (1...1200).contains(amount) else { throw SafeError("Invalid scroll request.") }
            let center = CGPoint(x: bounds.midX, y: bounds.midY); postMouse(.mouseMoved, center)
            let wheel = Int32(max(1, min(1200, amount))) * (direction == "up" ? 1 : -1)
            CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: wheel, wheel2: 0, wheel3: 0)?.post(tap: .cghidEventTap)
        default: throw SafeError("That action is not supported.")
        }
    }

    private static func postMouse(_ type: CGEventType, _ point: CGPoint) {
        let button: CGMouseButton = type == .leftMouseDown || type == .leftMouseUp ? .left : .left
        CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: button)?.post(tap: .cghidEventTap)
    }
}

private struct SafeError: Error, LocalizedError { let message: String; init(_ message: String) { self.message = message }; var errorDescription: String? { message } }

@MainActor func runMacAutomationCommand() async -> Int32 {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    var response: [String: Any]
    var exitCode: Int32 = 1
    if input.count > 16_384 {
        response = ["ok": false, "error": "Helper request is too large."]
    } else if let object = try? JSONSerialization.jsonObject(with: input) as? [String: Any] {
        do {
            let result = try await MacAutomation.dispatch(object)
            response = ["ok": true, "result": result]
            exitCode = 0
        } catch let error as LocalizedError {
            response = ["ok": false, "error": error.errorDescription ?? "The requested app action failed."]
        } catch {
            response = ["ok": false, "error": "The requested app action failed."]
        }
    } else {
        response = ["ok": false, "error": "Invalid helper request."]
    }
    do {
        let data = try JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])
        FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
        return exitCode
    } catch { return 2 }
}
