#!/usr/bin/env python3
"""
Auto-download & setup sound packs: South Park + Melchandead (horror/zombie)

Downloads sounds from free sources and organizes them into the
~/.claude/cache/tts/sounds/ directory with semantic chime mappings.

Usage:
    python scripts/download_packs.py [--pack southpark|horror-zombie|all]
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# === PATHS ===
CACHE_DIR = Path.home() / ".claude" / "cache" / "tts"
SOUNDS_DIR = CACHE_DIR / "sounds"

# === SOUTH PARK PACK ===
# Sources: myinstants.com, soundfxcenter.com, thesoundarchive.com
SOUTHPARK_SOUNDS = {
    # task_done: acknowledgment/success sounds
    "sp-task-done-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/nice_nWiU8mH.mp3",
        "desc": "Nice! (South Park)",
    },
    "sp-task-done-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/cartmansweet.mp3",
        "desc": "Sweet! - Cartman",
    },
    "sp-task-done-3.mp3": {
        "url": "https://www.myinstants.com/media/sounds/south-park-its-a-beautiful-day.mp3",
        "desc": "Beautiful Day - Cartman",
    },
    # stop: work completed, going home
    "sp-stop-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/eric-cartman-oh-yeah-well-screw-you-guys-im-going-home.mp3",
        "desc": "Screw you guys, I'm going home!",
    },
    "sp-stop-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/cartman2.mp3",
        "desc": "I'm going home - Cartman",
    },
    "sp-stop-3.mp3": {
        "url": "https://soundfxcenter.com/television/south-park/8d82b5_South_Park_Cartman_Whatever_Sound_Effect.mp3",
        "desc": "Whatever - Cartman",
    },
    # permission: asking for approval
    "sp-permission-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/cartman-respect-mah-authoritah.mp3",
        "desc": "Respect my authoritah! - Cartman",
    },
    "sp-permission-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/goddamnit-cartman.mp3",
        "desc": "GODDAMNIT CARTMAN!",
    },
    "sp-permission-3.mp3": {
        "url": "https://www.myinstants.com/media/sounds/you-will-respect-my-authoritah_1.mp3",
        "desc": "You WILL respect my authoritah!",
    },
    # question: asking user something
    "sp-question-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/south-park-shenanigans.mp3",
        "desc": "Shenanigans!",
    },
    "sp-question-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/took-our-job-mp3cut.mp3",
        "desc": "They took our job!",
    },
    # idle: waiting for input
    "sp-idle-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/howdy-ho.mp3",
        "desc": "Howdy Hooo!! - Mr Hankey",
    },
    "sp-idle-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/timmy-south-park.mp3",
        "desc": "TIMMY!",
    },
    "sp-idle-3.mp3": {
        "url": "https://soundfxcenter.com/television/south-park/8d82b5_South_Park_Cartman_Boring_Sound_Effect.mp3",
        "desc": "Boring! - Cartman",
    },
    "sp-idle-4.mp3": {
        "url": "https://soundfxcenter.com/television/south-park/8d82b5_South_Park_Cartman_Ahem_Sound_Effect.mp3",
        "desc": "Ahem - Cartman",
    },
    "sp-idle-5.mp3": {
        "url": "https://soundfxcenter.com/television/south-park/8d82b5_South_Park_Cartman_Come_On_You_Guys_Sound_Effect.mp3",
        "desc": "Come on you guys! - Cartman",
    },
    # auth: authentication completed
    "sp-auth-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/i-do-what-i-want.mp3",
        "desc": "I do what I want! - Cartman",
    },
    "sp-auth-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/im_back-b9304d01-cdb5-444e-b3a1-6c73084b52f4.mp3",
        "desc": "I'm back! - South Park",
    },
    # default: general notification
    "sp-default-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/south-park-sound.mp3",
        "desc": "South Park sound",
    },
    "sp-default-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/south-park-guitar-transition-strums-collection-mp3cut.mp3",
        "desc": "South Park transition guitar",
    },
    "sp-default-3.mp3": {
        "url": "https://www.myinstants.com/media/sounds/southpark_cartman_beefcake.mp3",
        "desc": "BEEFCAKE! - Cartman",
    },
}

# Semantic chime mapping for South Park
SOUTHPARK_CHIMES = {
    "task_done": ["sp-task-done-1.mp3", "sp-task-done-2.mp3", "sp-task-done-3.mp3"],
    "stop": ["sp-stop-1.mp3", "sp-stop-2.mp3", "sp-stop-3.mp3"],
    "permission": ["sp-permission-1.mp3", "sp-permission-2.mp3", "sp-permission-3.mp3"],
    "question": ["sp-question-1.mp3", "sp-question-2.mp3"],
    "idle": ["sp-idle-1.mp3", "sp-idle-2.mp3", "sp-idle-3.mp3", "sp-idle-4.mp3", "sp-idle-5.mp3"],
    "auth": ["sp-auth-1.mp3", "sp-auth-2.mp3"],
    "default": ["sp-default-1.mp3", "sp-default-2.mp3", "sp-default-3.mp3"],
}

# === MELCHANDEAD PACK (Horror/Zombie) ===
# Sources: quicksounds.com, orangefreesounds.com (CC-licensed)
HORROR_ZOMBIE_SOUNDS = {
    # task_done: zombie acknowledgment
    "md-task-done-1.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/7648792_1686797639_673859211.mp3",
        "desc": "Zombie Grunt - acknowledgment",
    },
    "md-task-done-2.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/682817001_48301912_1691334510.mp3",
        "desc": "Zombie Grunt 2 - acknowledged",
    },
    # stop: horror ending stinger
    "md-stop-1.mp3": {
        "url": "https://orangefreesounds.com/wp-content/uploads/2022/09/Horror-stinger-sound-effect.mp3",
        "desc": "Horror Stinger - work complete (7s)",
    },
    "md-stop-2.mp3": {
        "url": "https://orangefreesounds.com/wp-content/uploads/2023/03/Horror-stinger-short-sound-effect.mp3",
        "desc": "Horror Stinger Short - done (4s)",
    },
    # permission: suspense/tension
    "md-permission-1.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/107994564_1573746859_1761245374.mp3",
        "desc": "Zombie Tense - permission needed",
    },
    "md-permission-2.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/327827713_40756167_1144403399.mp3",
        "desc": "Zombie Shout - attention required",
    },
    # question: eerie/mysterious
    "md-question-1.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/63175302_1841388388_1741321213.mp3",
        "desc": "Zombie Moan Medium - questioning",
    },
    "md-question-2.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/689153487_680636868_115432432.mp3",
        "desc": "Zombie Rattle Man - curious",
    },
    # idle: atmospheric waiting
    "md-idle-1.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/1629455743_165363394_1808421223.mp3",
        "desc": "Zombie Groan - idle waiting",
    },
    "md-idle-2.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/126227208_1328348829_955442689.mp3",
        "desc": "Zombie Groan Monsterious - ambient idle",
    },
    # auth: dark mechanical/lock sounds
    "md-auth-1.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/671474542_769345652_104154832.mp3",
        "desc": "Zombie Hiss Man - auth gate",
    },
    "md-auth-2.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/378785300_483763763_662359874.mp3",
        "desc": "Zombie Hiss Man 2 - access granted",
    },
    # default: general horror notification
    "md-default-1.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/1492028976_1546248065_2076975264.mp3",
        "desc": "Zombie Monster - notification",
    },
    "md-default-2.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/249436772_1139312626_2068428467.mp3",
        "desc": "Zombie Hurt - alert",
    },
    # bonus: extra variety
    "md-task-done-3.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/585282634_1519030148_2039614353.mp3",
        "desc": "Zombie Grunt 3 - confirmed",
    },
    "md-stop-3.mp3": {
        "url": "https://www.orangefreesounds.com/wp-content/uploads/2015/08/Zombie-noises.mp3",
        "desc": "Zombie Noises - ending (3s)",
    },
    "md-idle-3.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/847671131_282613893_796999888.mp3",
        "desc": "Zombie Groan Monsterious 3 - deep idle",
    },
    "md-question-3.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/2040560653_1709728415_1339607992.mp3",
        "desc": "Zombie Shout 2 - what?",
    },
    "md-default-3.mp3": {
        "url": "https://quicksounds.com/uploads/tracks/772017985_1941664996_1299034954.mp3",
        "desc": "Zombie Roar Woman - alert",
    },
}

# Semantic chime mapping for Melchandead
HORROR_ZOMBIE_CHIMES = {
    "task_done": ["md-task-done-1.mp3", "md-task-done-2.mp3", "md-task-done-3.mp3"],
    "stop": ["md-stop-1.mp3", "md-stop-2.mp3", "md-stop-3.mp3"],
    "permission": ["md-permission-1.mp3", "md-permission-2.mp3"],
    "question": ["md-question-1.mp3", "md-question-2.mp3", "md-question-3.mp3"],
    "idle": ["md-idle-1.mp3", "md-idle-2.mp3", "md-idle-3.mp3"],
    "auth": ["md-auth-1.mp3", "md-auth-2.mp3"],
    "default": ["md-default-1.mp3", "md-default-2.mp3", "md-default-3.mp3"],
}


# === AMERICAN DAD PACK ===
# Sources: myinstants.com, soundfxcenter.com
AMERICANDAD_SOUNDS = {
    # task_done: Roger/Stan acknowledgment
    "ad-task-done-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/good-morning-usa.mp3",
        "desc": "Good Morning USA! - theme",
    },
    "ad-task-done-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/roger-roger.mp3",
        "desc": "Roger Roger!",
    },
    # stop: bye / going home
    "ad-stop-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/bye-have-a-beautiful-time.mp3",
        "desc": "Bye! Have a beautiful time!",
    },
    "ad-stop-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/bonecall-short.mp3",
        "desc": "Bonecall - Roger",
    },
    # permission: Roger dramatic
    "ad-permission-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/sixthsence.mp3",
        "desc": "I'll skin you alive! - Roger",
    },
    "ad-permission-2.mp3": {
        "url": "https://www.myinstants.com/media/sounds/roger-nooo.mp3",
        "desc": "Nooo! - Roger",
    },
    # question: Roger curious
    "ad-question-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/straddle-mein-bowl.mp3",
        "desc": "STRADDLE MEIN BOWL - Roger",
    },
    # idle: waiting
    "ad-idle-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/roger-nooo.mp3",
        "desc": "Nooo! - Roger (waiting)",
    },
    # default: general notification
    "ad-default-1.mp3": {
        "url": "https://www.myinstants.com/media/sounds/good-morning-usa.mp3",
        "desc": "Good Morning USA! - default",
    },
}

# Semantic chime mapping for American Dad
# Uses existing files from the pack too (distracted.mp3, etc.)
AMERICANDAD_CHIMES = {
    "task_done": ["ad-task-done-1.mp3", "ad-task-done-2.mp3", "happy-hour.mp3"],
    "stop": ["ad-stop-1.mp3", "ad-stop-2.mp3"],
    "permission": ["ad-permission-1.mp3", "ad-permission-2.mp3"],
    "question": ["ad-question-1.mp3", "oh-my-god.mp3"],
    "idle": ["ad-idle-1.mp3", "distracted.mp3", "haley-wake-up.mp3"],
    "auth": ["ad-task-done-2.mp3", "roger-elections.mp3"],
    "default": ["ad-default-1.mp3", "oh-my-god.mp3", "happy-hour.mp3"],
}


def download_file(url: str, dest: Path, desc: str) -> tuple[bool, str]:
    """Download a single file with retry logic."""
    if dest.is_file() and dest.stat().st_size > 0:
        return True, f"  [SKIP] {dest.name} (already exists)"

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                if len(data) < 100:
                    return False, f"  [FAIL] {dest.name} - file too small ({len(data)}b)"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return True, f"  [OK]   {dest.name} ({len(data)//1024}KB) - {desc}"
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                return False, f"  [FAIL] {dest.name} - {e}"
    return False, f"  [FAIL] {dest.name} - max retries"


def get_duration_ms(filepath: Path) -> int:
    """Get audio duration in milliseconds via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(filepath)],
            capture_output=True, text=True, timeout=5,
        )
        return int(float(result.stdout.strip()) * 1000)
    except Exception:
        return 0


def generate_manifest(pack_name: str, sounds_meta: dict, chimes: dict, pack_dir: Path, desc: str) -> None:
    """Generate manifest.json with ffprobe durations for the pack."""
    sound_entries = {}

    # Index downloaded sounds
    for filename, info in sounds_meta.items():
        filepath = pack_dir / filename
        if filepath.is_file():
            duration = get_duration_ms(filepath)
            event = "unmapped"
            for ev, files in chimes.items():
                if filename in files:
                    event = ev
                    break
            sound_entries[filename] = {
                "label": info["desc"],
                "duration_ms": duration,
                "event": event,
                "source_url": info["url"],
            }

    # Also index pre-existing MP3s referenced in chimes but not in sounds_meta
    for ev, files in chimes.items():
        for filename in files:
            if filename not in sound_entries:
                filepath = pack_dir / filename
                if filepath.is_file():
                    duration = get_duration_ms(filepath)
                    sound_entries[filename] = {
                        "label": filename.replace(".mp3", "").replace("-", " ").title(),
                        "duration_ms": duration,
                        "event": ev,
                    }

    manifest = {
        "pack": pack_name,
        "version": "1.0",
        "description": desc,
        "sound_count": len(sound_entries),
        "chimes": chimes,
        "sounds": sound_entries,
    }

    manifest_path = pack_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"  manifest.json saved ({len(sound_entries)} sounds with metadata)")


def download_pack(pack_name: str, sounds: dict, chimes: dict, desc: str = "") -> tuple[int, int]:
    """Download all sounds for a pack in parallel, then generate manifest."""
    pack_dir = SOUNDS_DIR / pack_name
    pack_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Downloading pack: {pack_name}")
    print(f"  Target: {pack_dir}")
    print(f"  Sounds: {len(sounds)}")
    print(f"{'='*60}\n")

    ok_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for filename, info in sounds.items():
            dest = pack_dir / filename
            future = executor.submit(download_file, info["url"], dest, info["desc"])
            futures[future] = filename

        for future in as_completed(futures):
            success, msg = future.result()
            print(msg)
            if success:
                ok_count += 1
            else:
                fail_count += 1

    # Generate manifest.json with ffprobe metadata (replaces _chime_mapping.json)
    generate_manifest(pack_name, sounds, chimes, pack_dir, desc or pack_name)

    return ok_count, fail_count


def update_notify_tts_mappings():
    """Print instructions for adding pack mappings to notify-tts.py."""
    print(f"\n{'='*60}")
    print("  INTEGRATION: Add to notify-tts.py")
    print(f"{'='*60}")
    print("""
  To enable semantic chime mapping (like R2D2), add these
  dictionaries to notify-tts.py after R2D2_CHIMES:

  SOUTHPARK_CHIMES = {
      "task_done": ["sp-task-done-1.mp3", "sp-task-done-2.mp3"],
      "stop": ["sp-stop-1.mp3", "sp-stop-2.mp3"],
      "permission": ["sp-permission-1.mp3", "sp-permission-2.mp3"],
      "question": ["sp-question-1.mp3", "sp-question-2.mp3"],
      "idle": ["sp-idle-1.mp3", "sp-idle-2.mp3"],
      "auth": ["sp-auth-1.mp3", "sp-auth-2.mp3"],
      "default": ["sp-default-1.mp3", "sp-default-2.mp3"],
  }

  HORROR_ZOMBIE_CHIMES = {
      "task_done": ["md-task-done-1.mp3", "md-task-done-2.mp3"],
      "stop": ["md-stop-1.mp3", "md-stop-2.mp3"],
      "permission": ["md-permission-1.mp3", "md-permission-2.mp3"],
      "question": ["md-question-1.mp3", "md-question-2.mp3"],
      "idle": ["md-idle-1.mp3", "md-idle-2.mp3"],
      "auth": ["md-auth-1.mp3", "md-auth-2.mp3"],
      "default": ["md-default-1.mp3", "md-default-2.mp3"],
  }
""")


def main():
    parser = argparse.ArgumentParser(description="Download sound packs for parlaconclaudio")
    parser.add_argument("--pack", choices=["southpark", "horror-zombie", "americandad", "all"],
                        default="all", help="Which pack to download")
    parser.add_argument("--integrate", action="store_true",
                        help="Also update notify-tts.py with semantic mappings")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  parlaconclaudio - Sound Pack Downloader")
    print("  Packs: South Park + Melchandead (Horror/Zombie)")
    print("=" * 60)

    total_ok = 0
    total_fail = 0

    if args.pack in ("southpark", "all"):
        ok, fail = download_pack(
            "south-park", SOUTHPARK_SOUNDS, SOUTHPARK_CHIMES,
            "South Park character sounds - Cartman, Kenny, Butters & friends",
        )
        total_ok += ok
        total_fail += fail

    if args.pack in ("horror-zombie", "all"):
        ok, fail = download_pack(
            "horror-zombie", HORROR_ZOMBIE_SOUNDS, HORROR_ZOMBIE_CHIMES,
            "Horror/zombie themed sounds - grunts, stingers, groans",
        )
        total_ok += ok
        total_fail += fail

    if args.pack in ("americandad", "all"):
        ok, fail = download_pack(
            "american-dad", AMERICANDAD_SOUNDS, AMERICANDAD_CHIMES,
            "American Dad - Roger, Stan Smith & family",
        )
        total_ok += ok
        total_fail += fail

    print(f"\n{'='*60}")
    print(f"  RESULTS: {total_ok} downloaded, {total_fail} failed")
    print(f"{'='*60}")

    if args.integrate:
        update_notify_tts_mappings()

    print("\nDone! Switch pack in tray icon or edit tts_config.json:")
    print('  {"sound_pack": "south-park"} | {"sound_pack": "horror-zombie"} | {"sound_pack": "american-dad"}')
    print()


if __name__ == "__main__":
    main()
