<p align="center">
  <h1 align="center">parlaconclaudio</h1>
  <p align="center"><strong>Talk to Claude with your voice. Dictate, listen, control.</strong></p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%2011-0078D4?style=flat-square&logo=windows" alt="Windows 11">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76B900?style=flat-square&logo=nvidia" alt="NVIDIA CUDA">
  <img src="https://img.shields.io/badge/STT-Whisper%20large--v3-FF6F00?style=flat-square" alt="Whisper large-v3">
  <img src="https://img.shields.io/badge/TTS-edge--tts-00A4EF?style=flat-square&logo=microsoft" alt="edge-tts">
  <img src="https://img.shields.io/badge/built%20with-Claude%20Code-7C3AED?style=flat-square" alt="Built with Claude Code">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License">
</p>

---

**[English](#english)** | **[Italiano](#italiano)** | **[Portugues BR](#portugues-br)**

---

# English

## What is parlaconclaudio?

A local voice bridge for **Claude Code** on Windows. Two components:

1. **Voice Bridge (STT)** - Press `Ctrl+Alt+Space`, speak, and your words are transcribed locally on your GPU (Whisper large-v3) and pasted into the active terminal. No cloud, no latency.

2. **TTS Notifications** - A Claude Code hook that announces task completions, permission requests, and status changes with natural voices (edge-tts). Walk away from the screen and still know what Claude is doing.

## Architecture

```
Voice Bridge (STT)
  Ctrl+Alt+Space -> Microphone -> Whisper (GPU) -> Clipboard + Ctrl+V -> Terminal

TTS Notifications
  Claude Code Hook -> notify-tts.py -> Chime + edge-tts voice announcement
```

## Prerequisites

- Windows 10/11
- NVIDIA GPU with CUDA support
- Python 3.11+
- FFmpeg (`ffplay` in PATH)
- Claude Code CLI

## Installation

```bash
git clone https://github.com/fra-itc/parlaconclaudio.git
cd parlaconclaudio

python -m venv venv
.\venv\Scripts\activate

# Core dependencies
pip install faster-whisper pynput pyperclip pyaudio pystray Pillow pywin32

# CUDA support
pip install nvidia-cudnn-cu12 nvidia-cublas-cu12

# TTS
pip install edge-tts
```

## Configure Claude Code Hooks

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [{ "hooks": [{ "type": "command", "command": "python C:/PROJECTS/parlaconclaudio/scripts/notify-tts.py", "timeout": 10 }] }],
    "Notification": [{ "hooks": [{ "type": "command", "command": "python C:/PROJECTS/parlaconclaudio/scripts/notify-tts.py", "timeout": 10 }] }]
  }
}
```

## Launch

```bash
# Option 1: Batch file
.\VoiceBridge.bat

# Option 2: Direct
.\venv\Scripts\python.exe -m src.voice_bridge
```

## Tip: Name your terminals

When running multiple Claude Code terminals in parallel, start each session with a first prompt that declares the terminal's role, e.g.:

```
You are "Frontend" terminal. You work on UI components.
```

The TTS voice guide announces the project name from `cwd`, but naming each terminal helps you instantly recognize **which agent is speaking** when you hear a notification.

## Sound Pack System

The TTS notification system uses **data-driven sound packs** with semantic event mapping. Each pack is a folder with MP3 files and a `manifest.json` that maps sounds to events.

### Available Packs (188 sounds)

| Pack | Sounds | Description |
|------|--------|-------------|
| `r2d2` | 22 | R2-D2 semantic chimes - robot beeps and boops |
| `south-park` | 28 | Cartman, Kenny, Butters - English |
| `south-park-ita` | 25 | Cartman doppiaggio italiano, Trombino & Pompadour |
| `american-dad` | 14 | Roger, Stan Smith & family |
| `star-wars` | 17 | Lightsabers, Vader, Palpatine, Chewbacca, Duel of Fates |
| `dune` | 48 | Bene Gesserit Voice, sandworms, shields, Zimmer score |
| `maccio-capatonda` | 15 | Italiano Medio, SCOPAREEEEE, balletto |
| `horror-zombie` | 19 | Zombie grunts, horror stingers, groans |

### How it works

Each pack lives in `~/.claude/cache/tts/sounds/<pack-name>/` with a `manifest.json`:

```json
{
  "pack": "star-wars",
  "version": "1.0",
  "description": "Star Wars - lightsabers, Vader, Palpatine",
  "chimes": {
    "task_done": ["sw-hello-there.mp3", "sw-do-it.mp3"],
    "stop": ["sw-imperial-march.mp3", "sw-duel-of-fates.mp3"],
    "permission": ["sw-i-am-your-father.mp3", "sw-unlimited-power.mp3"],
    "question": ["sw-its-a-trap.mp3", "sw-tusken-raider.mp3"],
    "idle": ["sw-chewbacca-roar.mp3", "sw-battle-alarm.mp3"],
    "auth": ["sw-lightsaber-sith.mp3", "sw-order-66.mp3"],
    "default": ["sw-tie-blaster.mp3", "sw-seismic-charge.mp3"]
  },
  "sounds": {
    "sw-hello-there.mp3": {
      "label": "Hello There! - Obi-Wan",
      "duration_ms": 1800,
      "event": "task_done"
    }
  }
}
```

The 7 semantic events:
- **task_done** - A task completed successfully
- **stop** - Claude finished all work
- **permission** - Claude needs your approval
- **question** - Claude is asking you something
- **idle** - Waiting for your input
- **auth** - Authentication completed
- **default** - General notification

### Switch pack

Edit `~/.claude/cache/tts/tts_config.json`:
```json
{"sound_pack": "dune"}
```
Or use the system tray icon menu.

### Create your own pack

1. Create a folder in `~/.claude/cache/tts/sounds/my-pack/`
2. Add MP3 files (short clips, 1-5 seconds ideal for notifications)
3. Create `manifest.json` with chime mappings (see example above)
4. Select the pack from tray icon or config - zero code changes needed

### Download packs

```bash
python scripts/download_packs.py --pack all          # Download South Park + Horror + American Dad
python scripts/generate_manifests.py                  # Regenerate manifests with ffprobe durations
```

### Pipeline for creating packs from YouTube

```
YouTube video
  -> yt-dlp (download audio as MP3)
  -> Whisper large-v3 (transcribe with timestamps)
  -> ffmpeg silencedetect (find segment boundaries)
  -> ffmpeg (cut individual clips with fade in/out)
  -> manifest.json (semantic event mapping)
  -> notify-tts.py auto-discovers the new pack
```

## Project Structure

```
src/voice_bridge/       # STT Bridge
  bridge.py             # State machine: IDLE -> RECORDING -> TRANSCRIBING -> OUTPUT
  config.py             # Configuration
  hotkey_listener.py    # Ctrl+Alt+Space hotkey
  audio_recorder.py     # Microphone capture (PortAudio)
  transcriber.py        # Whisper wrapper
  output_handler.py     # Clipboard + Win32 paste
  sounds.py             # Audio feedback
  tray_icon.py          # Animated system tray icon + settings menu

src/core/
  stt_engine/
    whisper_rtx.py      # FasterWhisper on CUDA
    model_setup.py      # Model download/cache
  audio_capture/
    drivers/
      portaudio_driver.py

scripts/
  notify-tts.py         # Claude Code TTS hook - data-driven chime system
  download_packs.py     # Auto-download sound packs (South Park, Horror, American Dad)
  generate_manifests.py # Generate manifest.json for packs with ffprobe metadata

~/.claude/cache/tts/
  tts_config.json       # Master config (voice, pack, volume, mode)
  sounds/               # Sound packs (r2d2, south-park, dune, star-wars, ...)
  dynamic/              # Cached TTS voice announcements
```

---

# Italiano

## Cos'e parlaconclaudio?

Un bridge vocale locale per **Claude Code** su Windows. Due componenti:

1. **Voice Bridge (STT)** - Premi `Ctrl+Alt+Space`, parla, e le tue parole vengono trascritte localmente sulla GPU (Whisper large-v3) e incollate nel terminale attivo. Nessun cloud, nessuna latenza.

2. **Notifiche TTS** - Un hook di Claude Code che annuncia il completamento dei task, richieste di permesso e cambi di stato con voci naturali (edge-tts). Puoi allontanarti dallo schermo e sapere comunque cosa sta facendo Claude.

## Prerequisiti

- Windows 10/11
- GPU NVIDIA con supporto CUDA
- Python 3.11+
- FFmpeg (`ffplay` nel PATH)
- Claude Code CLI

## Installazione

```bash
git clone https://github.com/fra-itc/parlaconclaudio.git
cd parlaconclaudio

python -m venv venv
.\venv\Scripts\activate

pip install faster-whisper pynput pyperclip pyaudio pystray Pillow pywin32
pip install nvidia-cudnn-cu12 nvidia-cublas-cu12
pip install edge-tts
```

## Avvio

```bash
.\VoiceBridge.bat
# oppure
.\venv\Scripts\python.exe -m src.voice_bridge
```

## Sistema Sound Pack

Il sistema di notifiche TTS usa **sound pack data-driven** con mapping semantico degli eventi. Ogni pack e' una cartella con file MP3 e un `manifest.json` che mappa i suoni agli eventi.

### Pack disponibili (188 suoni)

| Pack | Suoni | Descrizione |
|------|-------|-------------|
| `r2d2` | 22 | Chime semantici R2-D2 |
| `south-park` | 28 | Cartman, Kenny, Butters - Inglese |
| `south-park-ita` | 25 | Cartman doppiaggio italiano, Trombino & Pompadour |
| `american-dad` | 14 | Roger, Stan Smith & family |
| `star-wars` | 17 | Spade laser, Vader, Palpatine, Chewbacca |
| `dune` | 48 | Voce Bene Gesserit, vermi, scudi, Zimmer |
| `maccio-capatonda` | 15 | Italiano Medio, SCOPAREEEEE, balletto |
| `horror-zombie` | 19 | Zombie, horror stinger |

### Cambiare pack

Modifica `~/.claude/cache/tts/tts_config.json`:
```json
{"sound_pack": "dune"}
```
Oppure dal menu dell'icona nel system tray.

### Creare un pack personalizzato

1. Crea una cartella in `~/.claude/cache/tts/sounds/mio-pack/`
2. Aggiungi file MP3 (clip brevi, 1-5 secondi ideali per notifiche)
3. Crea `manifest.json` con il mapping dei chime (vedi sezione English per il formato)
4. Seleziona il pack dal tray icon o dal config - zero codice da modificare

### Pipeline per creare pack da YouTube

```
Video YouTube -> yt-dlp -> Whisper large-v3 (trascrizione) -> ffmpeg (taglio clip) -> manifest.json
```

## Tip: Dai un nome ai terminali

Quando usi piu' terminali Claude Code in parallelo, inizia ogni sessione con un primo prompt che dichiara il ruolo del terminale, es.:

```
Sei il terminale "Frontend". Ti occupi dei componenti UI.
```

La voce guida annuncia il nome del progetto dalla `cwd`, ma dare un nome a ogni terminale ti aiuta a riconoscere immediatamente **quale agente sta parlando** quando senti una notifica.

---

# Portugues BR

## O que e parlaconclaudio?

Um bridge vocal local para o **Claude Code** no Windows. Dois componentes:

1. **Voice Bridge (STT)** - Pressione `Ctrl+Alt+Space`, fale, e suas palavras sao transcritas localmente na GPU (Whisper large-v3) e coladas no terminal ativo. Sem nuvem, sem latencia.

2. **Notificacoes TTS** - Um hook do Claude Code que anuncia conclusoes de tarefas, pedidos de permissao e mudancas de status com vozes naturais (edge-tts).

## Pre-requisitos

- Windows 10/11
- GPU NVIDIA com suporte CUDA
- Python 3.11+
- FFmpeg (`ffplay` no PATH)
- Claude Code CLI

## Instalacao

```bash
git clone https://github.com/fra-itc/parlaconclaudio.git
cd parlaconclaudio

python -m venv venv
.\venv\Scripts\activate

pip install faster-whisper pynput pyperclip pyaudio pystray Pillow pywin32
pip install nvidia-cudnn-cu12 nvidia-cublas-cu12
pip install edge-tts
```

## Executar

```bash
.\VoiceBridge.bat
# ou
.\venv\Scripts\python.exe -m src.voice_bridge
```

## Sistema de Sound Packs

O sistema de notificacoes TTS usa **sound packs data-driven** com mapeamento semantico de eventos. Cada pack e' uma pasta com arquivos MP3 e um `manifest.json`.

8 packs disponiveis com 188 sons: `r2d2`, `south-park`, `south-park-ita`, `american-dad`, `star-wars`, `dune`, `maccio-capatonda`, `horror-zombie`.

Trocar pack: edite `~/.claude/cache/tts/tts_config.json` ou use o menu do tray icon.

## Dica: Nomeie seus terminais

Quando usar varios terminais Claude Code em paralelo, comece cada sessao com um primeiro prompt que declare o papel do terminal, ex.:

```
Voce e o terminal "Frontend". Voce trabalha nos componentes de UI.
```

A voz guia anuncia o nome do projeto pelo `cwd`, mas nomear cada terminal ajuda a reconhecer imediatamente **qual agente esta falando** quando ouvir uma notificacao.

---

## License

MIT License - see [LICENSE](LICENSE) for details.

Built with **[Claude Code](https://claude.ai/claude-code)** by [Anthropic](https://anthropic.com).
