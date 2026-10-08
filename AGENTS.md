# Mavi contributor and setup instructions

- Mavi 2.0.0 has a macOS desktop release and a Windows source/setup package. Do not claim verified Windows execution or complete platform parity until the target Windows checks in WINDOWS.md are satisfied. Keep unverified platform capabilities explicit in release notes.
- For a recipient's one-prompt Windows installation, follow INSTALL_WITH_CODEX.md and the official Install-Mavi.cmd / Install-Mavi.ps1 path. Inspect hardware before model selection, keep user state under that user's %LOCALAPPDATA%\Mavi, and distinguish installer smoke checks from release qualification. Do not upload or publish from setup.
- Never copy another installation's data into source or a release: no chats, profile notes, training data, adapters, model weights, logs, credentials, browser profiles or absolute personal home paths.
- Data belongs in the current user's local Mavi data directory. A fresh install starts with empty user data and personalization off.
- The existing short built-in UI strings are intentionally public. This narrow exception never permits other personal information or training data. Keep undocumented UI details out of setup guidance and release notes.
- Use standard official models by default. Optional user-selected uncensored/abliterated chat or coding models may remain available; this never changes execution permissions. Do not bundle or offer uncensored image models.
- Inspect RAM, GPU model/VRAM and free disk before selecting large models. Do not assume 32 GB RAM guarantees image-pipeline support. Ask before large optional downloads.
- Keep the web service on loopback, retain host/origin/session checks, never disable browser or OS sandbox/security, and never copy login cookies into automation profiles.
- Explain and obtain permission before enabling remote Discord or computer control for a new user. Credentials belong in the dedicated local setup field, never chat or source.
- Use local models for bounded coding proposals when available. Review exact diffs, verify file hashes and run tests before applying or releasing. Never execute arbitrary generated code to validate a proposal.
- For an installation task, run the checks listed in INSTALL_WITH_CODEX.md, then verify a real local chat and small file export on the recipient's machine. Report untested Windows features honestly.
- For a developer/release qualification task, run `python -m unittest discover -s tests -v` and `python test_update.py -v` from the repo root. Also test real local chat, onboarding, history deletion, generated-file download and clean update preservation. Test Windows GPU/control features on Windows; mocks are not substitutes.
- Releases use a versioned ZIP plus SHA-256 sidecar and a clear changelog. User data stays outside release folders. Never delete an existing working installation before verifying its replacement.
