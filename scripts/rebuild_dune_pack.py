#!/usr/bin/env python3
"""
Rebuild Dune sound pack with CORRECT timestamps from Whisper analysis.
Maps each SFX gap and named moment to the proper sound.
"""

import json
import subprocess
import sys
from pathlib import Path

SOUNDS_DIR = Path.home() / ".claude" / "cache" / "tts" / "sounds"
DUNE_DIR = SOUNDS_DIR / "dune"
AUDIO_FILE = str(Path.home() / "AppData" / "Local" / "Temp" / "dune_full.mp3")

# Mapped from Whisper transcription analysis
# Format: name -> (start, end, description, event)
# SFX = actual sound effect gaps between narrator speech
# VOICE = intentional dialogue/voice clips from the films
DUNE_SOUNDS = {
    # === HONORABLE MENTIONS ===
    # Spider eating from bowl (gap #1 area, but actual SFX at 29-32)
    "dune-spider-eating": {
        "start": 29.5, "end": 33.5,
        "desc": "Human spider eating from bowl - creepy chewing",
        "event": "idle",
    },
    # "The thing must leave" - Bene Gesserit voice command
    "dune-bene-thing-must-leave": {
        "start": 32.0, "end": 36.0,
        "desc": "The thing must leave - Bene Gesserit voice command",
        "event": "permission",
    },
    # Baron choking on ceiling (gap #2: 55.45-62.62)
    "dune-baron-ceiling": {
        "start": 55.5, "end": 62.5,
        "desc": "Baron Harkonnen choking on ceiling after poisoning",
        "event": "auth",
    },
    # Feyd growl (gap #3+4 area: ~97-104)
    "dune-feyd-growl": {
        "start": 97.1, "end": 103.5,
        "desc": "Feyd-Rautha's animalistic growl after killing",
        "event": "auth",
    },
    # Piscador clicking (gap #5: 111.43-115.41)
    "dune-piscador-clicking": {
        "start": 111.5, "end": 115.4,
        "desc": "Piscador clicking sounds in the arena",
        "event": "question",
    },

    # === TOP 20 ===
    # #20: Sand swallow - frequency roll-offs (gap #6: 165.95-176.88)
    "dune-sand-swallow": {
        "start": 166.0, "end": 176.8,
        "desc": "Sand swallowing Paul - frequency roll-off silence",
        "event": "stop",
    },
    # #20 continued: Two scenes edit (gap #7: 195.22-214.48)
    "dune-sand-music": {
        "start": 195.5, "end": 214.0,
        "desc": "Sand scenes musical edit - sand frequency transition",
        "event": "default",
    },
    # #19: Bodies hitting floor (gap #8: 220.76-228.22)
    "dune-bodies-floor": {
        "start": 220.8, "end": 228.0,
        "desc": "Harkonnen bodies hitting floor in eclipse scene",
        "event": "stop",
    },
    # #18: Bene Gesserit ship leaving (around 253-288)
    "dune-bene-ship": {
        "start": 273.3, "end": 282.8,
        "desc": "Bene Gesserit ship digital sound and departure",
        "event": "idle",
    },
    # Bene ship thunder (gap #9: 279-288 overlapping narrator)
    "dune-bene-ship-thunder": {
        "start": 282.9, "end": 288.5,
        "desc": "Bene Gesserit ship disappearing into thunder",
        "event": "idle",
    },
    # #17: Muadib mouse sniffing (around 307-325)
    "dune-muaddib-mouse": {
        "start": 317.0, "end": 325.0,
        "desc": "Muadib desert mouse sniffing - tiny animal vs harvester",
        "event": "question",
    },
    # #16: Sand compactor (around 331-361)
    "dune-sand-compactor": {
        "start": 348.3, "end": 361.0,
        "desc": "Sand compactor magnetic field device",
        "event": "task_done",
    },
    # #15: God Emperor voice (around 374-409)
    "dune-god-emperor-voice": {
        "start": 374.8, "end": 386.4,
        "desc": "God Emperor opening voice - spiritual narration",
        "event": "permission",
    },
    # #14: Hunter seeker drill (around 418-456)
    "dune-hunter-seeker": {
        "start": 434.9, "end": 444.5,
        "desc": "Hunter seeker drill emerging from wall - high pitched",
        "event": "question",
    },
    # #13: Chani explosion (around 462-479)
    "dune-chani-explosion": {
        "start": 462.5, "end": 474.5,
        "desc": "Chani exploding Harkonnen with rocket launcher",
        "event": "task_done",
    },
    # #12: Ornithopter turrets (gap #10: 483.32-487.34 + more)
    "dune-ornithopter-turrets": {
        "start": 483.4, "end": 498.9,
        "desc": "Ornithopter turrets - electronic tactical gunfire",
        "event": "default",
    },
    # #11: Emperor ship arriving (around 512-556)
    "dune-emperor-ship": {
        "start": 532.1, "end": 544.0,
        "desc": "Emperor ship entering atmosphere with choir and fire",
        "event": "idle",
    },
    # #10: Shields first use (gap #11: 580.75-588.76)
    "dune-shields": {
        "start": 556.9, "end": 580.7,
        "desc": "First shields activation - soft crunchy ping",
        "event": "task_done",
    },
    # #9: Atreides ship note (gap #12: 615.30-618.89)
    "dune-atreides-ship": {
        "start": 615.3, "end": 618.8,
        "desc": "Atreides ship satisfying digital note",
        "event": "auth",
    },
    # #8: Arena door unlocking for Feyd (around 618-674)
    "dune-arena-door": {
        "start": 649.6, "end": 664.5,
        "desc": "Arena door heavy latch - rhythmic unlocking for Feyd",
        "event": "auth",
    },
    # #7: Arena announcer (gap #13: 674.16-682.18 + 696-722)
    "dune-arena-announcer": {
        "start": 674.2, "end": 682.0,
        "desc": "Arena announcer booming voice",
        "event": "permission",
    },
    # Arena announcer extended
    "dune-arena-announcer-extended": {
        "start": 696.9, "end": 722.0,
        "desc": "Arena announcer full captivating speech with sound design",
        "event": "permission",
    },
    # #6: Worm speaks to Paul (around 738-779)
    "dune-worm-speaks": {
        "start": 738.8, "end": 758.6,
        "desc": "Sandworm speaking to Paul - throat clicking spiritual",
        "event": "question",
    },
    # #5: Worm vibration sand sinking (around 779-821)
    "dune-worm-vibration": {
        "start": 791.8, "end": 821.8,
        "desc": "Sandworm vibration - sand melting into itself",
        "event": "idle",
    },
    # #4: Ornithopter takeoff (around 821-854)
    "dune-ornithopter-takeoff": {
        "start": 827.4, "end": 854.5,
        "desc": "Ornithopter first takeoff - doors, wings, liftoff",
        "event": "task_done",
    },
    # #3: Atomic explosion (around 860-929)
    "dune-atomic": {
        "start": 860.6, "end": 912.3,
        "desc": "Atomic explosion with quiet reverb aftermath",
        "event": "stop",
    },
    # #2: Margot voice (around 929-968)
    "dune-margot-voice": {
        "start": 933.4, "end": 968.8,
        "desc": "Margot Fenring Bene Gesserit voice - hypnotic seduction",
        "event": "permission",
    },
    # #1: Missile shields (gap #15: 968.79-984.99 + 993-1036)
    "dune-missile-shields": {
        "start": 968.8, "end": 984.9,
        "desc": "Harkonnen missiles hitting Atreides shields - pause and explode",
        "event": "default",
    },
    "dune-missile-shields-full": {
        "start": 1007.5, "end": 1036.0,
        "desc": "Missile shield full explosion - pressure buildup and burst",
        "event": "default",
    },
}

SHORT_DURATION = 5.0


def extract_segment(source, output, start, duration):
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", source,
             "-ss", str(start), "-t", str(duration),
             "-acodec", "libmp3lame", "-ab", "192k",
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             output],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  Error: {e}")
        return False


def verify_audio(filepath):
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", filepath, "-af", "volumedetect",
             "-f", "null", "/dev/null"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stderr.split("\n"):
            if "max_volume" in line:
                return float(line.split("max_volume:")[1].strip().split()[0])
    except:
        pass
    return -91.0


def main():
    DUNE_DIR.mkdir(parents=True, exist_ok=True)

    if not Path(AUDIO_FILE).is_file():
        print(f"Audio file not found: {AUDIO_FILE}")
        print("Run: yt-dlp -x --audio-format mp3 -o <path> <url>")
        sys.exit(1)

    ok = 0
    fail = 0
    manifest_sounds = []

    for name, info in DUNE_SOUNDS.items():
        start = info["start"]
        end = info["end"]
        full_dur = end - start
        desc = info["desc"]
        event = info["event"]

        # Full version
        full_path = str(DUNE_DIR / f"{name}.mp3")
        print(f"  {name}.mp3 ({start:.1f}s-{end:.1f}s, {full_dur:.1f}s)...", end=" ")
        if extract_segment(AUDIO_FILE, full_path, start, full_dur):
            vol = verify_audio(full_path)
            status = "OK" if vol > -50 else "QUIET"
            print(f"{status} ({vol:.1f}dB)")
            ok += 1
            manifest_sounds.append({
                "file": f"{name}.mp3",
                "label": name,
                "duration_ms": int(full_dur * 1000),
                "event": event,
                "short": False,
                "use_hint": desc,
            })
        else:
            print("FAIL")
            fail += 1

        # Short version
        short_dur = min(SHORT_DURATION, full_dur)
        # For short clips, find the most interesting part (skip first 0.5s if long)
        short_start = start + 0.5 if full_dur > 8 else start
        short_path = str(DUNE_DIR / f"{name}-short.mp3")
        print(f"  {name}-short.mp3 ({short_start:.1f}s, {short_dur:.1f}s)...", end=" ")
        if extract_segment(AUDIO_FILE, short_path, short_start, short_dur):
            vol = verify_audio(short_path)
            status = "OK" if vol > -50 else "QUIET"
            print(f"{status} ({vol:.1f}dB)")
            ok += 1
            manifest_sounds.append({
                "file": f"{name}-short.mp3",
                "label": f"{name}-short",
                "duration_ms": int(short_dur * 1000),
                "event": event,
                "short": True,
                "use_hint": f"{desc} (short)",
            })
        else:
            print("FAIL")
            fail += 1

    # Build manifest
    # Group events
    event_map = {}
    for s in manifest_sounds:
        if s["short"]:
            event_map.setdefault(s["event"], []).append(s["file"])

    manifest = {
        "pack": "dune",
        "version": "3.0",
        "description": "Dune Films - Best Sounds: Bene Gesserit Voice, sandworms, shields, Zimmer score. Rebuilt with Whisper-verified timestamps.",
        "source": "https://www.youtube.com/watch?v=6UO0mKGuuHk",
        "sound_count": len(manifest_sounds),
        "events": event_map,
        "sounds": manifest_sounds,
    }

    manifest_path = DUNE_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest saved: {manifest_path}")

    print(f"\n{'='*60}")
    print(f"  DUNE PACK v3.0 REBUILD COMPLETE")
    print(f"  OK: {ok}, Failed: {fail}")
    print(f"  Sounds: {len(DUNE_SOUNDS)} x 2 (full + short) = {len(DUNE_SOUNDS)*2}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
