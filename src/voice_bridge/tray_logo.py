"""Flat monochrome tray logos: the equaliser (default, as in the README), C inside C holding a P, the wave.

Each logo is a few polylines in a 100x100 space; a state is a motion applied to
them (rotation, flip, dash flow, tick lengths). frame() renders one supersampled
frame; frame_count() says how many frames make one seamless loop of a state.
Idle is always frame 0 of a still pose: the animator renders it once and sleeps.
The look matches .work/logo-mock/logo-ccp.html (the mock the user picked from).
"""
import math

from .i18n import N_

DEFAULT = "bars"
STYLES = (  # id, label shown in the panel
    ("bars", N_("Equalizzatore")),
    ("wave", N_("Onda")),
    ("rings", N_("Anelli")),
    ("mech", N_("Meccanica")),
    ("mech_soft", N_("Meccanica smussata")),
    ("gyro", N_("Giroscopio")),
    ("alien", N_("Alieno")),
    ("marble", N_("Sfera classica")),
)
STYLE_IDS = tuple(style for style, _label in STYLES)
SUPERSAMPLE = 4
STATES = ("idle", "loading", "recording", "transcribing", "output")

# Frames per loop (0.12 s each). Bounded: the animator caches every frame of a loop.
_FRAMES = {"idle": 1, "recording": 12, "transcribing": 12, "loading": 24, "output": 8}


def frame_count(style, state):
    if state not in _FRAMES:
        state = "idle"
    if style in ("mech", "mech_soft", "gyro", "wave", "bars") and state in ("recording", "loading"):
        return 16 if state == "recording" else 20
    return _FRAMES[state]


def color_for(dark_taskbar):
    return (255, 255, 255) if dark_taskbar else (26, 26, 26)


# ── geometry ────────────────────────────────────────────────────────

def _arc(r, a0, a1, step=4.0):
    """Points along a circle (degrees, screen coordinates: clockwise)."""
    n = max(2, int(abs(a1 - a0) / step) + 1)
    return [(50 + r * math.cos(math.radians(a0 + (a1 - a0) * i / (n - 1))),
             50 + r * math.sin(math.radians(a0 + (a1 - a0) * i / (n - 1)))) for i in range(n)]


def _c(r, gap, turn=0.0):
    return _arc(r, gap / 2 + turn, 360 - gap / 2 + turn)


def _octagon(r, turn=0.0):
    return [(50 + r * math.cos(math.radians(22.5 + 45 * k + turn)),
             50 + r * math.sin(math.radians(22.5 + 45 * k + turn))) for k in range(8)]


def _p_round():
    return [(43, 66), (43, 34), (53, 34)] + _arc_at(53, 42.5, 8.5, -90, 90) + [(43, 51)]


def _p_square():
    return [(43, 66), (43, 34), (55, 34), (60, 39), (60, 46), (55, 51), (43, 51)]


def _p_soft():
    return ([(43, 66), (43, 34), (54, 34)] + _arc_at(54, 40, 6, -90, 0) + _arc_at(54, 45, 6, 0, 90)
            + [(43, 51)])


def _arc_at(cx, cy, r, a0, a1, step=10.0):
    return [(x - 50 + cx, y - 50 + cy) for x, y in _arc(r, a0, a1, step)]


def _transform(points, rotate=0.0, sx=1.0, sy=1.0, cx=50.0, cy=50.0):
    cos, sin = math.cos(math.radians(rotate)), math.sin(math.radians(rotate))
    out = []
    for x, y in points:
        x, y = (x - cx) * sx, (y - cy) * sy
        out.append((cx + x * cos - y * sin, cy + x * sin + y * cos))
    return out


def _dashes(r, gap, pattern, offset, turn=0.0):
    """Dashed C: pieces of the arc that fall on the 'on' parts of *pattern* shifted by *offset*."""
    start, end = gap / 2 + turn, 360 - gap / 2 + turn
    length = math.radians(end - start) * r
    period = sum(pattern)
    pieces, position = [], -(offset % period)
    index = 0
    while position < length:
        size = pattern[index % len(pattern)]
        if index % 2 == 0:
            a, b = max(position, 0), min(position + size, length)
            if b > a:
                pieces.append(_arc(r, start + math.degrees(a / r), start + math.degrees(b / r), 3))
        position += size
        index += 1
    return pieces


# ── motion ──────────────────────────────────────────────────────────

def _ease(x):
    return 1 - (1 - x) ** 3


def _back(x):
    c = 1.9
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def _shapes(style, state, t):
    """[(points, width, round_caps, alpha)] and [(cx, cy, r, alpha)] for one instant t in [0, 1)."""
    lines, dots = [], []
    if style == "wave":
        ring_turn = 32 * math.floor(t * 10) if state == "loading" else 0
        # Fills the tray slot: tick ends (with round caps) reach the canvas edge at rest;
        # recording only shortens ticks, so the equaliser never clips.
        core, inner, length, width = 15.0, 25.0, 19.0, 11.0
        for i in range(10):
            angle = 36 + 32 * i + ring_turn
            start, outer, alpha = inner, inner + length, 1.0
            if state == "recording":
                outer = inner + length * (0.4 + 0.6 * (0.5 + 0.5 * math.sin(math.tau * (t + i * 0.2))))
            elif state == "transcribing":
                d = (t - i / 10) % 1.0
                alpha = 1.0 if d < 0.15 else 0.3
            elif state == "output":
                d = min(1.0, max(0.0, t * 1.15 - i * 0.012))
                if d < 0.6:
                    p = _ease(d / 0.6)
                    start, outer, alpha = inner + 8 * p, inner + length + 8 * p, 1 - p
                else:
                    p = _ease((d - 0.6) / 0.4)
                    outer, alpha = inner + length * p, p
            if alpha > 0.02:
                lines.append((_transform([(50 + start, 50), (50 + outer, 50)], angle), width, True, alpha))
        if state == "recording":
            core = 15 * (1 + 0.22 * (0.5 + 0.5 * math.sin(math.tau * t * 2)))
        dots.append((50, 50, core, 1.0))
        return lines, dots

    if style == "bars":
        return _bars(state, t), dots

    outer_rot = inner_rot = 0.0
    outer_s = inner_s = (1.0, 1.0)
    p_scale = 1.0
    if style == "rings":
        if state == "recording":
            outer_rot, inner_rot = 360 * t, -720 * t
        elif state == "transcribing":
            outer_rot, inner_rot = 360 * t, 720 * t
        elif state == "loading":
            outer_rot = 360 * t
        elif state == "output":
            outer_rot = inner_rot = 360 * _ease(min(1.0, t * 1.4))
    elif style in ("mech", "mech_soft"):
        soft = style == "mech_soft"

        def steps(count, x):
            whole = math.floor(x * count)
            if not soft:
                return 45 * whole
            return 45 * (whole + _back(min(1.0, (x * count - whole) * 2)))
        if state == "recording":
            inner_rot = steps(8, t)
            # outer C rocks one step forth and back per loop
            outer_rot = 45 * (0.5 - 0.5 * math.cos(math.tau * t)) if soft else 45 * float(0.25 <= t < 0.75)
        elif state == "transcribing":
            outer_rot, inner_rot = steps(8, t), -steps(8, t)
        elif state == "loading":
            outer_rot, inner_rot = steps(4, t), -steps(4, t)
        elif state == "output":
            outer_rot = steps(4, t) if not soft else 360 * _ease(min(1.0, t * 1.4))
            inner_rot = 0 if not soft else outer_rot
    elif style == "gyro":
        wave = math.cos(math.tau * t)
        if state == "recording":
            outer_s, inner_s = (wave, 1.0), (1.0, wave)
            p_scale = 1 - 0.08 * (0.5 - 0.5 * math.cos(math.tau * t * 2))
        elif state == "transcribing":
            outer_rot, inner_s = 360 * t, (math.cos(math.tau * t * 2), 1.0)
        elif state == "loading":
            outer_rot, inner_rot = 18 * math.sin(math.tau * t), -18 * math.sin(math.tau * t)
        elif state == "output":
            outer_s = (1.0, math.cos(math.tau * t))
    elif style == "alien":
        flow = {"recording": 1, "transcribing": 3, "output": 2}.get(state, 0)
        outer = _dashes(42, 54, (38, 7, 4, 7, 64, 9, 12, 7), 148 * flow * t)
        inner = _dashes(27, 80, (22, 6, 3, 6, 40, 8), -85 * flow * t, turn=180)
        wobble = 18 * math.sin(math.tau * t) if state == "loading" else 0
        lines += [(_transform(piece, wobble), 8, True, 1.0) for piece in outer]
        lines += [(piece, 6, True, 1.0) for piece in inner]
        if state in ("recording", "transcribing"):
            spin = 360 * t * (1 if state == "recording" else -2)
            for (x, y), r in zip(_transform([(50, 13), (50, 87)], spin), (3.6, 2.4)):
                dots.append((x, y, r, 1.0))
        blink = 1.0
        if state in ("loading", "output"):
            phase = (t * (1 if state == "loading" else 2)) % 1
            blink = 0.12 if 0.42 < phase < 0.58 else 1.0
        lines.append(([(44, 67), (44, 36)], 6.5, True, 1.0))
        eye = [(44, 36)] + _quad((44, 36), (60, 32), (62, 43)) + _quad((62, 43), (60, 54), (44, 51))
        lines.append((_transform(eye, sy=blink, cx=54, cy=43), 6, True, 1.0))
        dots.append((53, 43 + (43.5 - 43) * blink, 2.6 * max(blink, 0.4), 1.0))
        return lines, dots

    if style in ("mech", "mech_soft"):
        outer, inner = _octagon(42), _octagon(27, 180)
        letter = _p_square() if style == "mech" else _p_soft()
        widths = (9, 7.5, 7) if style == "mech" else (10, 8, 7.5)
        caps = style == "mech_soft"
    else:
        outer, inner = _c(42, 70), _c(27, 90, 180 if style == "gyro" else 0)
        letter = _p_round()
        widths = (10, 8, 7) if style == "rings" else (6, 5, 6.5)
        caps = True
    lines.append((_transform(outer, outer_rot, *outer_s), widths[0], caps, 1.0))
    lines.append((_transform(inner, inner_rot, *inner_s), widths[1], caps, 1.0))
    lines.append((_transform(letter, sx=p_scale, sy=p_scale), widths[2], caps, 1.0))
    return lines, dots


# Equaliser, 8 vertical bars (README hero). Talking-car voice box: bars pulse
# symmetrically from the centre while recording, a light sweeps back and forth
# while transcribing.
_BARS = (0.30, 0.62, 1.0, 0.72, 0.37, 0.69, 0.47, 0.25)
_BAR_STEP, _BAR_WIDTH, _BAR_SPAN = 12.4, 8.6, 88.0


def _bars(state, t):
    lines = []
    count = len(_BARS)
    for i, base in enumerate(_BARS):
        x = 50 + (i - (count - 1) / 2) * _BAR_STEP
        height, alpha = base, 1.0
        if state == "recording":
            centre = abs(i - (count - 1) / 2) / ((count - 1) / 2)  # 0 middle, 1 edges
            level = 0.5 + 0.5 * math.sin(math.tau * (2 * t - 0.35 * centre))
            height = max(0.14, (1.0 - 0.55 * centre) * (0.25 + 0.75 * level))
        elif state == "transcribing":
            sweep = (count - 1) * (1 - abs(1 - 2 * t))  # 0 -> 7 -> 0
            alpha = max(0.25, 1 - abs(i - sweep) * 0.38)
            height = max(base, 0.45) if alpha > 0.6 else base
        elif state == "loading":
            height = base * (0.55 + 0.45 * (0.5 + 0.5 * math.sin(math.tau * (t - i / count))))
        elif state == "output":
            position = -1.5 + (count + 2) * _ease(t)  # a ripple runs left to right
            height = base + (1 - base) * max(0.0, 1 - abs(i - position) / 1.5)
        half = max(height * _BAR_SPAN / 2 - _BAR_WIDTH / 2, 0.01)
        lines.append(([(x, 50 - half), (x, 50 + half)], _BAR_WIDTH, True, alpha))
    return lines


def _quad(p0, p1, p2, n=8):
    return [((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u ** 2 * p2[0],
             (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u ** 2 * p2[1])
            for u in (i / n for i in range(1, n + 1))]


# ── rendering ───────────────────────────────────────────────────────

def frame(style, state, index, size=64, color=(255, 255, 255)):
    """One RGBA frame of *style* in *state*; *index* wraps on frame_count()."""
    from PIL import Image, ImageDraw
    if state not in _FRAMES:
        state = "idle"
    count = frame_count(style, state)
    t = (index % count) / count
    big = size * SUPERSAMPLE
    k = big / 100
    image = Image.new("RGBA", (big, big), color + (0,))
    lines, dots = _shapes(style, state, t)
    for points, width, round_caps, alpha in lines:
        layer_color = color + (round(255 * alpha),)
        draw = ImageDraw.Draw(image)
        scaled = [(x * k, y * k) for x, y in points]
        w = max(1, round(width * k))
        draw.line(scaled, fill=layer_color, width=w, joint="curve")
        if round_caps:
            for x, y in (scaled[0], scaled[-1]):
                draw.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=layer_color)
    for x, y, r, alpha in dots:
        ImageDraw.Draw(image).ellipse(((x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k),
                                      fill=color + (round(255 * alpha),))
    return image.resize((size, size), Image.LANCZOS)
