# Mavi changes

## 2.0.0-preview.3 — built-in surprise

- Included the author-approved fireworks Easter egg in every Windows/web package, ready on first launch without a private configuration file.
- The animation runs entirely in the app, consumes no model inference, respects reduced-motion settings, and does not create a chat or send a task.
- Custom personal-touch phrases remain local. Chats, training data, credentials, profiles, and other personal information remain excluded from releases.

## 2.0.0-preview.2 — setup and task routing candidate

- Added a guided Windows installer, local setup diagnostics, source-only release checks, and a documented update path that preserves each user’s data.
- Added per-task approval choices for computer work: ask before each action or allow bounded routine navigation. Typing and consequential changes remain reviewable.
- Improved native routing for document creation and local Discord status, with separate reporting for the native and web connections.
- Added a whole-computer task scope for reviewed transitions between supported apps, alongside single-app control.
- Added explicit app targeting for supported browsers and meeting apps, with default-browser link opening and window disambiguation.
- Moved native mode selection inside the composer and softened its layout.
- Added automatic learning-site guidance for reading instructions, tracking progress, pausing for login, and reviewing consequential submissions. Canvas, Pearson, and McGraw Hill live workflows still need verification.
- Added optional personal-touch animations configured entirely in the current user’s local settings. Public packages contain no preset personal messages.
- Target Windows execution, GPU image generation, and desktop-control verification remain pending; this is a development candidate.

## 2.0.0-preview.1 — public-source development candidate

- Added local Excel workbook creation with multiple sheets, formulas, tables, charts, number formats, and list validation, plus PowerPoint export. Formulas are stored for recalculation in a spreadsheet application.
- Added concise response policies, task-specific output budgets, and filtering of model reasoning text. Routine progress updates do not need a narration model call.
- Added a subtle image-generation animation with reduced-motion support and actual timing information. Progress percentages are shown only when supplied by the backend.
- Added optional official FLUX.2 Klein 4B generation alongside standard Qwen generation and editing. Downloads are separate and require confirmation; hardware checks precede loading.
- Improved Discord command help, task ownership checks, attachment delivery, and handling of interrupted requests. Discord connection remains opt-in and needs a user-created bot.
- Documented friend-owned Discord setup: least-privilege bot permissions, per-launch chat enable and token entry, separate task opt-in, and a two-install isolation/update acceptance checklist.
- Added a one-prompt Codex Windows installation guide with hardware-aware model consent, local-data isolation and an honest machine-side verification checklist; packaged installation scripts and the guide with source releases.
- Added persistent failed/stopped task notices, shared evidence for local workers, and bounded file caching.
- Fixed the Windows launcher's handling of Python paths containing spaces. Added source-only update archives with SHA-256 checksums and extraction checks.

### Validation limits

The Mac source compiles and local regression checks pass. Local chat, basic file creation, and a live Discord `!mavi ask` chat have been exercised on macOS. Windows execution, Windows Discord operation, the two-install Discord acceptance test, Windows desktop control, CUDA image generation, and model training still need tests on their actual target services and hardware. Mock tests do not establish model quality or hardware performance. This public-source candidate is not a finished Windows release; native macOS distribution still needs appropriate signing/notarization.

### Updating

Download the complete versioned Windows source ZIP and its matching `.sha256` file. Run `Update-Mavi.cmd` and select the ZIP. The updater extracts a new release folder; local settings, chats, preferences, Discord channel/user IDs, and models stay in the user's separate Mavi data directory. Discord tokens are memory-only and must be re-entered after launch. Keep the previous version until the replacement works.
