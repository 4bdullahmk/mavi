#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$APP_DIR/.." && pwd)
VERSION=${MAVI_VERSION:-2.0.1}
BUILD_NUMBER=${MAVI_BUILD_NUMBER:-27}
BUILD_ROOT=${MAVI_BUILD_DIR:-${TMPDIR:-/tmp}/mavi-web-build}
mkdir -p "$BUILD_ROOT"
BUILD_ROOT=$(CDPATH= cd -- "$BUILD_ROOT" && pwd)
APP="$BUILD_ROOT/Mavi.app"
CACHE="$BUILD_ROOT/swift-cache"

case "$BUILD_ROOT" in
  /|"$APP_DIR"|"$REPO_DIR"|"$APP_DIR"/*|"$REPO_DIR"/*)
    echo "Choose a separate temporary build directory outside the source tree." >&2
    exit 1
    ;;
esac

case "$(uname -m)" in
  arm64) ;;
  *) echo "Mavi's native web app build requires Apple Silicon." >&2; exit 1 ;;
esac
OS_VERSION=$(sw_vers -productVersion)
case "$OS_VERSION" in *[!0-9.]*|'') echo "Could not read the macOS version." >&2; exit 1 ;; esac
IFS=. read -r OS_MAJOR OS_MINOR _ <<EOF
$OS_VERSION
EOF
OS_MINOR=${OS_MINOR:-0}
if [ "$OS_MAJOR" -lt 14 ] || { [ "$OS_MAJOR" -eq 14 ] && [ "$OS_MINOR" -lt 2 ]; }; then
  echo "Mavi's native web app targets macOS 14.2 or newer." >&2
  exit 1
fi

if [ ! -f "$APP_DIR/MacAutomationHelper.swift" ]; then
  echo "MacAutomationHelper.swift is not ready; refusing an incomplete build." >&2
  exit 1
fi
rm -rf "$APP" "$CACHE"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/MaviRuntime" "$CACHE"

/usr/bin/swiftc -parse-as-library -O -target arm64-apple-macos14.2 \
  -module-cache-path "$CACHE" \
  "$APP_DIR/MaviWebApp.swift" "$APP_DIR/MacAutomationHelper.swift" \
  -o "$APP/Contents/MacOS/Mavi" \
  -framework SwiftUI -framework AppKit -framework WebKit \
  -framework ScreenCaptureKit -framework ApplicationServices -framework ImageIO

cp "$APP_DIR/Info.plist" "$APP/Contents/Info.plist"
cp "$APP_DIR/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cp "$APP_DIR/setup_macos_web.sh" "$APP/Contents/Resources/Setup-Mavi-Mac.command"
chmod 755 "$APP/Contents/Resources/Setup-Mavi-Mac.command"
cp -R "$REPO_DIR/portable" "$APP/Contents/Resources/MaviRuntime/portable"
mkdir -p "$APP/Contents/Resources/MaviRuntime/app"
for helper in "$APP_DIR"/*.py; do
  helper_name=${helper##*/}
  case "$helper_name" in pack_icon.py|package_release.py) continue ;; esac
  cp "$helper" "$APP/Contents/Resources/MaviRuntime/app/$helper_name"
done
find "$APP/Contents/Resources/MaviRuntime/portable" -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} +
find "$APP/Contents/Resources/MaviRuntime/portable" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$APP/Contents/Resources/MaviRuntime/app" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$APP/Contents/Resources/MaviRuntime/app" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP/Contents/Info.plist"
# Finder may attach a bundle-level FinderInfo record in synchronized build roots;
# remove only that signing-incompatible attribute. Keep quarantine and all other
# security metadata untouched.
if /usr/bin/xattr -p com.apple.FinderInfo "$APP" >/dev/null 2>&1; then
  /usr/bin/xattr -d com.apple.FinderInfo "$APP"
fi
SIGN_IDENTITY=${MAVI_SIGN_IDENTITY:--}
/usr/bin/codesign --force --sign "$SIGN_IDENTITY" --timestamp=none "$APP"
/usr/bin/codesign --verify --strict "$APP"
/usr/bin/python3 "$APP_DIR/package_release.py" "$APP" "$APP_DIR/dist" "$VERSION"
printf 'Built %s\n' "$APP"
