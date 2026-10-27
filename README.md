<p align="center">
  <h1 align="center">parlaconclaudio</h1>
  <p align="center"><strong>Professional voice dictation for Windows, with spoken status for Claude Code and Codex.</strong></p>
</p>

<p align="center">
  <a href="https://github.com/Bottega-Alu/parlaconclaudio/releases/latest"><img src="https://img.shields.io/github/v/release/Bottega-Alu/parlaconclaudio?style=flat-square&color=blueviolet&label=version" alt="Latest release"></a>
  <a href="https://github.com/Bottega-Alu/parlaconclaudio/releases/latest/download/parlaconclaudio-voice-bridge-Setup.exe"><img src="https://img.shields.io/badge/download-Windows%20installer-0078D4?style=flat-square&logo=windows" alt="Download Windows installer"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D4?style=flat-square&logo=windows" alt="Windows 10 | 11">
  <img src="https://img.shields.io/badge/STT-Whisper%20large--v3%20FP16-FF6F00?style=flat-square" alt="Whisper large-v3 FP16">
  <img src="https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76B900?style=flat-square&logo=nvidia" alt="NVIDIA CUDA">
  <img src="https://img.shields.io/badge/cloud-Groq%20%7C%20Deepgram%20%7C%20OpenRouter-FF6F00?style=flat-square" alt="Groq | Deepgram | OpenRouter">
  <img src="https://img.shields.io/badge/tests-440%20automated-2EA043?style=flat-square" alt="440 automated tests">
  <img src="https://img.shields.io/badge/install-per--user%2C%20no%20admin-555?style=flat-square" alt="Per-user install, no admin">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License">
</p>

---

**[English](#english)** | **[Italiano](#italiano)** | **[Português BR](#português-br)**

---

# English

## What it is

parlaconclaudio turns speech into text in **any Windows application**: press your dictation key, speak, and the text lands where you are typing (or on the clipboard, if you prefer). It started as a hands-free way to prompt Claude Code and grew into a general-purpose dictation tool that replaces Windows voice typing for email, documents, chat and code.

It also gives your coding agents a voice: **Claude Code and Codex announce** finished work, permission requests and questions, naming the project and the agent that is speaking, so you can step away from the screen without losing track.

## Why it is different

| | |
|---|---|
| **Local-first speech recognition** | OpenAI Whisper **large-v3** in **FP16 on CUDA** (faster-whisper / CTranslate2). In local mode your **audio never leaves the PC**. |
| **Deterministic fallback** | A fixed decision tree: local GPU → Groq → Deepgram. No GPU? A free Groq key is enough. |
| **Language-preserving LLM cleanup** | An optional pass fixes slips, punctuation and phonetically mangled technical terms (*"di tab"* → **GitHub**), grounded on **your glossary**, and never translates. If the network is down, you get the raw transcript: dictation **never blocks**. |
| **Engineered for failure** | Retry honouring `Retry-After`, circuit breaker, truncation guard, a supervisor that relaunches a crashed bridge (3 times in 5 minutes, 2/4/6 s back-off) and a single-instance mutex. |
| **Secrets handled properly** | API keys resolve from environment variables → Windows Credential Manager (keyring) → JSON, in that order. |
| **Non-destructive integration** | Agent hook files are parsed before writing, backed up with a timestamp and written atomically; a malformed file is reported and never overwritten. |
| **Native Windows 11 UI** | Fluent icons, system accent colour, live light/dark switching, per-monitor DPI awareness (sharp at 4K / 300%), rounded corners and native shadow. |
| **Your trigger, your way** | Any key combination, the **middle mouse button** or the **side buttons** (Back/Forward), toggle or hold-to-talk. Mouse triggers are swallowed system-wide so the click never reaches other apps. |
| **Quiet when idle** | Hidden windows do not poll devices or configuration and do not redraw. |
| **Tested and verifiable** | 440 automated tests; every release ships a SHA-256 checksum file. |

## Download

**[Download the latest Windows installer](https://github.com/Bottega-Alu/parlaconclaudio/releases/latest/download/parlaconclaudio-voice-bridge-Setup.exe)** (~23 MB), or choose a version on the **[Releases page](https://github.com/Bottega-Alu/parlaconclaudio/releases/latest)**. Checksums are in `SHA256SUMS.txt` on each release.

- Installs **per user, without admin rights** (x64). Upgrades install in place.
- On first launch a bootstrap creates the Python environment and downloads what your machine needs: the **GPU profile** on an NVIDIA PC (~2.3 GB including the Whisper model) or the **CPU/cloud profile** elsewhere (~0.3 GB).
- Optional: start at sign-in, and connect the voice hooks for Claude Code and Codex.

## Using it

1. Press **`Ctrl+Alt+Space`** (default) and speak; press it again to stop. A short tone marks start, stop and delivery.
2. The text is pasted into the active window, or only copied to the clipboard if you turn off **Auto paste**.
3. **Left click** the tray icon for the quick controls; **right click** for the app window.

The animated tray sphere shows the state: rainbow drift when ready, amber while the engine loads, red while recording, gold while transcribing.

### Quick controls (left click)

Laid out like Windows 11 Quick Settings: dictation, audio on/off, sound pack, voice announcements, microphone and recognition engine, separate **sound** and **voice** volume sliders, and shortcuts to audio-file transcription, settings and exit. Sound packs are chosen like Wi-Fi networks: expand a pack to **Listen** or **Use** it. It opens in about a tenth of a second and follows the Windows **system** light/dark mode, like Quick Settings.

### App window (right click)

Laid out like Windows 11 Settings:

- **Home**: status, engine in use, start dictation, transcribe audio files.
- **Dictation**: trigger (key combination or mouse button), toggle or hold, a **live test** area that shows the events it receives without dictating, microphone, engine with GPU status, language, auto paste, GPU memory.
- **Voice alerts**: mute, sound and voice volume, announcement mode, quick voices and all 44 voices in 8 languages, sound pack with preview.
- **Connections**: Claude Code and Codex voice hooks, start at sign-in, Groq and Deepgram keys (status, where to get them, connection test).
- **About**: version, documentation, exit.

### Audio files

*Transcribe audio file…* processes recordings in the background with the same engine and cleanup, and can add translations and a spoken dub (see the configuration keys below).

## Speech recognition engines

| Mode | Engine | Notes |
|---|---|---|
| **Automatic** (default) | Local GPU, then Groq, then Deepgram | Picks the first available |
| **Local GPU** | Whisper large-v3, FP16, CUDA | NVIDIA GPU required |
| **Cloud: Groq** | whisper-large-v3 | Free tier, no credit card |
| **Cloud: Deepgram** | Nova-3 | Free starting credit |

Dictation language: automatic detection, or fixed to Italian, English, Portuguese, Spanish, French, German or Japanese. Fixing it avoids misdetection on short phrases.

## LLM cleanup

After transcription an optional pass, through **[OpenRouter](https://openrouter.ai)**, corrects recognition slips, restores English technical terms heard phonetically and fixes punctuation, while **keeping the spoken language**. The model is `google/gemini-2.5-flash` with `qwen/qwen-2.5-7b-instruct` as fallback. Your terms in `stt_cleanup_glossary` are passed to the model as grounding: an LLM-first whitelist, not a hardcoded alias table. In local mode with cleanup on, the **text** (never the audio) is sent to OpenRouter; turn `enable_stt_cleanup` off for fully offline operation.

| `tts_config.json` key | Default | Purpose |
|---|---|---|
| `enable_stt_cleanup` | `true` | Master switch for the cleanup pass |
| `stt_cleanup_model` | `google/gemini-2.5-flash` | Primary model |
| `stt_cleanup_fallback` | `qwen/qwen-2.5-7b-instruct` | Fallback model |
| `stt_cleanup_glossary` | `[]` | Your domain terms, used as grounding |
| `transcribe_translations` | `true` | EN/IT/PT translations for audio files |
| `transcribe_tts_dub` | `true` | Spoken dub of the translations |
| `transcribe_extras_max_chars` | `2500` | Above this length: cleaned transcript only |

OpenRouter key: `OPENROUTER_API_KEY`, the keyring, or `stt_api_key_openrouter` in `tts_config.json`, in this order.

## Voice hooks for Claude Code and Codex

Turn them on in **App window → Connections**. The switches read the real files:

| Agent | File | Events |
|---|---|---|
| Claude Code | `%CLAUDE_CONFIG_DIR%` or `~/.claude/settings.json` | `Stop`, `TaskCompleted` (done), `Notification` (needs you), `PreToolUse` on `AskUserQuestion` (needs your choice) |
| Codex | `%CODEX_HOME%` or `~/.codex/hooks.json` | `Stop` (done), `PermissionRequest` (needs you) |

Connecting adds only the missing entries; disconnecting removes only the entries that run `notify-tts.py`. Each write is parsed first, backed up as `<file>.bak-<timestamp>` and written atomically. The `AskUserQuestion` hook is asynchronous and never answers or approves anything. Codex runs a new hook only after you trust it in `/hooks`; its `notify` key in `config.toml` is left untouched.

Announcements say who is speaking, for example *"Progetto explore-benchmark, Claudio ha bisogno del tuo aiuto"* (status phrases are in Italian; the voice is yours to choose). When several terminals work in parallel, give each session a name so you recognise the agent.

## Sound packs

Eight packs are included in the installer (R2-D2, South Park EN and IT, American Dad, Star Wars, Dune, Maccio Capatonda, Horror Zombie). Each pack is a folder in `~/.claude/cache/tts/sounds/<pack>/` with MP3 files and a `manifest.json` that maps sounds to seven events (`task_done`, `stop`, `permission`, `question`, `idle`, `auth`, `default`). Add a folder and it appears in the list, with no code changes.

## Reliability and operations

- **Single instance**: a second launch exits while a bridge is running (named mutex) and never kills the running one.
- **Supervisor**: the bridge runs as a child process and is relaunched after a crash, up to 3 times in 5 minutes, then the supervisor stops and logs `supervisor_gave_up`. *Exit* is never relaunched.
- **Start at sign-in**: a per-user Task Scheduler task (`parlaconclaudio-voice-bridge`), with no admin rights or time limit and restart on failure. If the task cannot be registered, an `HKCU\...\Run` entry is used instead. It is deliberately not a Windows service: services run in session 0, with no hotkeys, clipboard, tray or per-user microphone. Command line: `python -m src.voice_bridge.autostart enable|disable|status`.
- **Logs**: `voicebridge.log` in the installation folder; TTS errors in `~/.claude/cache/tts/notify-tts.log`.

## Build from source

Prerequisites: Windows 10/11, Python 3.11+, FFmpeg (`ffplay` on `PATH`), and an NVIDIA GPU with CUDA **or** a Groq/Deepgram key.

```bash
git clone https://github.com/Bottega-Alu/parlaconclaudio.git
cd parlaconclaudio
python -m venv venv
.\venv\Scripts\activate
pip install faster-whisper pynput pyperclip pyaudio pystray Pillow pywin32 "edge-tts>=7.3.1" keyring
pip install -e .[gpu]          # optional: local GPU (cuBLAS/cuDNN for CTranslate2, no torch)

.\VoiceBridge.bat              # no console window
.\venv\Scripts\python.exe -m src.voice_bridge    # with live log
.\venv\Scripts\python.exe -m pytest              # test suite
```

Installer: `scripts\build_exe.ps1 -Installer` (PyInstaller + Inno Setup 6).

## Project structure

```
src/voice_bridge/
  bridge.py              State machine: IDLE -> RECORDING -> TRANSCRIBING -> OUTPUT
  transcriber.py         Multi-backend STT orchestrator (decision tree)
  stt_cleanup.py         LLM cleanup: NullObject, retry, circuit breaker
  dictation_trigger.py   Key combination or mouse button, toggle or hold, live test
  hotkey_listener.py     Global keyboard listener
  output_handler.py      Clipboard, Win32 paste
  tray_icon.py           Animated tray icon and settings actions
  tray_flyout.py         Quick controls (Windows 11 Quick Settings style)
  control_panel.py       App window (Windows 11 Settings style)
  fluent.py              Fluent icons, theme, accent, slider and switch
  supervisor.py          Crash relaunch
  single_instance.py     Named mutex
  autostart.py           Task Scheduler / Run key
src/core/stt_engine/     Whisper (CUDA), Groq, Deepgram, NullSTTEngine, key manager
src/core/voice_hooks.py  Claude Code / Codex hook wiring
scripts/notify-tts.py    Voice announcements hook
installer/               Inno Setup script and bootstrap
```

---

# Italiano

## Cos'è

parlaconclaudio trasforma la voce in testo in **qualsiasi applicazione Windows**: premi il comando di dettatura, parla, e il testo arriva dove stai scrivendo (oppure negli appunti, se preferisci). È nato per dettare prompt a Claude Code a mani libere ed è diventato uno strumento di dettatura generale che sostituisce la dettatura di Windows per email, documenti, chat e codice.

Dà anche una voce ai tuoi agenti: **Claude Code e Codex annunciano** la fine dei lavori, le richieste di permesso e le domande, dicendo il progetto e l'agente che parla, così puoi allontanarti dallo schermo senza perdere il filo.

## Perché è diverso

| | |
|---|---|
| **Riconoscimento vocale local-first** | OpenAI Whisper **large-v3** in **FP16 su CUDA** (faster-whisper / CTranslate2). In modalità locale **l'audio non lascia mai il PC**. |
| **Fallback deterministico** | Un albero decisionale fisso: GPU locale → Groq → Deepgram. Niente GPU? Basta una chiave Groq gratuita. |
| **Pulizia LLM che preserva la lingua** | Un passaggio opzionale corregge errori, punteggiatura e termini tecnici storpiati foneticamente (*"di tab"* → **GitHub**), ancorato al **tuo glossario**, senza mai tradurre. Se la rete non c'è ricevi il testo grezzo: la dettatura **non si blocca mai**. |
| **Progettato per i guasti** | Retry che rispetta `Retry-After`, circuit breaker, controllo sulle risposte troncate, un supervisor che riavvia il bridge dopo un crash (3 volte in 5 minuti, attese di 2/4/6 s) e un mutex di istanza unica. |
| **Chiavi gestite correttamente** | Le chiavi API si leggono da variabili d'ambiente → Gestione credenziali di Windows (keyring) → JSON, in quest'ordine. |
| **Integrazione non distruttiva** | I file degli hook vengono letti e validati prima di scrivere, salvati con backup datato e scritti in modo atomico; un file malformato viene segnalato e mai sovrascritto. |
| **Interfaccia nativa Windows 11** | Icone Fluent, colore principale del sistema, tema chiaro/scuro seguito in tempo reale, DPI per monitor (nitida anche a 4K / 300%), angoli arrotondati e ombra nativa. |
| **Il comando che vuoi** | Qualsiasi combinazione di tasti, il **tasto centrale del mouse** o i **tasti laterali** (Indietro/Avanti), premi una volta o tieni premuto. I tasti del mouse vengono intercettati a livello di sistema: il clic non arriva alle altre app. |
| **Silenzioso a riposo** | Le finestre nascoste non interrogano dispositivi né configurazione e non ridisegnano. |
| **Testato e verificabile** | 440 test automatici; ogni release pubblica il file dei checksum SHA-256. |

## Download

**[Scarica l'ultimo installer per Windows](https://github.com/Bottega-Alu/parlaconclaudio/releases/latest/download/parlaconclaudio-voice-bridge-Setup.exe)** (~23 MB), oppure scegli una versione nella **[pagina Releases](https://github.com/Bottega-Alu/parlaconclaudio/releases/latest)**. I checksum sono in `SHA256SUMS.txt` di ogni release.

- Installazione **per utente, senza diritti di amministratore** (x64). Gli aggiornamenti si installano sopra la versione precedente.
- Al primo avvio un bootstrap crea l'ambiente Python e scarica ciò che serve alla tua macchina: **profilo GPU** su PC NVIDIA (~2,3 GB compreso il modello Whisper) o **profilo CPU/cloud** altrove (~0,3 GB).
- Opzionale: avvio all'accesso e collegamento degli avvisi vocali per Claude Code e Codex.

## Come si usa

1. Premi **`Ctrl+Alt+Spazio`** (predefinito) e parla; premi di nuovo per fermare. Un breve suono segna avvio, fine e consegna.
2. Il testo viene incollato nella finestra attiva, oppure solo copiato negli appunti se spegni **Incolla automaticamente**.
3. **Clic sinistro** sull'icona nella tray per i comandi rapidi; **clic destro** per la finestra dell'app.

La sfera animata nella tray mostra lo stato: arcobaleno quando è pronta, ambra mentre carica il motore, rosso mentre registra, oro mentre trascrive.

### Comandi rapidi (clic sinistro)

Come le Impostazioni rapide di Windows 11: dettatura, audio sì/no, pacchetto suoni, avvisi vocali, microfono e motore di riconoscimento, slider separati per volume **suoni** e **voci**, scorciatoie per trascrivere file audio, impostazioni ed Esci. I pacchetti suoni si scelgono come le reti Wi-Fi: espandi un pacchetto per **Ascoltarlo** o **Usarlo**. Si apre in circa un decimo di secondo e segue la modalità chiaro/scuro del **sistema**, come le Impostazioni rapide.

### Finestra dell'app (clic destro)

Organizzata come le Impostazioni di Windows 11:

- **Home**: stato, motore in uso, avvio dettatura, trascrizione di file audio.
- **Dettatura**: comando (combinazione di tasti o tasto del mouse), premi o tieni premuto, un'**area di prova** che mostra gli eventi ricevuti senza dettare, microfono, motore con stato della GPU, lingua, incolla automaticamente, memoria GPU.
- **Avvisi vocali**: audio sì/no, volume suoni e voci, modalità degli annunci, voci rapide e tutte le 44 voci in 8 lingue, pacchetto suoni con anteprima.
- **Collegamenti**: avvisi vocali di Claude Code e Codex, avvio all'accesso, chiavi Groq e Deepgram (stato, dove ottenerle, verifica connessione).
- **Informazioni**: versione, documentazione, Esci.

### File audio

*Trascrivi file audio…* elabora le registrazioni in background con lo stesso motore e la stessa pulizia, e può aggiungere traduzioni e un doppiaggio parlato (vedi le chiavi di configurazione qui sotto).

## Motori di riconoscimento

| Modalità | Motore | Note |
|---|---|---|
| **Automatico** (predefinito) | GPU locale, poi Groq, poi Deepgram | Usa il primo disponibile |
| **GPU locale** | Whisper large-v3, FP16, CUDA | Serve una GPU NVIDIA |
| **Cloud: Groq** | whisper-large-v3 | Piano gratuito, senza carta di credito |
| **Cloud: Deepgram** | Nova-3 | Credito iniziale gratuito |

Lingua di dettatura: rilevamento automatico, oppure fissata su italiano, inglese, portoghese, spagnolo, francese, tedesco o giapponese. Fissarla evita errori di riconoscimento sulle frasi brevi.

## Pulizia LLM

Dopo la trascrizione un passaggio opzionale, tramite **[OpenRouter](https://openrouter.ai)**, corregge gli errori di riconoscimento, ripristina i termini tecnici inglesi sentiti foneticamente e sistema la punteggiatura, **mantenendo la lingua parlata**. Il modello è `google/gemini-2.5-flash` con `qwen/qwen-2.5-7b-instruct` come riserva. I tuoi termini in `stt_cleanup_glossary` vengono passati al modello come riferimento: una whitelist LLM-first, non una tabella di alias scritta nel codice. In modalità locale con la pulizia attiva viene inviato a OpenRouter il **testo** (mai l'audio); spegni `enable_stt_cleanup` per lavorare completamente offline.

| Chiave in `tts_config.json` | Predefinito | Scopo |
|---|---|---|
| `enable_stt_cleanup` | `true` | Attiva la pulizia |
| `stt_cleanup_model` | `google/gemini-2.5-flash` | Modello principale |
| `stt_cleanup_fallback` | `qwen/qwen-2.5-7b-instruct` | Modello di riserva |
| `stt_cleanup_glossary` | `[]` | I tuoi termini, usati come riferimento |
| `transcribe_translations` | `true` | Traduzioni EN/IT/PT per i file audio |
| `transcribe_tts_dub` | `true` | Doppiaggio parlato delle traduzioni |
| `transcribe_extras_max_chars` | `2500` | Oltre questa lunghezza: solo trascrizione pulita |

Chiave OpenRouter: `OPENROUTER_API_KEY`, il keyring, oppure `stt_api_key_openrouter` in `tts_config.json`, in quest'ordine.

## Avvisi vocali per Claude Code e Codex

Si attivano in **Finestra dell'app → Collegamenti**. Gli interruttori leggono i file reali:

| Agente | File | Eventi |
|---|---|---|
| Claude Code | `%CLAUDE_CONFIG_DIR%` o `~/.claude/settings.json` | `Stop`, `TaskCompleted` (finito), `Notification` (serve il tuo intervento), `PreToolUse` su `AskUserQuestion` (serve una tua scelta) |
| Codex | `%CODEX_HOME%` o `~/.codex/hooks.json` | `Stop` (finito), `PermissionRequest` (serve il tuo intervento) |

Collegare aggiunge solo le voci mancanti; scollegare rimuove solo quelle che eseguono `notify-tts.py`. Ogni scrittura è preceduta dalla validazione del file, da un backup `<file>.bak-<timestamp>` e avviene in modo atomico. L'hook su `AskUserQuestion` è asincrono e non risponde né approva mai nulla. Codex esegue un nuovo hook solo dopo che l'hai approvato in `/hooks`; la chiave `notify` in `config.toml` non viene toccata.

Gli annunci dicono chi parla, per esempio *"Progetto explore-benchmark, Claudio ha bisogno del tuo aiuto"*. Con più terminali in parallelo, dai un nome a ogni sessione per riconoscere l'agente.

## Pacchetti suoni

Otto pacchetti sono inclusi nell'installer (R2-D2, South Park EN e IT, American Dad, Star Wars, Dune, Maccio Capatonda, Horror Zombie). Ogni pacchetto è una cartella in `~/.claude/cache/tts/sounds/<pacchetto>/` con file MP3 e un `manifest.json` che associa i suoni a sette eventi (`task_done`, `stop`, `permission`, `question`, `idle`, `auth`, `default`). Aggiungi una cartella e compare nella lista, senza modificare il codice.

## Affidabilità e gestione

- **Istanza unica**: un secondo avvio si chiude da solo se il bridge è già attivo (mutex con nome) e non termina mai quello in esecuzione.
- **Supervisor**: il bridge gira come processo figlio e viene riavviato dopo un crash, fino a 3 volte in 5 minuti; poi il supervisor si ferma e registra `supervisor_gave_up`. *Esci* non viene mai riavviato.
- **Avvio all'accesso**: un'attività dell'Utilità di pianificazione per utente (`parlaconclaudio-voice-bridge`), senza diritti di amministratore né limiti di durata, con riavvio in caso di errore. Se l'attività non si può registrare si usa una voce `HKCU\...\Run`. Non è un servizio di Windows, per scelta: i servizi girano nella sessione 0, senza hotkey, appunti, tray né microfono dell'utente. Da riga di comando: `python -m src.voice_bridge.autostart enable|disable|status`.
- **Log**: `voicebridge.log` nella cartella di installazione; errori TTS in `~/.claude/cache/tts/notify-tts.log`.

## Compilare dai sorgenti

Requisiti, comandi e struttura del progetto sono nella sezione **[Build from source](#build-from-source)** in inglese.

---

# Português BR

## O que é

parlaconclaudio transforma voz em texto em **qualquer aplicativo do Windows**: pressione o comando de ditado, fale, e o texto aparece onde você está digitando (ou na área de transferência, se preferir). Nasceu para ditar prompts ao Claude Code sem usar as mãos e virou uma ferramenta de ditado de uso geral que substitui a digitação por voz do Windows em e-mails, documentos, chat e código.

Também dá voz aos seus agentes: **Claude Code e Codex anunciam** trabalhos concluídos, pedidos de permissão e perguntas, dizendo o projeto e o agente que está falando, para você se afastar da tela sem perder o fio.

## Por que é diferente

| | |
|---|---|
| **Reconhecimento de voz local-first** | OpenAI Whisper **large-v3** em **FP16 na CUDA** (faster-whisper / CTranslate2). No modo local **o áudio nunca sai do PC**. |
| **Fallback determinístico** | Uma árvore de decisão fixa: GPU local → Groq → Deepgram. Sem GPU? Uma chave gratuita da Groq basta. |
| **Limpeza por LLM que preserva o idioma** | Uma etapa opcional corrige deslizes, pontuação e termos técnicos deturpados foneticamente (*"di tab"* → **GitHub**), ancorada no **seu glossário**, e nunca traduz. Sem rede, você recebe o texto bruto: o ditado **nunca trava**. |
| **Projetado para falhas** | Retry que respeita `Retry-After`, circuit breaker, proteção contra respostas truncadas, um supervisor que reinicia o bridge após um crash (3 vezes em 5 minutos, esperas de 2/4/6 s) e um mutex de instância única. |
| **Chaves tratadas corretamente** | As chaves de API são lidas de variáveis de ambiente → Gerenciador de Credenciais do Windows (keyring) → JSON, nessa ordem. |
| **Integração não destrutiva** | Os arquivos de hooks são validados antes da escrita, ganham backup com data e são gravados de forma atômica; um arquivo malformado é sinalizado e nunca sobrescrito. |
| **Interface nativa do Windows 11** | Ícones Fluent, cor de destaque do sistema, tema claro/escuro acompanhado em tempo real, DPI por monitor (nítida até em 4K / 300%), cantos arredondados e sombra nativa. |
| **O comando que você quiser** | Qualquer combinação de teclas, o **botão do meio do mouse** ou os **botões laterais** (Voltar/Avançar), pressionar uma vez ou segurar. Os botões do mouse são interceptados no sistema: o clique não chega aos outros apps. |
| **Silencioso em repouso** | Janelas ocultas não consultam dispositivos nem configuração e não redesenham. |
| **Testado e verificável** | 440 testes automatizados; cada release publica o arquivo de checksums SHA-256. |

## Download

**[Baixe o instalador mais recente para Windows](https://github.com/Bottega-Alu/parlaconclaudio/releases/latest/download/parlaconclaudio-voice-bridge-Setup.exe)** (~23 MB), ou escolha uma versão na **[página Releases](https://github.com/Bottega-Alu/parlaconclaudio/releases/latest)**. Os checksums estão em `SHA256SUMS.txt` de cada release.

- Instalação **por usuário, sem direitos de administrador** (x64). Atualizações instalam por cima da versão anterior.
- Na primeira execução um bootstrap cria o ambiente Python e baixa o que sua máquina precisa: **perfil GPU** em PCs NVIDIA (~2,3 GB incluindo o modelo Whisper) ou **perfil CPU/nuvem** nos demais (~0,3 GB).
- Opcional: iniciar com o Windows e conectar os avisos de voz do Claude Code e do Codex.

## Como usar

1. Pressione **`Ctrl+Alt+Espaço`** (padrão) e fale; pressione de novo para parar. Um som curto marca início, fim e entrega.
2. O texto é colado na janela ativa, ou apenas copiado para a área de transferência se você desligar **Colar automaticamente**.
3. **Clique esquerdo** no ícone da bandeja para os controles rápidos; **clique direito** para a janela do app.

A esfera animada na bandeja mostra o estado: arco-íris quando pronta, âmbar enquanto carrega o motor, vermelho gravando, dourado transcrevendo.

### Controles rápidos (clique esquerdo)

Como as Configurações rápidas do Windows 11: ditado, áudio ligado/desligado, pacote de sons, avisos de voz, microfone e motor de reconhecimento, controles separados de volume de **sons** e **vozes**, atalhos para transcrever arquivos de áudio, configurações e Sair. Os pacotes de sons são escolhidos como redes Wi-Fi: expanda um pacote para **Ouvir** ou **Usar**. Abre em cerca de um décimo de segundo e acompanha o modo claro/escuro do **sistema**, como as Configurações rápidas.

### Janela do app (clique direito)

Organizada como as Configurações do Windows 11:

- **Início**: estado, motor em uso, iniciar ditado, transcrever arquivos de áudio.
- **Ditado**: comando (combinação de teclas ou botão do mouse), pressionar ou segurar, uma **área de teste** que mostra os eventos recebidos sem ditar, microfone, motor com estado da GPU, idioma, colar automaticamente, memória da GPU.
- **Avisos de voz**: áudio ligado/desligado, volume de sons e vozes, modo dos avisos, vozes rápidas e todas as 44 vozes em 8 idiomas, pacote de sons com prévia.
- **Conexões**: avisos de voz do Claude Code e do Codex, iniciar com o Windows, chaves Groq e Deepgram (estado, onde obter, teste de conexão).
- **Sobre**: versão, documentação, Sair.

### Arquivos de áudio

*Transcrever arquivo de áudio…* processa gravações em segundo plano com o mesmo motor e a mesma limpeza, e pode adicionar traduções e uma dublagem falada (veja as chaves de configuração abaixo).

## Motores de reconhecimento

| Modo | Motor | Notas |
|---|---|---|
| **Automático** (padrão) | GPU local, depois Groq, depois Deepgram | Usa o primeiro disponível |
| **GPU local** | Whisper large-v3, FP16, CUDA | Requer GPU NVIDIA |
| **Nuvem: Groq** | whisper-large-v3 | Plano gratuito, sem cartão de crédito |
| **Nuvem: Deepgram** | Nova-3 | Crédito inicial gratuito |

Idioma do ditado: detecção automática, ou fixado em italiano, inglês, português, espanhol, francês, alemão ou japonês. Fixá-lo evita erros de detecção em frases curtas.

## Limpeza por LLM

Após a transcrição, uma etapa opcional via **[OpenRouter](https://openrouter.ai)** corrige erros de reconhecimento, restaura termos técnicos em inglês ouvidos foneticamente e acerta a pontuação, **mantendo o idioma falado**. O modelo é `google/gemini-2.5-flash` com `qwen/qwen-2.5-7b-instruct` como reserva. Seus termos em `stt_cleanup_glossary` são passados ao modelo como referência: uma whitelist LLM-first, não uma tabela de aliases no código. No modo local com a limpeza ativa, o **texto** (nunca o áudio) é enviado ao OpenRouter; desligue `enable_stt_cleanup` para operar totalmente offline.

| Chave em `tts_config.json` | Padrão | Função |
|---|---|---|
| `enable_stt_cleanup` | `true` | Liga a limpeza |
| `stt_cleanup_model` | `google/gemini-2.5-flash` | Modelo principal |
| `stt_cleanup_fallback` | `qwen/qwen-2.5-7b-instruct` | Modelo de reserva |
| `stt_cleanup_glossary` | `[]` | Seus termos, usados como referência |
| `transcribe_translations` | `true` | Traduções EN/IT/PT para arquivos de áudio |
| `transcribe_tts_dub` | `true` | Dublagem falada das traduções |
| `transcribe_extras_max_chars` | `2500` | Acima desse tamanho: só a transcrição limpa |

Chave OpenRouter: `OPENROUTER_API_KEY`, o keyring, ou `stt_api_key_openrouter` em `tts_config.json`, nessa ordem.

## Avisos de voz para Claude Code e Codex

Ative em **Janela do app → Conexões**. Os interruptores leem os arquivos reais:

| Agente | Arquivo | Eventos |
|---|---|---|
| Claude Code | `%CLAUDE_CONFIG_DIR%` ou `~/.claude/settings.json` | `Stop`, `TaskCompleted` (concluído), `Notification` (precisa de você), `PreToolUse` em `AskUserQuestion` (precisa da sua escolha) |
| Codex | `%CODEX_HOME%` ou `~/.codex/hooks.json` | `Stop` (concluído), `PermissionRequest` (precisa de você) |

Conectar adiciona só as entradas que faltam; desconectar remove só as que executam `notify-tts.py`. Cada escrita é precedida pela validação do arquivo e por um backup `<arquivo>.bak-<timestamp>`, e é atômica. O hook em `AskUserQuestion` é assíncrono e nunca responde nem aprova nada. O Codex só executa um hook novo depois que você o aprova em `/hooks`; a chave `notify` em `config.toml` não é alterada.

Os avisos dizem quem está falando, por exemplo *"Progetto explore-benchmark, Claudio ha bisogno del tuo aiuto"* (as frases de estado são em italiano; a voz você escolhe). Com vários terminais em paralelo, dê um nome a cada sessão para reconhecer o agente.

## Pacotes de sons

Oito pacotes vêm incluídos no instalador (R2-D2, South Park EN e IT, American Dad, Star Wars, Dune, Maccio Capatonda, Horror Zombie). Cada pacote é uma pasta em `~/.claude/cache/tts/sounds/<pacote>/` com arquivos MP3 e um `manifest.json` que associa os sons a sete eventos (`task_done`, `stop`, `permission`, `question`, `idle`, `auth`, `default`). Adicione uma pasta e ela aparece na lista, sem mudar código.

## Confiabilidade e operação

- **Instância única**: uma segunda execução se encerra sozinha se o bridge já estiver ativo (mutex nomeado) e nunca encerra o que está rodando.
- **Supervisor**: o bridge roda como processo filho e é reiniciado após um crash, até 3 vezes em 5 minutos; depois o supervisor para e registra `supervisor_gave_up`. *Sair* nunca é reiniciado.
- **Iniciar com o Windows**: uma tarefa do Agendador de Tarefas por usuário (`parlaconclaudio-voice-bridge`), sem direitos de administrador nem limite de duração, com reinício em caso de falha. Se a tarefa não puder ser registrada, usa-se uma entrada `HKCU\...\Run`. Não é um serviço do Windows, de propósito: serviços rodam na sessão 0, sem atalhos de teclado, área de transferência, bandeja nem microfone do usuário. Linha de comando: `python -m src.voice_bridge.autostart enable|disable|status`.
- **Logs**: `voicebridge.log` na pasta de instalação; erros de TTS em `~/.claude/cache/tts/notify-tts.log`.

## Compilar a partir do código

Requisitos, comandos e estrutura do projeto estão na seção **[Build from source](#build-from-source)** em inglês.

---

## License

MIT License — see [LICENSE](LICENSE).

Built with **[Claude Code](https://claude.com/claude-code)** by [Anthropic](https://www.anthropic.com).
