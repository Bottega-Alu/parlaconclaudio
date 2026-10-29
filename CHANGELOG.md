# Changelog

Detailed notes for each release: [`docs/releases/`](docs/releases/) and the [GitHub releases](https://github.com/Bottega-Alu/parlaconclaudio/releases).

## Unreleased

### Security
- API keys are stored in Windows Credential Manager; keys left in plaintext by older versions are moved there at start-up.
- The voice hook no longer runs a `git.exe` / `ffmpeg.exe` placed inside the project folder.
- Dictated text stays out of Windows clipboard history and cloud clipboard.
- Dictation history can be switched off; uninstall deletes it and removes the agent hooks that run from the app folder.

### Performance
- Dictation works without an NVIDIA GPU: Whisper small on the CPU.
- The local model follows the hardware (smaller or quantized on cards with little VRAM).
- About 0.3 s less per dictation with LLM cleanup (reused HTTPS connection, opened while you speak); faster GPU decoding; warm-up after loading; shorter wait before pasting.

### Sound packs
- The installer ships only the R2-D2 pack; the others are verified add-ons downloaded on demand and updated daily without reinstalling.

## 1.0.19 and earlier

See [`docs/releases/`](docs/releases/).
