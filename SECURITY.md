# Security policy

## Supported versions

Only the [latest release](https://github.com/Bottega-Alu/parlaconclaudio/releases/latest) receives security fixes. Update before reporting.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private report: **Security → Report a vulnerability** on this repository. Include the app version, Windows version, steps to reproduce and the impact you expect. You will get an answer within 7 days; fixes ship in the next release and are credited in its notes unless you prefer otherwise.

Never include real API keys, transcripts or unredacted configuration files in a report.

## What the app does with your data

| Data | Where it goes |
|---|---|
| Microphone audio | Stays on the PC with local recognition (GPU or CPU). Sent to Groq or Deepgram only if you choose or configure that engine. |
| Transcribed text | Sent to OpenRouter only when LLM cleanup is on and you set an OpenRouter key. Copied for pasting without entering Windows clipboard history or cloud clipboard. Kept in `%LOCALAPPDATA%\ParlaConClaudio\history.jsonl` (last 100) unless you switch history off; uninstall deletes it. |
| Agent announcements | The spoken text (project name, status, tool description) goes to Microsoft's online Edge voices through `edge-tts`. |
| API keys | Windows Credential Manager. Keys found in older plaintext config are moved there at start-up. |
| Network checks | Once a day the app downloads the sound pack catalogue from this repository's `addons` release (can be switched off). Packs are verified by size and SHA-256 before use. |

Nothing listens on a network port. The app installs per user without administrator rights; uninstalling removes the Claude Code / Codex hooks that run from the app folder.
