# Install Mavi with Codex on Windows

The public source repository is [github.com/4bdullahmk/mavi](https://github.com/4bdullahmk/mavi). The Windows release is still a candidate and must not be described as fully validated until its target-machine checks pass.

## One-prompt setup

Paste this prompt into Codex on the recipient's Windows 10/11 laptop:

```text
Set up Mavi for me on this Windows laptop from https://github.com/4bdullahmk/mavi.git.

Read AGENTS.md, INSTALL_WITH_CODEX.md, README.md, and WINDOWS.md first. Use the repository's official Install-Mavi.cmd / Install-Mavi.ps1 setup path; do not invent a second installer. Inspect this machine's Windows version, Python version, RAM, free storage, NVIDIA GPU model and VRAM before choosing models or optional features. Explain the results and any unavailable features accurately.

Keep all chats, preferences, Discord IDs, files and other personal state in my own %LOCALAPPDATA%\Mavi data directory. Never copy data, bot credentials, tokens, logs, profiles, adapters, models or personal paths from another installation or the repository. Do not put secrets in Codex chat, source, shell history, or logs. Keep the service on 127.0.0.1 and preserve Windows security settings.

Install only the standard runtime prerequisites required to launch Mavi. Do not download model weights automatically. Ask me before each optional or large model download, training setup, Discord connection, browser/computer control, or other feature that sends data outside my computer. Use a modest standard local chat model only after I approve its download. Do not choose an image model based on RAM alone; image generation needs compatible NVIDIA VRAM and may still fail its live memory preflight.

Run the documented automated checks, then launch Mavi and verify a real local chat and one small file export. Confirm onboarding starts with empty user data and optional profile personalization off. The bundled UI-only Easter egg is already included; no separate personal-touch file or import is needed. If I choose Discord, configure only my own bot, private channel and allowed user IDs; ask me to enter the token directly in Mavi's dedicated field, never in chat. Keep Discord chat disabled until I enable it, and leave remote tasks disabled unless I separately opt in. Explain that the token must be re-entered after restart and that local IDs survive release updates.

Do not call Windows setup complete until the actual Windows checks have run. Report passed checks separately from mocks, untested hardware features, and any manual action still needed. Do not publish or upload anything.
```

The installer and app should do the routine setup. The recipient may still need to approve optional model downloads, enter their own Discord token inside Mavi, and grant explicit permissions for optional controls. Do not send those secrets or approvals through Codex chat.

## What setup does

The installer verifies Python 3.11 or newer and Ollama, prepare Mavi's isolated Python environment, and start the local app. It must not request administrator access, alter execution policy, expose the service to the network, or download model weights without approval. Runtime state belongs under `%LOCALAPPDATA%\Mavi`; release source and personal state are separate.

The default small chat model is optional and is not bundled. A 32 GB machine is a general-workspace target, not a guarantee that a particular image model fits. Inspect actual free RAM, GPU, VRAM, and disk before optional setup. See [Windows requirements and feature limits](WINDOWS.md).

## Future updates

For a public GitHub release, download the versioned ZIP and its matching SHA-256 file from Releases, then use the release's `Update-Mavi.cmd`. The updater must validate the checksum and extract a new release without replacing `%LOCALAPPDATA%\Mavi` user data. Keep the old release until the replacement starts and passes a real-chat check; do not delete user data as part of an update. The recipient's Discord channel and allowed-user IDs should persist, while the bot token is intentionally memory-only and must be entered again after restart.

Source updates by Git should likewise leave `%LOCALAPPDATA%\Mavi` untouched. Never run an unverified release archive, overwrite the only working release, or treat a checksum as proof of publisher identity.

## Validation status

This is a public-source Windows candidate. The repository's CI and mocked tests do not establish successful installation or feature parity on a recipient's laptop. Windows setup, GPU image generation, desktop automation, dictation, and live Discord must be tested on the target Windows machine before claiming those capabilities are verified.
