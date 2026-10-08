# Mavi

Mavi is a local-first desktop assistant for Apple Silicon Macs. This source package targets macOS 14.2 or newer and builds an ad hoc signed app with bundle identifier `app.mavi.desktop` and executable `Mavi`.

## Requirements

- Apple Silicon Mac running macOS 14.2 or later
- Xcode Command Line Tools (`xcode-select --install`)
- Python 3 (`/usr/bin/python3`)
- Ollama for local language models; install it separately from its official distribution

The build does not download model weights or bundle user data, adapters, or local models. Chat is most useful after installing a model. A small starter model can be added with `ollama pull qwen3:4b`; image generation and some specialist tools need their own optional local runtimes and model weights.

## Install from source

From this directory, run:

```sh
./setup.sh
```

The script builds Mavi, copies it to `~/Applications/Mavi.app`, and opens it. It offers to pull the optional `qwen3:4b` model only when Ollama is already installed. To build without installing, run `./build.sh`; the app and release archive are placed under a temporary build directory and `dist/` respectively.

Mavi asks macOS for Screen Recording, Accessibility, and microphone access only when the related feature needs it. Your conversations and preferences are kept in your local macOS account. Mavi does not inherit sign-ins from Codex or other apps.

## Build a release archive

Set version metadata and build the app plus a deterministic ZIP and SHA-256 sidecar:

```sh
MAVI_VERSION=2.0.1 MAVI_BUILD_NUMBER=27 ./build.sh
```

The outputs are `dist/Mavi-2.0.1.zip` and `dist/Mavi-2.0.1.zip.sha256`. The ZIP contains the `Mavi.app` bundle. Check the digest after downloading with:

```sh
shasum -a 256 -c Mavi-2.0.1.zip.sha256
```

The app is ad hoc signed for source builds and is not notarized. A public downloadable release should be signed and notarized before distribution.

## Update behavior

Mavi’s local candidate builder compiles a private candidate, checks it, and installs it with a staged swap and rollback path under `~/Library/Application Support/Mavi/Updates`. It does not fetch releases automatically. For a private GitHub release, sign into GitHub in your browser, download the versioned ZIP and its `.sha256` sidecar, and verify it with `shasum -a 256 -c`. Quit Mavi, unzip the archive, then replace `~/Applications/Mavi.app` with the downloaded `Mavi.app`. Your local settings and saved preferences remain under `~/Library/Application Support/Mavi`; do not remove that folder when upgrading. A downloaded build should be Developer ID signed and notarized before distribution.
