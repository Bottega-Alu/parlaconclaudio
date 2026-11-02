# Changelog

Detailed notes for each release: [`docs/releases/`](docs/releases/) and the [GitHub releases](https://github.com/Bottega-Alu/parlaconclaudio/releases).

## 1.0.22

### Look
- The Wave tray logo (default) fills its slot: longer, bolder ticks and a larger centre dot. It used to take about 88% of the icon. The exe icon follows.

## 1.0.21

### Keys
- Setting an API key opens a themed dialog that checks the key with the provider before saving it; a refused key is not saved, and offline you can save anyway.
- Configured keys stand out on the Links page with a status badge; hovering shows the first and last characters, where the key is stored and what the provider reports (OpenRouter: remaining budget, renewal, expiry, account credit).
- A key set in an environment variable is flagged, since it overrides the one in Credential Manager.
- "Get key" is now "Get new key"; the project link opens the current repository.

### Look
- Flat monochrome tray logo, still when idle and animated per state; seven styles, chosen in the app window (Appearance). The exe and Start menu icon use the same logo on a dark tile.

## 1.0.20

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
