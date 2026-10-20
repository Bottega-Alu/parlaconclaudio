"""The tray marble sphere is rendered with numpy (idle CPU fix, 2026-10-20).

The reference below is the original per-pixel pure-Python renderer, kept here
verbatim: the vectorized one must produce the same image (it ran ~90 ms per
192px frame in the GIL and was ~50% of a core at rest).
"""
import colorsys
import math
import time

import numpy as np
import pytest

from src.voice_bridge import tray_icon as ti

pytestmark = pytest.mark.skipif(not ti.TRAY_AVAILABLE, reason="Pillow/pystray missing")


def _reference_sphere(size, hue_offset, hue_range=0.3, saturation=0.75, brightness=0.95, time_val=0.0):
    from PIL import Image, ImageFilter
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = img.load()
    center = size / 2.0
    radius = (size - 6) / 2.0

    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            dist = math.sqrt(dx * dx + dy * dy)

            if dist > radius:
                continue

            norm_dist = dist / radius
            nx = dx / radius
            ny = dy / radius
            nz = math.sqrt(max(0, 1 - nx * nx - ny * ny))

            # 3D sphere lighting
            light_dot = nx * (-0.3) + ny * (-0.4) + nz * 0.8
            light_factor = max(0.15, min(1.0, 0.5 + light_dot * 0.6))

            # Marble veins — rotate sampling point around Y axis (like Earth)
            rot_offset = time_val * 12.0  # horizontal scroll speed in pixels
            marble = ti._marble_noise(x + rot_offset, y, 0.0)

            # Hue: uniform base + very subtle vein variation
            h = (hue_offset + marble * hue_range * 0.08) % 1.0
            # Saturation: smooth, uniform
            s = saturation * (1.0 - norm_dist * 0.10)
            # Brightness: base + lighting (no pulse/shimmer in idle)
            v = brightness * light_factor

            # Main specular highlight (glass)
            spec_dist = math.sqrt((nx + 0.35) ** 2 + (ny + 0.35) ** 2)
            if spec_dist < 0.38:
                spec = (1.0 - spec_dist / 0.38) ** 2.5 * 0.65
                v = min(1.0, v + spec)
                s = max(0.0, s - spec * 0.8)

            # Pinpoint specular
            spec2_dist = math.sqrt((nx + 0.25) ** 2 + (ny + 0.50) ** 2)
            if spec2_dist < 0.12:
                spec2 = (1.0 - spec2_dist / 0.12) ** 4 * 0.35
                v = min(1.0, v + spec2)
                s = max(0.0, s - spec2)

            # Rim lighting
            if norm_dist > 0.65:
                rim = (norm_dist - 0.65) / 0.35
                v = min(1.0, v + rim ** 1.5 * 0.25)

            r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))

            alpha = 255
            if norm_dist > 0.85:
                alpha = int(255 * (1.0 - (norm_dist - 0.85) / 0.15))

            pixels[x, y] = (int(r * 255), int(g * 255), int(b * 255), alpha)

    glow = img.filter(ImageFilter.GaussianBlur(radius=2.0))
    img = Image.blend(img, glow, alpha=0.15)

    return img


CASES = [
    # (hue, hue_range, saturation, brightness, time_val) - one per state preset
    (0.30, 0.08, 0.90, 0.95, 0.4),     # idle
    (0.13, 0.08, 0.90, 0.75, 7.5),     # loading (pulse)
    (0.98, 0.10, 0.92, 0.60, 123.0),   # recording, hue wraps
    (0.12, 0.10, 0.80, 0.95, 4000.0),  # transcribing, long uptime
]


@pytest.mark.parametrize("size", [32, 192])
@pytest.mark.parametrize("hue,hue_range,sat,bri,t", CASES)
def test_vectorized_sphere_matches_reference(size, hue, hue_range, sat, bri, t):
    ref = np.asarray(_reference_sphere(size, hue, hue_range, sat, bri, t), dtype=np.int16)
    new_img = ti._generate_marble_sphere(size, hue, hue_range=hue_range, saturation=sat,
                                         brightness=bri, time_val=t)
    assert new_img.mode == "RGBA" and new_img.size == (size, size)
    new = np.asarray(new_img, dtype=np.int16)
    diff = np.abs(ref - new)
    # float rounding at an int() truncation boundary may flip a channel by 1
    assert diff.max() <= 1
    assert (diff > 0).mean() < 0.001


def test_vectorized_sphere_is_cheap():
    ti._generate_marble_sphere(ti.ICON_SIZE, 0.3)  # warm-up
    n = 10
    start = time.perf_counter()
    for i in range(n):
        ti._generate_marble_sphere(ti.ICON_SIZE, 0.3, time_val=i * 0.4)
    per_frame = (time.perf_counter() - start) / n
    assert per_frame < 0.030, f"{per_frame * 1000:.1f} ms/frame"


def test_idle_frame_rate_is_capped():
    # The idle animation runs forever: its cadence is the idle CPU budget
    # (each frame is also an ICO file written by pystray). Before the numpy
    # renderer the 60-90 ms render throttled it to ~8 fps (measured live: ~8 ICO writes/s); keep it there.
    assert ti._ANIM_PRESETS["idle"]["interval"] >= 0.12
