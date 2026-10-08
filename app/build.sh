#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
root="$(pwd)"
version="${MAVI_VERSION:-2.0.1}"
build_root="${MAVI_BUILD_DIR:-${TMPDIR:-/tmp}/mavi-build}"
app_path="$build_root/Mavi.app"
cache_path="$build_root/swift-cache"
iconset="$build_root/mavi.iconset"
rm -rf "$app_path" "$cache_path" "$iconset"
mkdir -p "$app_path/Contents/MacOS" "$app_path/Contents/Resources" "$cache_path" "$iconset"
/usr/bin/swiftc -parse-as-library -O -target arm64-apple-macos14.2 -module-cache-path "$cache_path" \
  MaviApp.swift MaviEasterEgg.swift MaviModelPolicy.swift MaviImageProgress.swift MaviSidebar.swift MaviSidebarMaterial.swift DiscordRemote.swift DiscordWorkspace.swift DiscordPanel.swift DiscordChecks.swift PortableConnection.swift Intelligence.swift AgentFleet.swift ProgressNarrator.swift UnifiedAgent.swift UnifiedPlanning.swift MaviExecution.swift MaviEvidence.swift MaviTasks.swift MaviTaskChecks.swift DesignAdvisor.swift MaviAccent.swift Files.swift Model3D.swift SelfUpdateUI.swift Browser.swift Dictation.swift Stocks.swift Integrations.swift MaviOnboarding.swift MaviReleaseDownloads.swift \
  -o "$app_path/Contents/MacOS/Mavi" -framework SwiftUI -framework ScreenCaptureKit -framework ApplicationServices -framework SceneKit -framework AVFoundation
cp PersonalAgent.py ProjectReadCache.py DeveloperAgent.py BrowserAgent.py BrowserSnapshot.js Dictation.py StockTools.py MaviChecks.py SelfUpdate.py UpdateInstaller.py CADAgent.py CADTools.py ProductSpecs.py MeshTools.py FileTools.py SpreadsheetTools.py PresentationTools.py "$app_path/Contents/Resources/"
cp Info.plist "$app_path/Contents/Info.plist"
/usr/bin/swift -module-cache-path "$cache_path" Icon.swift "$iconset"
/usr/bin/python3 pack_icon.py "$app_path/Contents/Resources/AppIcon.icns" "$iconset"
mkdir -p "$app_path/Contents/Resources/Source"
cp *.swift *.py *.sh *.plist *.js "$app_path/Contents/Resources/Source/"
cp "$app_path/Contents/Resources/AppIcon.icns" "$app_path/Contents/Resources/Source/"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $version" "$app_path/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${MAVI_BUILD_NUMBER:-27}" "$app_path/Contents/Info.plist"
# Strip only FinderInfo/resource-fork metadata that can prevent codesign.
# Preserve quarantine and all other security attributes.
/usr/bin/xattr -r -d com.apple.FinderInfo "$app_path" 2>/dev/null || true
/usr/bin/xattr -r -d com.apple.ResourceFork "$app_path" 2>/dev/null || true
/usr/bin/codesign --force --sign - --timestamp=none "$app_path"
/usr/bin/codesign --verify --strict "$app_path"
/usr/bin/python3 package_release.py "$app_path" "$root/dist" "$version"
printf 'Built: %s\n' "$app_path"
