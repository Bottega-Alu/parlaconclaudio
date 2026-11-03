# Changelog

Detailed notes for each release: [`docs/releases/`](docs/releases/) and the [GitHub releases](https://github.com/Bottega-Alu/parlaconclaudio/releases).

## 1.1.1

### Microphone
- The microphone list shows each microphone Windows has active once, with its full name (as in Windows Settings > Sound). It used to show every input once per Windows audio system plus unplugged devices and Bluetooth hands-free profiles: 11 entries on a PC with one microphone.
- The choice is saved by name instead of by position in the list, which changed whenever a device was plugged in or out (and could point to a device that is not a microphone). A saved microphone that is unplugged stays in the list as "Not connected" and the Windows default is used meanwhile. A choice saved by an older version is reset to the Windows default once.

## 1.1.0

### Quick controls
- The quick controls (left click on the tray icon) are drawn on a single canvas instead of ~300 native widgets: page switches, theme and language changes appear in one step, with no screenshot curtain. Measured with real clicks: back from a list 7 visible states and 1 flash -> 2 states, 0 flashes; lists open in 172-197 ms instead of 266-784 ms; Theme tile 727 ms -> 327 ms.
- Keyboard: Tab / Shift+Tab move the focus, arrows move in lists and change the volume sliders, Enter / Space activate; the focus ring appears only after keyboard use.
- Margins are symmetric at every scale (the native shadow frame takes a few pixels of the window).

### API keys
- "Check connection" no longer opens a Windows message box (and its system sound): the key badges show "Checking..." and the result appears on the card, green or red.

## 1.0.23

### Look
- New Equalizer tray logo, now the default: the bars of the README banner pulse from the centre while you dictate, a light sweeps back and forth while transcribing. The exe icon uses it too. A style you already picked stays.
- The app window is organised by topic: Home for daily use; agent alerts (Claude Code, Codex) in Voice alerts; text correction in Dictation; all keys in API keys; theme, language, tray icon and start with Windows in Personalize.
- Readable social preview card for links shared in chats.

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
