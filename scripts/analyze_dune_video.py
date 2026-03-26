#!/usr/bin/env python3
"""
Analyze the Dune YouTube video to find narrator vs SFX segments.
Uses Whisper to transcribe and identify where the narrator speaks,
then extracts the SFX-only segments between narration.
"""

import json
import subprocess
import sys
from pathlib import Path

AUDIO_FILE = str(Path.home() / "AppData" / "Local" / "Temp" / "dune_full.mp3")

def transcribe_with_timestamps():
    """Use faster-whisper to get word-level timestamps."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Installing faster-whisper...")
        subprocess.run([sys.executable, "-m", "pip", "install", "faster-whisper"],
                      capture_output=True)
        from faster_whisper import WhisperModel

    print("Loading Whisper model...")
    model = WhisperModel("base", device="cuda", compute_type="float16")

    print(f"Transcribing {AUDIO_FILE}...")
    segments, info = model.transcribe(
        AUDIO_FILE,
        language="en",
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    results = []
    for seg in segments:
        results.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })
        print(f"  [{seg.start:7.2f}s - {seg.end:7.2f}s] {seg.text.strip()}")

    return results


def find_sfx_gaps(segments, total_duration=1077):
    """Find gaps between speech segments (these are the SFX)."""
    gaps = []

    # Sort by start time
    segments.sort(key=lambda x: x["start"])

    prev_end = 0
    for seg in segments:
        gap = seg["start"] - prev_end
        if gap > 1.5:  # gaps > 1.5s are likely SFX
            gaps.append({
                "start": round(prev_end, 2),
                "end": round(seg["start"], 2),
                "duration": round(gap, 2),
                "after_text": seg["text"][:60],
            })
        prev_end = max(prev_end, seg["end"])

    # Final gap
    if total_duration - prev_end > 1.5:
        gaps.append({
            "start": round(prev_end, 2),
            "end": total_duration,
            "duration": round(total_duration - prev_end, 2),
            "after_text": "(end of video)",
        })

    return gaps


def main():
    segments = transcribe_with_timestamps()

    print(f"\n{'='*60}")
    print(f"Total speech segments: {len(segments)}")

    gaps = find_sfx_gaps(segments)

    print(f"\nSFX GAPS (non-speech segments > 1.5s):")
    print(f"{'='*60}")
    for i, gap in enumerate(gaps, 1):
        print(f"  #{i:2d} [{gap['start']:7.2f}s - {gap['end']:7.2f}s] "
              f"dur={gap['duration']:6.2f}s  (before: \"{gap['after_text']}\")")

    # Save results
    output = {
        "speech_segments": segments,
        "sfx_gaps": gaps,
    }

    out_file = Path(__file__).parent / "dune_analysis.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved analysis to {out_file}")


if __name__ == "__main__":
    main()
