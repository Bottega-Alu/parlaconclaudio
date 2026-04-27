# Gesture Triggers + Random Sound Pack — Design

**Date**: 2026-04-27
**Status**: Approved (ready for implementation plan)

## Goal

Aggiungere modalità di attivazione del recording alternative all'hotkey `Ctrl+Alt+Space` e introdurre un "random sound pack" che varia il pack ad ogni evento.

I nuovi trigger di toggle REC:

1. **Long-press mouse sulla sfera** (tray icon) — soglia 480 ms
2. **Doppio click sulla sfera** — finestra 350 ms
3. **Long-press Caps Lock** — soglia 660 ms, con compensazione del toggle OS

L'hotkey `Ctrl+Alt+Space` resta attiva e convive con i nuovi trigger.

## Non-goals

- Modificare il push-to-talk mode esistente (resta tal quale).
- Esporre i singoli ms thresholds nel menu tray (solo via JSON config — YAGNI per ora).
- Identificare la tray icon nell'overflow popup di Windows (fallback: long-press mouse disattivato).

## Architecture

### Nuovo modulo `src/voice_bridge/gesture_listener.py`

Coordina due rilevatori globali via `pynput`. Espone `start()`, `stop()`, e prende un singolo callback `on_toggle_rec()` condiviso con l'hotkey listener esistente.

#### `MouseGestureDetector`

`pynput.mouse.Listener` globale. Stato per-sessione di click sinistro:

- **on press**: chiede al helper `_win32_tray.is_inside_tray_icon(x, y)` se il cursore è sopra l'icona del bridge. Se sì, registra `press_ts = now()` e arma timer 480 ms.
- **on release**:
  - se `now() - press_ts < 480 ms` → "single click" → check finestra doppio click 350 ms con il single click precedente:
    - se due single click consecutivi entro 350 ms → invoca `on_toggle_rec()`
  - altrimenti → era già stato gestito come long-press dal timer (vedi sotto)
- **timer 480 ms**: se ancora in stato `pressed`, invoca `on_toggle_rec()` come long-press; marca lo stato così che il release successivo non scateni nulla.

#### `CapsLockGestureDetector`

`pynput.keyboard.Listener` globale.

- **on press Caps Lock**: registra `press_ts`, salva `caps_state_before = GetKeyState(VK_CAPITAL)`, arma timer 660 ms.
- **on release Caps Lock**: se `now() - press_ts < 660 ms` → era click breve → no-op (Caps Lock toggle OS rimane come da uso normale).
- **timer 660 ms**: se ancora premuto, invoca `on_toggle_rec()`. Poi confronta `GetKeyState(VK_CAPITAL)` con `caps_state_before`: se diverso, invia un tap virtuale di Caps Lock per ripristinare lo stato (`pynput.keyboard.Controller().tap(Key.caps_lock)`). Marca lo stato così il release non causa ulteriori azioni.

### Nuovo helper `src/voice_bridge/_win32_tray.py`

Funzione pura: `is_inside_tray_icon(x, y) -> bool`.

Implementazione: enumera la chain `Shell_TrayWnd → TrayNotifyWnd → SysPager → ToolbarWindow32` (e variante "User Promoted Notification Area"), per ogni bottone della toolbar legge `TBBUTTONINFO` + tooltip via `TB_GETBUTTONINFOW` + `TB_GETBUTTONTEXTW`. Quando trova un bottone con tooltip che inizia per `parlaconclaudio` o `voice_bridge`, ottiene il rect via `TB_GETITEMRECT` e lo confronta con `(x, y)`.

Cache del rect per 2 s per evitare hammer di Win32 calls.

Fallback: se la chain non viene trovata o il bottone non c'è (icona in overflow popup), restituisce `False` per sempre e il `MouseGestureDetector` logga warning una volta sola.

### Modifica `src/voice_bridge/tray_icon.py`

- Set tooltip iniziale dell'icona pystray a `parlaconclaudio` (verifica già esistente, eventualmente normalizza).
- Sezione "🎵 Sound Pack" del menu: aggiungere come prima voce `🎲 Random (mix all packs)` con callback che imposta `pack="random"` in config; check selected se `pack=="random"`.
- Dashboard read-only: quando `pack=="random"` mostrare `🎲 random` al posto del nome pack reale.

### Modifica `scripts/notify-tts.py` (random pack resolver)

I sound pack reali (MP3) vivono nell'hook handler Claude Code, non nel bridge. Config storage: `~/.claude/cache/tts/tts_config.json` chiave `sound_pack`.

- Estendere `get_sound_pack()`:
  - se `tts_config["sound_pack"] == "random"`:
    1. enumera directory pack installati in `~/.claude/cache/tts/sounds/`
    2. esclude entries non-directory e nomi che iniziano per `_`
    3. se lista vuota → fallback `"r2d2"` + log warning una volta sola
    4. ritorna `random.choice(...)` (nuova scelta ad ogni invocazione del hook → ogni evento usa pack diverso)
  - altrimenti → comportamento attuale invariato (ritorna stringa fissa).

Nessuna cache: ogni invocazione del hook è un processo separato (Claude Code spawna `python notify-tts.py` per ogni evento), quindi la "random per evento" è automatica.

### Modifica `src/voice_bridge/bridge.py`

- All'avvio istanza `GestureListener` se `config.gesture.enabled == True`.
- Espone callback `_toggle_recording()` (già di fatto presente per l'hotkey toggle); incapsula con `threading.Lock` + debounce 200 ms così doppi trigger ravvicinati da sorgenti diverse non causano doppio toggle.
- A `shutdown()` chiamare `gesture_listener.stop()`.

### Modifica `src/voice_bridge/config.py`

Nuovi default (vivono nello stesso JSON):

```json
{
  "gesture": {
    "enabled": true,
    "mouse_long_press_ms": 480,
    "caps_long_press_ms": 660,
    "double_click_ms": 350,
    "debounce_ms": 200
  }
}
```

E `pack` accetta il valore `"random"` oltre ai pack reali.

## Data flow (esempio: long-press Caps Lock)

```
[user] keydown Caps Lock
  -> CapsLockGestureDetector.on_press
     -> press_ts = T0
     -> caps_state_before = GetKeyState(VK_CAPITAL)
     -> arm timer (660 ms)
[OS] Caps Lock toggle ON (immediato al keydown)
[time T0+660]
  -> timer fires
     -> on_toggle_rec()  -> bridge._toggle_recording() -> Recorder.start()
     -> if GetKeyState(VK_CAPITAL) != caps_state_before:
          -> Controller().tap(Key.caps_lock)  # restore pre-press state
[user] eventually releases Caps Lock
  -> on_release: marked-as-handled, no-op
```

## Edge cases

- **Caps Lock già ON pre-press**: gestito da `caps_state_before`. Compensazione condizionale.
- **Tray icon non trovata** (overflow): mouse long-press/double-click disattivati per la sessione; Caps Lock + hotkey continuano. Warning loggato una sola volta.
- **REC già in corso quando arriva il toggle**: `_toggle_recording()` ferma e trascrive (comportamento corrente, identico per tutti i trigger).
- **Race con hotkey Ctrl+Alt+Space**: `Lock` + debounce 200 ms in `_toggle_recording()` evita doppio toggle.
- **Pack random con 0 pack installati**: fallback su `"default"` + warning una sola volta.
- **App full-screen / RDP / UAC**: pynput global hook funziona normalmente, nessuna gestione speciale.
- **`gesture.enabled=false`**: nessuno dei due listener viene istanziato. Hotkey resta funzionante.

## Testing

Manual smoke test (no unit test infra per gesture globali — sarebbero test integrati con eventi OS, fuori scope per ora):

1. Long-press mouse sulla sfera 600 ms → REC parte; secondo long-press → REC stop.
2. Doppio click rapido sulla sfera (< 350 ms tra i due) → REC start; ripetere → stop.
3. Caps Lock long-press 800 ms → REC parte; verifica con tasto fisico che Caps OS NON è rimasto attivo. Click breve di Caps Lock → toggle OS funziona normalmente.
4. Ctrl+Alt+Space → REC parte come sempre.
5. Long-press mouse + hotkey premuti contemporaneamente → un solo toggle (debounce).
6. Pack random impostato → tre eventi consecutivi (start/stop/done) usano pack diversi (osservare nei log o all'orecchio).
7. `gesture.enabled=false` in config + restart → mouse/Caps inerti, hotkey funziona.

## Parallelism plan (per writing-plans)

Tre work stream indipendenti:

- **Stream A** — `gesture_listener.py` + `_win32_tray.py` (mouse detector + caps detector + Win32 helper)
- **Stream B** — `sounds.py` random pack resolver
- **Stream C** — `config.py` defaults + `tray_icon.py` voce menu Random + dashboard display

Integrazione finale in `bridge.py` (wiring callback) — dipende da A e C.

## Open questions

Nessuna — tutte le decisioni di design risolte in brainstorming.
