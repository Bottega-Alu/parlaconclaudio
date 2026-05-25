# Subagent quiet-voice — design

**Date:** 2026-05-25
**Scope:** `C:\Users\Fra\.claude\scripts\notify-tts.py` (single file)
**Status:** approved

## Problem

In multi-agent workflows several subagents finish within a few seconds of
each other. Each fires a `TaskCompleted` hook, and each hook plays:

1. a chime mp3 from the active sound pack (~1–3 s)
2. a context-aware TTS message (~3–6 s)
3. a ~14 % chance of a long Frisco easter-egg quote (~4–8 s)

The existing burst-suppression window is 300 ms, which only collapses
hooks that fire in the same millisecond batch. Subagents that finish
500 ms–2 s apart all play their full fanfare and overlap into
cacophony ("chiasso baccano millecò").

## Goal

Subagent termination must produce one short, varied audio cue and
nothing more. The full fanfara is reserved for the main agent `Stop`
event and for `Notification`s.

## Behavior matrix

| Event | mp3 chime | TTS | Frisco easter egg | Burst window |
|---|---|---|---|---|
| `TaskCompleted` (subagent) | no | fixed phrase `"Subagente terminato."` — voice picked at random from the multilingual pool | no | **1500 ms** |
| `Stop` (main agent) | yes (unchanged) | context-aware (unchanged) | ~14 % (unchanged) | 300 ms |
| `Notification` | yes (unchanged) | context-aware (unchanged) | ~14 % (unchanged) | 300 ms |

## Design choices

- **Fixed phrase, varied voice.** The phrase is always
  `"Subagente terminato."` in Italian. The voice is picked uniformly at
  random from the existing `_ALL_VOICES` pool (~25 edge-tts voices
  across 7 languages). Italian phrase + e.g. Japanese or Brazilian
  voice gives variety without monotony and without polluting the
  semantic channel with random easter-egg quotes.

- **No chime mp3 on subagents.** The user explicitly asked for this.
  Removes the 1–3 s overlap source.

- **Per-event burst window.** `TaskCompleted` uses 1500 ms so that a
  cluster of 5 subagents finishing inside 2 s collapses to a single
  voice line (last-wins). `Stop` and `Notification` stay at 300 ms so
  the main agent and prompts remain reactive.

- **Easter egg disabled on subagents.** The long Frisco quotes are the
  worst offender for overlap; they fire only on `Stop` and
  `Notification`.

- **Cache friendly.** `"Subagente terminato."` × 25 voices → 25 cached
  mp3 in `~/.claude/cache/tts/dynamic/`. First invocation per voice
  pays the edge-tts roundtrip, subsequent are instant playback.

- **Mute + volume already work.** Both honor the tray config because of
  the earlier patch on `play_mp3` / `play_mp3_sync`.

## Code changes (`notify-tts.py`)

1. New module-level constants:
   ```python
   SUBAGENT_PHRASE = "Subagente terminato."
   SUBAGENT_BURST_WAIT_MS = 1500
   ```

2. `burst_check_should_play()` accepts a `wait_ms` parameter (default
   `BURST_WAIT_MS` = 300, unchanged for existing callers).

3. New helper `play_subagent_voice()` — picks a voice at random from
   `_ALL_VOICES`, builds a minimal profile (`rate="+0%"`, `pitch="+0Hz"`),
   resolves the audio via the existing `resolve_audio()` cache, plays
   it via the existing `play_mp3()` (which already honors mute /
   volume).

4. `main()` differentiates by `event_name`:
   - For `TaskCompleted`:
     - call `burst_check_should_play(SUBAGENT_BURST_WAIT_MS)`
     - keep `update_tracker(data)` (subtask counter stays correct for
       any future use, just not spoken)
     - call `play_subagent_voice()` and return
   - For `Stop` and `Notification`:
     - call `burst_check_should_play()` with the default 300 ms
     - behave exactly as today (chime + TTS + maybe easter egg)

5. No changes to: sound packs, manifests, tray menu, config schema,
   subtask tracker, cache cleanup.

## Out of scope

- No changes to the tray icon menu.
- No changes to TTS voice presets or pack contents.
- No new config key (the subagent behavior is hard-coded by design —
  user already settled on "Subagente terminato" + voice pool).
- No new burst-window config key (1500 ms hard-coded).

## Risk

- A subagent that finishes 2 s after another will still be suppressed
  by the 1500 ms window. Acceptable: the user prefers one clean line
  over two close ones.
- A lone subagent in isolation will speak `"Subagente terminato."`
  every time. Acceptable per design.
- First time a voice is used, edge-tts must succeed. If it fails the
  existing `resolve_audio` returns `None` and `play_mp3` silently
  skips — same fallback behavior as the rest of the script.
