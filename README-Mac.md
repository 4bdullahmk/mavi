# Mavi for macOS

Mavi for macOS opens the same local web workspace in a standalone Mac app window. The Python service listens on a loopback-only port chosen for that launch. Workspace data is stored in `~/Library/Application Support/Mavi`, outside the app bundle, so replacing the app does not replace chats, preferences, Discord IDs, or generated files.

## Requirements

- Apple Silicon Mac running macOS 14.2 or newer.
- Python 3.11 or newer. Install it from [python.org](https://www.python.org/downloads/macos/) if needed.
- Ollama installed from [ollama.com](https://ollama.com/) for local model responses. Start Ollama before opening Mavi.
- Internet access only for setup, model downloads, optional Discord, and websites you choose to visit.

Mavi does not bundle Python packages, Ollama, or model weights. On first setup, run `Setup-Mavi-Mac.command` from the app's `Contents/Resources` folder. It creates a Python virtual environment at `~/Library/Application Support/Mavi/runtime` and installs the app's listed Python requirements. Workspace data is kept separately at `~/Library/Application Support/Mavi/data`. Setup does not download models. It needs a network connection for Python package downloads, and it does not need administrator access.

If Mavi cannot find Python, set `MAVI_PYTHON3` to the full Python executable path before running setup, or install Python 3.11+ from python.org. The launcher checks the local `runtime/bin/python3` first, then common Homebrew/Python.org locations.

## Models

After installing Ollama, download a model that fits your Mac. For a modest starting point, install a standard Qwen 3 8B model in Ollama, then select it in Mavi. Model suitability depends on available memory and the task. Image generation uses separate, large model weights and can require substantial unified memory and disk space. Review Mavi's displayed requirements before choosing an image model. No model is downloaded automatically by the app.

## Install and update

When a macOS release is published, download the versioned `Mavi-<version>.zip` from Releases, verify its matching `.sha256` file, and move `Mavi.app` to `~/Applications`. Open the app after Python setup and Ollama are ready. For an update, quit Mavi and replace only `Mavi.app`; leave `~/Library/Application Support/Mavi` in place. Keep a copy of the previous app until the replacement starts successfully.

The public source build is signed locally for integrity checks but is not notarized. macOS may require an explicit user action to open it. Do not bypass a warning unless you obtained the archive from the expected release source and verified its checksum. The web app uses loopback port 8772 and will report an error if that port is already occupied; it does not attach to another local Mavi service.

## Privacy and permissions

Mavi's server binds to `127.0.0.1` only and its web UI uses a per-launch session cookie. Model requests go to the local Ollama service. Discord is optional and uses each user's own bot configuration. Discord tokens are entered in the dedicated local field and remain in memory only for that launch; do not paste them into chat or a terminal.

Browser links that leave Mavi open in your default browser. Website login information remains in that browser's normal profile. Mavi does not copy browser cookies. Computer assistance may require macOS Screen Recording and Accessibility permission; review the requested permissions and enable them only if you choose that feature. Mavi does not silently install permissions or grant access.

The standalone web client is a separate implementation from the native SwiftUI client. It does not claim complete parity with every native Mac feature. Windows behavior and Mac package installation must be checked on their target machines before calling those releases fully qualified.

## Build from source

From the repository root on Apple Silicon with Xcode Command Line Tools installed:

```sh
MAVI_VERSION=2.0.1 MAVI_BUILD_NUMBER=26 ./app/build_web_macos.sh
```

The build creates an ad-hoc signed app under a temporary build directory and a versioned ZIP plus SHA-256 sidecar in `app/dist/`. Set `MAVI_SIGN_IDENTITY` to an existing signing identity if you have one. A Developer ID signature and notarization are needed for the standard trusted-download experience.
