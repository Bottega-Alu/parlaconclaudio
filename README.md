<p align="center">
  <h1 align="center">parlaconclaudio</h1>
  <p align="center"><strong>Talk to Claude with your voice. Dictate, listen, control.</strong></p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.9.9.0426-blueviolet?style=flat-square" alt="v0.9.9.0426">
  <img src="https://img.shields.io/badge/platform-Windows%2011-0078D4?style=flat-square&logo=windows" alt="Windows 11">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76B900?style=flat-square&logo=nvidia" alt="NVIDIA CUDA">
  <img src="https://img.shields.io/badge/cloud-Groq%20%7C%20Deepgram-FF6F00?style=flat-square" alt="Groq | Deepgram">
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

A voice bridge that started as a way to **talk to Claude Code** — and turned out to be a **better dictation tool than Windows built-in speech recognition**. Faster, more accurate, works in any language, and pastes into any window. Not just for developers.

> We built it to dictate prompts to Claude Code hands-free. Then we realized Whisper large-v3 on a local GPU crushes Windows Speech Recognition in accuracy, speed, and multilingual support. So now we use it for everything — emails, documents, chat, notes. It just works.

Two components:

1. **Voice Bridge (STT)** - Press `Ctrl+Alt+Space`, speak, and your words are transcribed and pasted into the active window. Works with local GPU (Whisper large-v3) or cloud providers (Groq, Deepgram) with automatic fallback. Works everywhere — Claude Code, browsers, Office, any app.

2. **TTS Notifications** - A Claude Code hook that announces task completions, permission requests, and status changes with natural voices (edge-tts). Walk away from the screen and still know what Claude is doing.

## STT Multi-Backend Orchestration

parlaconclaudio v0.9.9 introduces **multi-backend STT** with automatic fallback:

| Mode | Description |
|------|-------------|
| **Auto** (default) | Local GPU if available, else Groq, else Deepgram, else disabled |
| **Local** | NVIDIA GPU only (with optional cloud fallback) |
| **Cloud: Groq** | Free tier - 2000 req/day, no credit card required |
| **Cloud: Deepgram** | Nova-3 model, $200 free credit |

**No GPU? No problem.** Get a free Groq API key at [console.groq.com/keys](https://console.groq.com/keys) and you're ready.

API keys are stored securely: environment variables > OS keyring > JSON config.

## Architecture

```
Voice Bridge (STT)
  Ctrl+Alt+Space -> Microphone -> STT Engine (auto) -> Clipboard + Ctrl+V -> Terminal
                                     |
                                     +-- Local: Whisper large-v3 (CUDA float16)
                                     +-- Cloud: Groq (whisper-large-v3)
                                     +-- Cloud: Deepgram (Nova-3)

TTS Notifications
  Claude Code Hook -> notify-tts.py -> Chime + edge-tts voice announcement
```

## Prerequisites

- Windows 10/11
- Python 3.11+
- FFmpeg (`ffplay` in PATH)
- Claude Code CLI
- **One of:** NVIDIA GPU with CUDA **or** free Groq/Deepgram API key

## Installation

```bash
git clone https://github.com/Bottega-Alu/parlaconclaudio.git
cd parlaconclaudio

python -m venv venv
.\venv\Scripts\activate

# Core dependencies
pip install faster-whisper pynput pyperclip pyaudio pystray Pillow pywin32 edge-tts

# GPU support (optional - for local Whisper)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128

# Secure key storage (optional)
pip install keyring
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

## How to Use

1. **Launch** `VoiceBridge.bat` — the Rorschach sphere appears in the system tray
2. **Press `Ctrl+Alt+Space`** to start recording (sphere turns red, you hear a beep)
3. **Speak** your message
4. **Press `Ctrl+Alt+Space` again** to stop recording (second beep)
5. Your words are transcribed and **automatically pasted** into the active window (third beep)

> **Hotkey:** `Ctrl+Alt+Space` (toggle mode — press once to start, once to stop)
>
> The text goes to clipboard and is pasted via `Ctrl+V`. In Claude Code CLI, you can also right-click to paste.

## Tray Menu

Right-click the animated Rorschach sphere in the system tray:

```
✨ Voice Bridge v0.9.9.0426 ✨
────────────────────────────
🧠 Local GPU  ·  🇮🇹 Italiano  ·  🎤 Default     <- always visible status
────────────────────────────
🔔 Mode [full | semi-silent | silent]
🔊 Volume [25% - 300%]
🎵 Sound Pack
🎧 Preview
────────────────────────────
⚙️ Settings & Info
  ├── 🧠 STT Engine [Auto → Local GPU]
  │     ├── Mode selector (Auto/Local/Groq/Deepgram)
  │     ├── 📊 Status (GPU ✅/⏳/❌, API keys)
  │     ├── 🔑 Set API keys (masked input)
  │     └── 🔗 Test Connection
  ├── 🎤 Microphone selector
  ├── 🎙️ TTS Voice (8 presets + full browser)
  ├── 🌐 STT Language
  ├── 🧹 Purge VRAM
  └── 🔗 Links (GitHub, Groq console, Deepgram console)
────────────────────────────
🚪 Exit
```

The sphere animates based on state:
- **Rainbow drift** - idle, ready
- **Yellow/amber pulse** - loading STT engine
- **Red/magenta pulse** - recording
- **Golden shimmer** - transcribing

## Tip: Name your terminals

When running multiple Claude Code terminals in parallel, start each session with:

```
You are "Frontend" terminal. You work on UI components.
```

The TTS voice announces the project name, but naming each terminal helps you recognize **which agent is speaking**.

## Sound Pack System

The TTS system uses **data-driven sound packs** with semantic event mapping.

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

Each pack lives in `~/.claude/cache/tts/sounds/<pack-name>/` with a `manifest.json` mapping sounds to 7 semantic events: `task_done`, `stop`, `permission`, `question`, `idle`, `auth`, `default`.

Switch pack from the tray menu or edit `~/.claude/cache/tts/tts_config.json`.

### Create your own pack

1. Create a folder in `~/.claude/cache/tts/sounds/my-pack/`
2. Add MP3 files (1-5 seconds ideal)
3. Create `manifest.json` with chime mappings
4. Select from tray - zero code changes needed

## Project Structure

```
src/voice_bridge/          # STT Bridge
  bridge.py                # State machine: IDLE -> RECORDING -> TRANSCRIBING -> OUTPUT
  transcriber.py           # Multi-backend STT orchestrator (decision tree)
  config.py                # Configuration (stt_mode, auto_fallback)
  audio_recorder.py        # Microphone capture with device selection
  tray_icon.py             # Animated tray icon + settings cruscotto
  hotkey_listener.py       # Ctrl+Alt+Space hotkey
  output_handler.py        # Clipboard + Win32 paste
  sounds.py                # Audio feedback

src/core/stt_engine/       # STT Engines
  base.py                  # STTEngine ABC, NullSTTEngine, TranscriptionResult
  whisper_rtx.py           # Local GPU: FasterWhisper on CUDA
  groq_stt.py              # Cloud: Groq (whisper-large-v3)
  deepgram_stt.py          # Cloud: Deepgram (Nova-3)
  key_manager.py           # Secure API key storage (env > keyring > JSON)

src/core/audio_capture/    # Audio Capture
  drivers/portaudio_driver.py

scripts/
  notify-tts.py            # Claude Code TTS hook
  download_packs.py        # Sound pack downloader
  generate_manifests.py    # Manifest generator
```

---

# Italiano

## Cos'e parlaconclaudio?

Un bridge vocale nato per **parlare con Claude Code** — e che si e' rivelato un **sistema di dettatura migliore di quello integrato in Windows**. Piu' veloce, piu' preciso, multilingua, e incolla in qualsiasi finestra. Non solo per sviluppatori.

> L'abbiamo costruito per dettare prompt a Claude Code a mani libere. Poi ci siamo resi conto che Whisper large-v3 su GPU locale distrugge il riconoscimento vocale di Windows in precisione, velocita' e supporto multilingua. Ora lo usiamo per tutto — email, documenti, chat, appunti. Funziona e basta.

Due componenti:

1. **Voice Bridge (STT)** - Premi `Ctrl+Alt+Spazio`, parla, e le tue parole vengono trascritte e incollate nella finestra attiva. Funziona con GPU locale (Whisper large-v3) o provider cloud (Groq, Deepgram) con fallback automatico. Funziona ovunque — Claude Code, browser, Office, qualsiasi app.

2. **Notifiche TTS** - Un hook di Claude Code che annuncia il completamento dei task, richieste di permesso e cambi di stato con voci naturali (edge-tts).

## Orchestrazione STT Multi-Backend

La v0.9.9 introduce **STT multi-backend** con fallback automatico:

| Modalita | Descrizione |
|----------|-------------|
| **Auto** (default) | GPU locale se disponibile, altrimenti Groq, Deepgram, o disabilitato |
| **Local** | Solo GPU NVIDIA (con fallback cloud opzionale) |
| **Cloud: Groq** | Free tier - 2000 req/giorno, nessuna carta di credito |
| **Cloud: Deepgram** | Modello Nova-3, $200 di credito gratis |

**Niente GPU? Nessun problema.** Ottieni una API key Groq gratuita su [console.groq.com/keys](https://console.groq.com/keys) e sei pronto.

Le API key sono gestite in modo sicuro: variabili d'ambiente > keyring OS > config JSON.

## Prerequisiti

- Windows 10/11
- Python 3.11+
- FFmpeg (`ffplay` nel PATH)
- Claude Code CLI
- **Uno tra:** GPU NVIDIA con CUDA **oppure** API key Groq/Deepgram gratuita

## Installazione

```bash
git clone https://github.com/Bottega-Alu/parlaconclaudio.git
cd parlaconclaudio

python -m venv venv
.\venv\Scripts\activate

# Dipendenze principali
pip install faster-whisper pynput pyperclip pyaudio pystray Pillow pywin32 edge-tts

# Supporto GPU (opzionale - per Whisper locale)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128

# Storage sicuro chiavi (opzionale)
pip install keyring
```

## Avvio

```bash
.\VoiceBridge.bat
# oppure
.\venv\Scripts\python.exe -m src.voice_bridge
```

## Come si Usa

1. **Avvia** `VoiceBridge.bat` — la sfera Rorschach appare nel system tray
2. **Premi `Ctrl+Alt+Spazio`** per iniziare la registrazione (sfera rossa, beep)
3. **Parla** il tuo messaggio
4. **Premi di nuovo `Ctrl+Alt+Spazio`** per fermare la registrazione (secondo beep)
5. Le tue parole vengono trascritte e **incollate automaticamente** nella finestra attiva (terzo beep)

> **Tasto rapido:** `Ctrl+Alt+Spazio` (modalita toggle — premi una volta per iniziare, una per fermare)
>
> Il testo va in clipboard e viene incollato con `Ctrl+V`. In Claude Code CLI puoi anche fare click destro per incollare.

## Menu Tray

Click destro sulla sfera Rorschach nel system tray:

```
✨ Voice Bridge v0.9.9.0426 ✨
────────────────────────────
🧠 Local GPU  ·  🇮🇹 Italiano  ·  🎤 Default     <- barra stato sempre visibile
────────────────────────────
🔔 Mode [full | semi-silent | silent]
🔊 Volume [25% - 300%]
🎵 Sound Pack
🎧 Preview
────────────────────────────
⚙️ Settings & Info
  ├── 🧠 STT Engine (cruscotto con stato GPU/cloud)
  ├── 🎤 Selezione microfono
  ├── 🎙️ Voce TTS (8 preset + browser completo)
  ├── 🌐 Lingua STT
  ├── 🧹 Purge VRAM
  └── 🔗 Link (GitHub, Groq, Deepgram)
```

La sfera si anima in base allo stato: rainbow (idle), giallo/ambra pulsante (loading), rosso (registrazione), dorato (trascrizione).

## Sound Pack

8 pack disponibili con 188 suoni: `r2d2`, `south-park`, `south-park-ita`, `american-dad`, `star-wars`, `dune`, `maccio-capatonda`, `horror-zombie`.

Cambia pack dal menu tray o modifica `~/.claude/cache/tts/tts_config.json`.

### Creare un pack personalizzato

1. Crea una cartella in `~/.claude/cache/tts/sounds/mio-pack/`
2. Aggiungi file MP3 (clip brevi, 1-5 secondi ideali)
3. Crea `manifest.json` con il mapping dei chime
4. Seleziona dal menu tray - zero codice da modificare

## Tip: Dai un nome ai terminali

Quando usi piu terminali Claude Code in parallelo, inizia ogni sessione con:

```
Sei il terminale "Frontend". Ti occupi dei componenti UI.
```

La voce guida annuncia il nome del progetto, ma dare un nome a ogni terminale ti aiuta a riconoscere **quale agente sta parlando**.

---

# Portugues BR

## O que e parlaconclaudio?

Um bridge vocal que comecou como uma forma de **falar com o Claude Code** — e se revelou uma **ferramenta de ditado melhor que o reconhecimento de voz do Windows**. Mais rapido, mais preciso, funciona em qualquer idioma, e cola em qualquer janela. Nao e so para desenvolvedores.

> Construimos para ditar prompts ao Claude Code sem as maos. Depois percebemos que o Whisper large-v3 na GPU local esmaga o reconhecimento de voz do Windows em precisao, velocidade e suporte multilingual. Agora usamos para tudo — emails, documentos, chat, anotacoes. Simplesmente funciona.

Dois componentes:

1. **Voice Bridge (STT)** - Pressione `Ctrl+Alt+Espaco`, fale, e suas palavras sao transcritas e coladas na janela ativa. Funciona com GPU local (Whisper large-v3) ou provedores cloud (Groq, Deepgram) com fallback automatico. Funciona em qualquer lugar — Claude Code, navegadores, Office, qualquer app.

2. **Notificacoes TTS** - Um hook do Claude Code que anuncia conclusoes de tarefas, pedidos de permissao e mudancas de status com vozes naturais (edge-tts).

## Orquestracao STT Multi-Backend

A v0.9.9 introduz **STT multi-backend** com fallback automatico:

| Modo | Descricao |
|------|-----------|
| **Auto** (padrao) | GPU local se disponivel, depois Groq, Deepgram, ou desativado |
| **Local** | Somente GPU NVIDIA (com fallback cloud opcional) |
| **Cloud: Groq** | Free tier - 2000 req/dia, sem cartao de credito |
| **Cloud: Deepgram** | Modelo Nova-3, $200 de credito gratis |

**Sem GPU? Sem problema.** Pegue uma API key Groq gratis em [console.groq.com/keys](https://console.groq.com/keys).

As API keys sao armazenadas com seguranca: variaveis de ambiente > keyring do OS > config JSON.

## Pre-requisitos

- Windows 10/11
- Python 3.11+
- FFmpeg (`ffplay` no PATH)
- Claude Code CLI
- **Um dos:** GPU NVIDIA com CUDA **ou** API key Groq/Deepgram gratis

## Instalacao

```bash
git clone https://github.com/Bottega-Alu/parlaconclaudio.git
cd parlaconclaudio

python -m venv venv
.\venv\Scripts\activate

# Dependencias principais
pip install faster-whisper pynput pyperclip pyaudio pystray Pillow pywin32 edge-tts

# Suporte GPU (opcional - para Whisper local)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128

# Armazenamento seguro de chaves (opcional)
pip install keyring
```

## Executar

```bash
.\VoiceBridge.bat
# ou
.\venv\Scripts\python.exe -m src.voice_bridge
```

## Como Usar

1. **Execute** `VoiceBridge.bat` — a esfera Rorschach aparece no system tray
2. **Pressione `Ctrl+Alt+Espaco`** para comecar a gravar (esfera vermelha, beep)
3. **Fale** sua mensagem
4. **Pressione `Ctrl+Alt+Espaco` novamente** para parar a gravacao (segundo beep)
5. Suas palavras sao transcritas e **coladas automaticamente** na janela ativa (terceiro beep)

> **Tecla de atalho:** `Ctrl+Alt+Espaco` (modo toggle — pressione uma vez para iniciar, uma para parar)
>
> O texto vai para a area de transferencia e e colado com `Ctrl+V`. No Claude Code CLI voce tambem pode clicar com o botao direito para colar.

## Menu Tray

Clique direito na esfera Rorschach no system tray:

```
✨ Voice Bridge v0.9.9.0426 ✨
────────────────────────────
🧠 Local GPU  ·  🇮🇹 Italiano  ·  🎤 Default     <- barra de status sempre visivel
────────────────────────────
🔔 Mode [full | semi-silent | silent]
🔊 Volume [25% - 300%]
🎵 Sound Pack
🎧 Preview
────────────────────────────
⚙️ Settings & Info
  ├── 🧠 STT Engine (painel com status GPU/cloud)
  ├── 🎤 Selecao de microfone
  ├── 🎙️ Voz TTS (8 presets + navegador completo)
  ├── 🌐 Idioma STT
  ├── 🧹 Purge VRAM
  └── 🔗 Links (GitHub, Groq, Deepgram)
```

A esfera anima conforme o estado: arco-iris (idle), amarelo/ambar pulsante (carregando), vermelho (gravando), dourado (transcrevendo).

## Sound Packs

8 packs disponiveis com 188 sons: `r2d2`, `south-park`, `south-park-ita`, `american-dad`, `star-wars`, `dune`, `maccio-capatonda`, `horror-zombie`.

Troque o pack pelo menu tray ou edite `~/.claude/cache/tts/tts_config.json`.

### Criar seu proprio pack

1. Crie uma pasta em `~/.claude/cache/tts/sounds/meu-pack/`
2. Adicione arquivos MP3 (clips curtos, 1-5 segundos ideal)
3. Crie `manifest.json` com mapeamento de chimes
4. Selecione pelo menu tray - zero codigo para mudar

## Dica: Nomeie seus terminais

Quando usar varios terminais Claude Code em paralelo, comece cada sessao com:

```
Voce e o terminal "Frontend". Voce trabalha nos componentes de UI.
```

A voz guia anuncia o nome do projeto, mas nomear cada terminal ajuda a reconhecer **qual agente esta falando**.

---

## License

MIT License - see [LICENSE](LICENSE) for details.

Built with **[Claude Code](https://claude.ai/claude-code)** by [Anthropic](https://anthropic.com).
