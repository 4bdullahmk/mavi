# Mavi

A local AI workspace for conversation, files, development and creative tools. A clean installation contains no personal profile, conversation history, training examples, adapters, credentials or model weights.

**Mavi 2.0.1** provides a macOS desktop app and a Windows source/setup package. The macOS app uses the same web workspace as the portable client, with a separate macOS automation adapter. Windows execution and hardware features have not been tested on a Windows machine. The Mac download is not Apple-notarized. See [Mac setup](README-Mac.md) and [Windows readiness](WINDOWS.md) for requirements and verification limits.

## macOS desktop

See [Mavi for macOS](README-Mac.md) for the standalone web interface, local Python setup, and update instructions. The desktop app stores workspace data in Application Support, outside the application bundle.

For browser tasks, specify the site and goal, for example: “Go into Canvas and open the assignment for my course.” Mavi uses the normal default browser and asks for the course or assignment link when it is unclear. It pauses for private sign-in and approvals. macOS requires Screen Recording and Accessibility permissions, and screen tasks need a local vision model. Each chat can retain its selected app and task progress; returning to that chat reacquires the current window. Saved task context never grants permission to control the computer.

## Course workflow guidance

For Canvas, Pearson, McGraw Hill, SmartBook, and class assignment requests, Mavi adds guidance to use only materials you supply or the course assigns. It asks for missing materials instead of filling gaps with web searches or general knowledge, cites visible chapter/page/slide details without inventing them, distinguishes source facts from derived calculations, and pauses for review before graded submission. You can explicitly broaden the source scope. This is prompt guidance, not model training. Synthetic tests cover the policy; live course assignment completion has not been verified.

## Windows setup

For a one-prompt setup by Codex, follow [Install Mavi with Codex](INSTALL_WITH_CODEX.md). The public source repository is [github.com/4bdullahmk/mavi](https://github.com/4bdullahmk/mavi). Its official `Install-Mavi.cmd` / `Install-Mavi.ps1` path sets up the local runtime; the app itself remains on loopback at `http://127.0.0.1:8769`. All chats, preferences, Discord IDs, and generated files stay in the recipient's `%LOCALAPPDATA%\Mavi` data folder, outside the release source.

For a 32 GB machine, start with a standard 8B chat model. Model suitability depends on the actual GPU, VRAM, free RAM and task. A 30B coding model is optional and may be slow or require more memory than expected. Optional image generation/editing is much more demanding. The optional FLUX.2 Klein 4B generator has a smaller local checkpoint than Qwen Image; Black Forest Labs reports about 8 GB VRAM, but neither 32 GB RAM nor a particular NVIDIA card guarantees it will fit. The Qwen-Image-2512 model card currently lists a 20B BF16 model and 57.7 GB repository, and a 32 GB computer will not pass this app's Qwen RAM gate. Mavi checks live RAM/VRAM before loading. See [Windows image requirements](WINDOWS.md#image-generation-and-editing) before downloading separate image checkpoints.

If you choose a model after setup, `py -3 Setup-Models.py chat` downloads the standard small starter. Optional commands select standard coding, vision, FLUX or Qwen image, image-edit or dictation models. Model weights are never included in the release; optional image downloads require confirmation. These scripts never import another person's personal data.

## Personalization

First launch offers a clean start or optional personalization. Copy Mavi's preference-summary prompt into ChatGPT, then paste or import the reviewed text. The prompt asks ChatGPT to use only context it can actually see. Names, credentials and sensitive details are excluded by default. The imported summary is local prompt context, not model training. No training happens automatically.

## Future updates

Each release should publish a versioned Windows ZIP and matching SHA-256 sidecar. Download them from the public GitHub Releases page. Run `Update-Mavi.cmd`, verify the archive, and start the new folder. Settings, history, local model caches, Discord channel/user IDs and preferences are outside the release folder. Keep the old folder until the new one works. A checksum detects corruption; it is not a substitute for trusting the publisher. The Discord token is memory-only and must be entered again after launch.

No update executable is downloaded or run silently. Native macOS binary distribution additionally needs appropriate Apple signing/notarization for the standard trusted-download experience.

## Discord (optional)

Each person must connect their own Mavi installation to their own Discord bot and private channel. A clean install has no bot token, server/channel ID, or allowed-user IDs. Create a bot application and private channel in your own Discord server, invite only the bot and intended members, and enter that bot's token in Mavi's Discord token field. Never reuse the maintainer's bot, token, server, or channel, and never paste a token into chat, a terminal, or the repository.

Grant only View Channels, Send Messages, Embed Links, Attach Files, and Read Message History (Discord permission total `117760`); do not grant Administrator. Enable Message Content Intent for the bot. Configure your own channel ID and the Discord user IDs allowed to issue commands. Those non-secret IDs are stored in your installation's local Mavi data and remain across release updates. The bot token is held in memory only and must be entered again each time Mavi starts. Choose whether to enable Discord chat for each launch. A separate “Allow Discord to start local tasks” setting is required for task, image, and edit commands; leave it off if you want chat only. Messages and files sent through Discord are visible to channel members.

Use `!mavi help` for commands. `!mavi ask` uses the local model and accepts UTF-8 text attachments. The computer running Mavi must stay awake and connected to the internet while the bot is in use. Tasks may pause for input or approval; enter credentials and complete private approvals on the computer, not Discord. A live `!mavi ask` chat was verified on the maintainer's Mac. The two-install isolation/update checklist and Windows Discord operation have not been tested.

## Workers and optional training

The Agent team workspace uses two distinct installed Ollama models for independent worker drafts, then reviews them against the same evidence. The two calls run concurrently only when Ollama reports both model sizes and current free RAM clears a conservative estimate (weights plus headroom); missing metadata or insufficient RAM serializes the calls. The reviewer reuses the first worker model to avoid loading a third model while the others may remain resident. Mavi never unloads Ollama models. Workers share selected project evidence through a bounded local read cache. This has mock coverage; Windows memory behavior and throughput still depend on the installed models and hardware.

Optional LoRA training uses a user-provided JSONL dataset and the official cached `Qwen/Qwen3-0.6B` base. It requires a compatible CUDA/PyTorch setup and is not GPU-tested on Windows. Install the optional dependencies only after selecting a CUDA build of PyTorch from the official PyTorch instructions:

```bat
py -3 -m pip install -r portable\requirements-training-optional.txt
```

Commands from the repository root:

```bat
py -3 portable\training_runtime.py validate --dataset "C:\path\examples.jsonl"
py -3 portable\training_runtime.py prepare --dataset "C:\path\examples.jsonl" --name "adapter-check"
py -3 portable\training_runtime.py train --dataset "C:\path\examples.jsonl" --name "my-adapter" --confirm-local-training
```

`prepare` is a dry run; `train` requires the explicit confirmation flag and an already-cached base model. No training starts automatically. Standard models are the default; users may select optional uncensored chat or coding models in Ollama. Image generation and editing remain restricted to the official standard Qwen image models.

## Validation

From the repository root:

```bat
py -3 -m unittest discover -s tests -v
py -3 test_update.py -v
```

Tests use temporary data and mocked models for deterministic checks. They do not establish actual GPU performance, Windows UI automation or image quality. The target-laptop smoke test is required before a finished release.

The Mac source is in `app/`. `app/build.sh` builds it locally; it requires Apple Silicon and Xcode Command Line Tools. Local development signing is not notarization.
