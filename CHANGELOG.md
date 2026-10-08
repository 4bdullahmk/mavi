# Mavi changes

## 2.0.1 — browser and desktop control fixes

- Route explicit browser targets and URLs to the requested browser, and return to the active control flow when a permission request is declined or needs follow-up.
- Require the local model's structured action response to match the action schema, clarify the approval prompt, and guard message sending until the pending question is answered.
- Restrict course assignment help across Canvas, Pearson, McGraw Hill, and SmartBook to user-supplied or assigned materials, with visible-source citations, missing-material questions, and review before graded submissions. This is prompt guidance, not training; it does not add course answers.
- Use bounded accessibility targets to identify visible links and buttons for reviewed computer actions.
- Convert accessibility target coordinates correctly across displays and inspect a bounded depth for nested course-page controls.
- Keep accessibility target labels and action history compact, and display a redacted local error when a request is rejected.
- Keep typed answers intact across progress polls and allow immediate app restarts after a connection closes.
- Save the user's approval preference per course workflow and keep an in-flight response intact while status updates arrive.
- Initialize the macOS automation helper on the main application run loop to avoid a capture-time crash.
- Verify read-only Brave window capture with Screen Recording and Accessibility permissions enabled. Windows execution and hardware qualification remain pending.
- Build 27 adds course-source guidance to Mac and Windows computer-control prompts, including synthetic step-by-step controller calls.
- Build 28 keeps class assignment help grounded in materials the user supplies or the course assigns. Missing material is requested rather than replaced with web searches or general knowledge; source facts are distinguished from calculations and visible citations are not invented.
- Build 28 allows a user-requested full SmartBook practice activity to continue until it finishes or the user presses Stop. Mac controls use bounded targets for visible links, buttons, checkboxes and radio buttons. One monitored SmartBook practice item was answered correctly using a class-source excerpt supplied by the user; this does not verify autonomous source discovery or full-workbook completion.
- Build 28 detects an existing Apple Silicon MLX-Gen runtime with complete official Qwen Image generation/edit checkpoints. It runs locally in offline mode, checks available unified memory before loading, and does not download image models automatically. Progress displays backend-reported step counts and an ETA only after enough observed steps; it does not invent percentages. Real image generation and quality remain unverified for this build.

## 2.0.0 — desktop web workspace and task continuation

- Add a macOS desktop wrapper for the portable web workspace with local runtime setup and stable workspace storage.
- Add macOS app and browser task routing, including Canvas requests, with normal browser profiles, private sign-in handoffs, and reviewed input actions.
- Continue compound tasks after opening their app, retaining bounded task progress per chat while reacquiring current windows.
- Add a chat context indicator, Continue action, and option to return to general chat. App context does not retain computer-control permissions.
- Keep long histories within the sidebar and group computer-control settings in a compact disclosure; align startup branding and light/dark window colors.
- Preserve typed approval answers during progress updates and support immediate clean app restarts without mistaking TCP cleanup for an occupied port.
- Add regression coverage for platform routing, compound tasks, context isolation, cancellation, and login handoffs. Windows hardware qualification remains pending.

## 2.0.0-preview.5 — Mac application routing

- Route explicit Discord launch requests to the installed macOS application before model planning.
- Remove unnecessary interface hints and simplify setup screens.
- Preserve local data, permissions, and the independent Discord connection for each installation.

## 2.0.0-preview.4 — guided Discord setup

- Replaced the Discord settings form with a four-step setup guide, generated bot invite links, ID instructions, input validation, connection status, and clear chat versus local-task permissions. Each installation connects its own bot; tokens remain in memory and must be re-entered after restart.
- Updated native Mac interface rendering and reduced-motion support. Windows hardware and live recipient Discord testing remain pending.

## 2.0.0-preview.3 — interface polish

- Improved local interface behavior and accessibility support.
- Chats, training data, credentials, profiles, and other personal information remain excluded from releases.

## 2.0.0-preview.2 — setup and task routing candidate

- Added a guided Windows installer, local setup diagnostics, source-only release checks, and a documented update path that preserves each user’s data.
- Added per-task approval choices for computer work: ask before each action or allow bounded routine navigation. Typing and consequential changes remain reviewable.
- Improved native routing for document creation and local Discord status, with separate reporting for the native and web connections.
- Added a whole-computer task scope for reviewed transitions between supported apps, alongside single-app control.
- Added explicit app targeting for supported browsers and meeting apps, with default-browser link opening and window disambiguation.
- Moved native mode selection inside the composer and softened its layout.
- Added automatic learning-site guidance for reading instructions, tracking progress, pausing for login, and reviewing consequential submissions. Canvas, Pearson, and McGraw Hill live workflows still need verification.
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

The full automated suite passes with one skipped test, and the updater and source-hygiene checks pass. Local chat, basic file creation, a live Discord `!mavi ask` chat, read-only Brave capture, and one monitored SmartBook practice answer have been exercised on macOS. The SmartBook check used a class-source excerpt supplied by the user and did not verify a complete workbook. Windows execution, Windows Discord operation, the two-install Discord acceptance test, Windows desktop control, CUDA image generation, Apple Silicon image generation for this build, and model training still need tests on their target services or hardware. Mock tests do not establish model quality or hardware performance. The public Mac app is not notarized.

### Updating

Download the complete versioned Windows source ZIP and its matching `.sha256` file. Run `Update-Mavi.cmd` and select the ZIP. The updater extracts a new release folder; local settings, chats, preferences, Discord channel/user IDs, and models stay in the user's separate Mavi data directory. Discord tokens are memory-only and must be re-entered after launch. Keep the previous version until the replacement works.
