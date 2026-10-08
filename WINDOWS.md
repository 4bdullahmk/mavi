# Mavi for Windows

The Windows edition runs Mavi's local web client and portable server on your own computer. It uses Ollama for local model responses and listens only on `127.0.0.1:8769`.

This is a separate Windows client and a public-source candidate. The features below are implemented in the portable client, but Windows execution and hardware behavior have not been tested. Mock tests do not establish that all features work on Windows; do not announce this as a finished release.

For the recipient's one-prompt setup, follow [INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md). Codex should use the repository's `Install-Mavi.cmd` / `Install-Mavi.ps1` path, inspect the actual machine before model selection, keep personal state in `%LOCALAPPDATA%\Mavi`, and verify a real local chat. A successful script or mocked test alone does not establish Windows feature parity.

## Windows capability parity checklist

The table separates implemented portable features from Windows-specific checks that remain. The repository CI workflow runs syntax and mocked tests on Windows and Ubuntu, but no target-device run has been completed; do not assume every feature works on a recipient's machine.

| Capability | Current Windows implementation | Evidence and remaining check |
| --- | --- | --- |
| Local chat and saved conversations | Ollama chat, persistent history, stop/follow-up | Portable unit coverage; real Windows chat and persistence smoke test |
| Preferences, onboarding, and local memory | Clean start; opt-in preference import/edit/delete; no automatic training | Portable unit coverage; fresh Windows profile and deletion smoke test |
| Files and documents | Text/CSV tools; optional PDF/DOCX/XLSX helpers | Mocked/unit coverage; install optional packages and check Windows exports |
| Code assistant | Reviewable proposals and explicit apply with source-hash checks | Unit coverage; exercise a selected Windows project |
| Worker fleet | Two independent workers plus an evidence reviewer; parallel calls require known Ollama model sizes and a live RAM headroom check, otherwise worker calls serialize; bounded shared cache for project evidence | Mock tests cover worker flow, cache reuse, and capacity fallback; measure behavior on the target machine |
| CAD and 3D | OpenSCAD source generation and optional STL rendering | Check OpenSCAD and manually review STL in Bambu Studio on Windows |
| Stock tools | Deterministic analysis of an attached CSV; no live market or brokerage access | Unit coverage; confirm the intended CSV workflow |
| Image generation/editing | Optional official FLUX.2 Klein 4B Diffusers generator; Qwen create/edit remains available; up to three source images for Qwen edit; bounded memory preflight and CPU offload | Mock/runtime checks only; install optional dependencies and official weights, inspect CUDA capacity, then test generation/editing on Windows |
| Browser tasks | Wired to the focused-window automation; existing browser profile; explicit confirmation, Continue handoff for private sign-in, and steering at step boundaries | Mock tests only; test real browser focus, private handoff, and stop behavior on Windows |
| Dictation | Local microphone-to-WAV UI and offline transcription adapter | Mock coverage; test microphone and local Whisper model on Windows |
| Computer control | Selected-window visual control with focus checks, bounded actions, repeat detection, and steering | Mock tests only; test primary-display control and stop behavior on Windows |
| Discord remote session | Optional own-bot chat, configured channel and allowed user IDs, per-launch chat enable, separate local-task opt-in; token held in memory | Mock dispatch/transport tests; `!mavi ask` verified with a live bot on the maintainer's Mac; Windows operation and two-install acceptance remain untested |
| Generated files gallery | Lists outputs with per-file and clear-all deletion confirmation | Portable route/UI implementation; verify on Windows |
| Self-edit | Isolated source candidates with Python syntax gate, no live auto-deploy | Unit coverage; review candidate and verify build/update workflow on Windows |
| Optional model training | Explicit `portable/training_runtime.py` JSONL validate/prepare/train CLI; CUDA LoRA on cached official `Qwen/Qwen3-0.6B` | Validation and dry-run mocks only; training has not been GPU-tested on Windows |
| Downloadable updates | Versioned archive staging and checksum/path/size checks | Test native Windows upgrade and rollback |

### Image generation and editing

Image generation stays unavailable until its optional packages and a compatible local checkpoint are installed. The optional [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) checkpoint is a four-step text-to-image model; its current repository is about 23.7 GB. Black Forest Labs says Klein 4B fits in about 8 GB VRAM on consumer cards, which is a reported target, not a guarantee for this application or a specific GPU. When installed, Mavi prefers FLUX for prompt-only generation. Qwen create/edit checkpoints remain separate downloads: [Qwen/Qwen-Image-2512](https://huggingface.co/Qwen/Qwen-Image-2512) and [Qwen/Qwen-Image-Edit-2511]. The Qwen-Image-2512 card currently lists a 20B BF16 model and a 57.7 GB repository. Check the current model cards and leave ample additional disk space before downloading. Mavi does not download weights during generation.

The conservative preflight stops before loading unless free physical RAM is at least the installed checkpoint's local weight-file size plus a margin of 6 GiB or 15% (whichever is larger), and at least 8 GiB of CUDA VRAM is free. These are safety gates, not a guarantee that inference will fit or run quickly. FLUX uses model CPU offload; Qwen uses sequential CPU offload. Both may be slow and still require substantial system RAM. A 32 GB computer cannot meet the preflight for Qwen-Image-2512; the smaller FLUX checkpoint may pass depending on the actual installed weights and free RAM, but 32 GB alone does not guarantee that. The adapter uses one 768×768 output (four steps for FLUX, 24 for Qwen), up to six attachments and three source images; each image is capped at 20 MB and 20 megapixels, then reduced to 1024 pixels on its longest side. Source-image editing remains on Qwen Edit; Mavi does not claim FLUX face/image-edit support in this app. Stop is checked before and after model loading and between inference steps; it cannot interrupt checkpoint loading itself. Prompt guidance discourages unrequested text, but generated lettering may still be inaccurate. This path has not been exercised on a Windows GPU.

Before downloading image weights, install a CUDA-compatible PyTorch build from the [official PyTorch selector](https://pytorch.org/get-started/locally/). Then install the Diffusers dependencies in Mavi's virtual environment:

```bat
%LOCALAPPDATA%\Mavi\venv\Scripts\python.exe -m pip install --upgrade "diffusers>=0.37.1" transformers accelerate huggingface-hub
```

Follow the official model cards if a newer Diffusers build is required. Then use `py -3 Setup-Models.py image-flux` for the optional smaller generator, or `py -3 Setup-Models.py image` / `image-edit` for Qwen. Each command asks before downloading the official checkpoint. With FLUX installed, prompt-only generation uses it by default; in the same Command Prompt, run `set MAVI_IMAGE_BACKEND=qwen` before `Start-Mavi.cmd` to prefer Qwen generation. Source-image editing continues to use the Qwen edit checkpoint. The image path remains disabled until its optional dependencies and an applicable local checkpoint are present.

Browser and computer modes are wired through the server and UI, but require the optional Windows automation packages and a local vision model. They require approval before reading a window and before consequential actions; they use the same focused HWND and do not read browser cookies or profiles or launch another profile. A private sign-in pauses for the user; after explicit `continue`, the task resumes only if the same window is focused. No screenshot or model call is made while waiting for that response. The automation is limited to the primary display. Dictation accepts a WAV recording and requires an already-installed local CTranslate2 Whisper model; it does not download model weights.

### Discord setup and two-install acceptance check

Discord is optional. Every installation must use its owner's own Discord bot, private channel, and allowlisted user IDs. A clean installation starts without a token or IDs. Do not copy another person's bot token or server configuration. In Discord's Developer Portal, enable Message Content Intent. Grant the bot only View Channels, Send Messages, Embed Links, Attach Files, and Read Message History (permission total `117760`); do not grant Administrator. Keep the channel private because its members can see messages and files Mavi sends.

Enter the token only in Mavi's Discord token field. It is memory-only and must be entered again after every Mavi restart. Channel and allowed-user IDs are non-secret local settings under `%LOCALAPPDATA%\Mavi`; they should survive release updates. Enable Discord chat separately on each launch. The “Allow Discord to start local tasks” setting is a distinct opt-in for task, image, and edit commands. Keep the Windows computer awake and online while using the bot. No live Windows bot test has been completed; use this checklist as a release acceptance test, not as a claim that it has passed:

1. Set up two clean installations under separate Windows accounts or on two computers so each has its own `%LOCALAPPDATA%\Mavi` data directory. If using one computer, test the instances sequentially because both use local port 8769. Confirm neither has a token, channel ID, or allowed-user ID before setup.
2. Create a different bot and private channel for each installation. Configure each bot only with its own channel ID and intended user's Discord ID. Do not share tokens between installations.
3. Enter each token, enable chat for that launch, and send `!mavi ask` from the allowed account in the configured channel. Confirm each bot answers from its own local Mavi host. Confirm a non-allowlisted user and a message in the other installation's channel do not run commands.
4. Restart each Mavi instance. Confirm chat starts disabled until enabled again and each token must be re-entered, while that installation's own channel and allowed-user IDs remain configured. Confirm `!mavi task` is unavailable until that installation separately enables local tasks.
5. Update installation A using the documented versioned ZIP flow. Confirm A's channel and allowed-user IDs remain, its token still requires re-entry, and installation B's configuration is unchanged. Repeat for B if validating both update paths.
6. Confirm neither bot has Administrator, tokens are absent from logs/chat/repository files, and each bot can see only its intended private channel. Record any failure before treating the Discord workflow as accepted.

The general portable dispatch has mock tests, and `!mavi ask` has been verified with a live bot on the maintainer's Mac. The two-install acceptance test and Windows bot operation have not been run. Do not represent Discord-on-Windows as verified until a Windows host completes it.

Automated checks verify separate local workspaces, token-free persistence, disabled access after restart, bot identity mismatch rejection, and preservation of two users' external data across verified ZIP updates. Browser checks on macOS also covered both first-run choices, optional Discord setup/skip, blank settings for a second workspace using the same browser, and saved IDs after a server restart. These checks do not replace the live Windows checklist above.

## Requirements

- Windows 10 or 11, 64-bit
- Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/). Enable the Python launcher during setup.
- Ollama for Windows from [ollama.com](https://ollama.com/download/windows)
- 32 GB+ RAM is the intended target for the general workspace, not for the optional image pipelines. Image use has a separate high-memory preflight and needs a supported NVIDIA GPU; Ollama may also run supported chat models on CPU.
- A local Ollama model; the optional small starter is `qwen3:4b`

Mavi does not install Ollama or download model weights automatically. After installing Ollama, open Command Prompt and run `ollama pull qwen3:4b` if you want the starter model. You can choose another compatible model later.

## Optional local tools

These are optional Windows-only dependencies. Install them into Mavi's virtual environment from Command Prompt after starting and stopping Mavi once:

```bat
%LOCALAPPDATA%\Mavi\venv\Scripts\python.exe -m pip install pyautogui pygetwindow Pillow
```

That enables browser and computer modes when paired with a compatible local vision model. Keep the library's fail-safe enabled; moving the pointer to the primary display's upper-left corner stops automation.

For computer or browser work, approve the task in Mavi and focus the window you want to use during the five-second countdown. Mavi asks you to confirm that exact window; return to it during a second countdown. Consequential confirmations also pause so you can refocus that same window. If it is not focused when the countdown ends, Mavi stops without sending input. For private sign-in, enter credentials directly in the selected window and reply `continue`; the task resumes only after the same window is focused again. No screenshot is taken while waiting for your response.

Offline dictation additionally needs `faster-whisper` and a compatible CTranslate2 Whisper model that you provide yourself. Install the package with:

```bat
%LOCALAPPDATA%\Mavi\venv\Scripts\python.exe -m pip install faster-whisper
```

Place the model files (including `model.bin`) under `%LOCALAPPDATA%\Mavi\models\whisper`. Mavi uses that local folder and does not fetch model weights. The UI can record local microphone audio into a 16 kHz WAV, or accept an existing WAV. It requires browser microphone permission only when Record is clicked.

### Optional local model training

Training is a separate CLI, not a UI feature. Use a UTF-8 JSONL dataset you provide; `validate` checks it without loading a model, and `prepare` is a dry run. Training needs a compatible CUDA build of PyTorch, the packages in `portable\requirements-training-optional.txt`, at least 8 GiB total and 6 GiB free VRAM, and an already-cached official `Qwen/Qwen3-0.6B` model. The training path has not been GPU-tested on Windows.

From the repository root in Command Prompt:

```bat
py -3 -m pip install -r portable\requirements-training-optional.txt
py -3 portable\training_runtime.py validate --dataset "C:\path\examples.jsonl"
py -3 portable\training_runtime.py prepare --dataset "C:\path\examples.jsonl" --name "adapter-check"
py -3 portable\training_runtime.py train --dataset "C:\path\examples.jsonl" --name "my-adapter" --confirm-local-training
```

Select and install the matching PyTorch CUDA build separately using PyTorch's official installation instructions. Training does not download a base model, modify Mavi's active model, or start automatically. Standard models remain the default; optional uncensored chat or coding models may be selected separately. Image generation/editing uses only the standard official Qwen image models.

## Start Mavi

For a source checkout or a newly downloaded versioned release ZIP, run `Install-Mavi.cmd` from its folder for first-time setup. It checks Python/Ollama, creates the runtime environment under `%LOCALAPPDATA%\Mavi`, asks before downloading a standard chat model, smoke-tests a real local response, then starts Mavi. Once setup is complete, use `Start-Mavi.cmd` for later launches. Keep the server console open and visit [http://127.0.0.1:8769](http://127.0.0.1:8769). Press Ctrl+C to stop it. See [INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md) for hardware inspection, model consent, and the recipient setup prompt.

The launcher checks for Python 3.11+, the Ollama command, and its local service. It prints official installation links if Python or Ollama is missing. It does not request administrator access, change PowerShell execution policy, or run downloaded shell commands.

Mavi's Windows settings and user files belong under `%LOCALAPPDATA%\Mavi`. Treat the local server as private to this Windows account and keep it bound to loopback; do not change the bind host to expose it to a network.

## Update a GitHub release

Download the versioned Windows ZIP and matching `.sha256` sidecar from the public Releases page. Double-click `Update-Mavi.cmd` in your existing release folder and paste the downloaded ZIP path without surrounding quotes. It verifies the sidecar and extracts the new release under `%LOCALAPPDATA%\Mavi\Releases`. Start it with the new `Start-Mavi.cmd`; settings and local state remain under `%LOCALAPPDATA%\Mavi`. You can open the exported STL manually in Bambu Studio, review the slicer output, and start a print yourself; Mavi does not send print jobs. Keep the previous release folder until the new one starts successfully.

To verify manually instead of using `Update-Mavi.cmd`, check the `.sha256` sidecar in PowerShell before extracting:

```powershell
(Get-FileHash .\Mavi-windows.zip -Algorithm SHA256).Hash.ToLower()
```

Compare the printed hash with the `.sha256` value shown on the release. The repository maintainer should publish both files together.

The in-app `update` tool prepares a separate source candidate under `%LOCALAPPDATA%\Mavi\self-updates`. It never replaces the running release. Review the candidate diff and test it yourself before using it as a release source. Mavi checks Python syntax only; it does not execute generated code or generated tests.

## Troubleshooting

- If Python is missing or too old, install 64-bit Python 3.11 or newer and enable its launcher, then open a new Command Prompt.
- If Ollama is missing, install it from the official Windows download page, start it once, and rerun the launcher.
- If Mavi opens but has no model response, run `ollama list` and install a compatible model such as `qwen3:4b`.
- If port 8769 is already in use, close the other local service using it before starting Mavi.
