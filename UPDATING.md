# Update Mavi

## Install or update from a GitHub release

Each Windows release provides a versioned source ZIP and a matching `.sha256` sidecar. Download both from the same GitHub release. From the current Mavi release folder, run `Update-Mavi.cmd` and enter the ZIP path. The updater verifies the sidecar and extracts a new release under `%LOCALAPPDATA%\Mavi\Releases`; it does not replace the current release.

Start the extracted version with its `Install-Mavi.cmd` for first-time setup, or `Start-Mavi.cmd` if its runtime is already prepared. Keep the previous release until the new one starts and passes a real local chat check. Chats, preferences, generated files, model weights, and Discord IDs stay in `%LOCALAPPDATA%\Mavi`; the Discord token is memory-only and must be entered again after restart. Never copy another person's data or credentials into this release.

A SHA-256 match confirms that the ZIP matches the sidecar you downloaded. It does not by itself prove who published either file; download them from the intended GitHub repository and release.

## Prepare a reviewed release

The `Release Mavi Windows source candidate` workflow is manual. It runs the reusable validation workflow on both Windows and Linux before packaging or publishing anything.

1. Make and review the source changes, update `CHANGELOG.md` with a heading for the exact version, and run the repository tests and source-hygiene check.
2. Push the reviewed commit to the repository. In GitHub Actions, open `Release Mavi Windows source candidate` and choose **Run workflow**.
3. Enter a strict semantic version such as `2.0.0` or `2.0.0-preview.2`. The prerelease option defaults to true. Leave **Source commit** blank to use the exact commit that triggered the run, or enter a full 40-character commit SHA. Branch names and tags are rejected.
4. Review both platform gates. After they pass, the workflow builds a deterministic source-only ZIP, verifies its SHA-256 sidecar and packaged-file allowlist, and publishes the ZIP and sidecar as GitHub release assets. It refuses a used tag or a missing version section in `CHANGELOG.md`.
5. Inspect the GitHub release and download its two assets to verify the ZIP and checksum before sharing it.

The release job has write permission only for GitHub contents and starts only after the Windows and Linux validation gates pass. It does not package the local Mavi data directory, logs, caches, credentials, or model weights. CI parsing and mocked tests do not establish real Windows GPU image generation, desktop automation, microphone, or live Discord support; consult [WINDOWS.md](WINDOWS.md) for target-machine checks before describing those features as verified.
