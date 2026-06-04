"""Diagram template registry.

Each diagram is a function that draws a labelled illustration into a given
bounding box on a slide. Diagrams are referenced by name from a Scene's
optional `diagram` field; render.py looks them up here.

Templates today: solar_system, photosynthesis, water_cycle, atom.

Why templates rather than LLM-generated SVG: hand-drawn templates always
render correctly, look consistent across the catalogue, and are cheap
(no extra Claude call). Long-tail diagrams are a future upgrade —
either generated SVG via Claude + cairosvg, or generated frames via the
code-execution tool with matplotlib."""

from __future__ import annotations

import math
from collections.abc import Callable

from PIL import ImageDraw, ImageFont

from .themes import Theme

DiagramFn = Callable[
    [ImageDraw.ImageDraw, int, int, int, int, Theme, str, str], None
]


REGISTRY: dict[str, DiagramFn] = {}


def register(name: str) -> Callable[[DiagramFn], DiagramFn]:
    def deco(fn: DiagramFn) -> DiagramFn:
        REGISTRY[name] = fn
        return fn
    return deco


def get(name: str) -> DiagramFn | None:
    return REGISTRY.get(name)


def _label(
    draw: ImageDraw.ImageDraw,
    text: str,
    cx: int, cy: int,
    body_font_path: str, size: int,
    fill: tuple[int, int, int],
) -> None:
    """Center a label horizontally on (cx, cy)."""
    font = ImageFont.truetype(body_font_path, size)
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((cx - w // 2, cy), text, fill=fill, font=font)


# ----------------------------- solar_system ----------------------------- #

@register("solar_system")
def solar_system(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,
    body_font_path: str,
) -> None:
    """Sun + 8 planets in a horizontal line, labelled, with relative sizes
    that read clearly (not strictly to scale — at true scale planets would
    be invisible next to the Sun)."""
    # planet definitions: (name, radius_px, color, rings?)
    planets = [
        ("Mercury", 8,  (170, 170, 170), False),
        ("Venus",   12, (220, 180, 100), False),
        ("Earth",   13, (60, 130, 220),  False),
        ("Mars",    10, (200, 90, 60),   False),
        ("Jupiter", 32, (210, 180, 140), False),
        ("Saturn",  26, (220, 195, 130), True),
        ("Uranus",  20, (150, 220, 220), False),
        ("Neptune", 19, (70, 110, 200),  False),
    ]
    sun_r = 55

    cy = y + h // 2 - 30   # leave room for labels below
    # Sun on the left with a soft glow ring
    sun_cx = x + sun_r + 20
    for k in range(6, 0, -1):
        glow = (255, 200, 80, max(0, 60 - k * 8))
        # PIL ellipse doesn't blend alpha in RGB mode — emulate with concentric solid rings
        draw.ellipse(
            (sun_cx - sun_r - k * 5, cy - sun_r - k * 5,
             sun_cx + sun_r + k * 5, cy + sun_r + k * 5),
            outline=(255, 200, 80), width=1,
        )
    draw.ellipse(
        (sun_cx - sun_r, cy - sun_r, sun_cx + sun_r, cy + sun_r),
        fill=(255, 200, 60),
    )
    # Sun label
    _label(draw, "Sun", sun_cx, cy + sun_r + 12, body_font_path, 22, theme.fg)

    # Planets spaced along the remaining width
    span_x = x + sun_r * 2 + 90
    end_x = x + w - 30
    n = len(planets)
    spacing = (end_x - span_x) / (n - 1) if n > 1 else 0

    for i, (name, r, color, has_rings) in enumerate(planets):
        px = int(span_x + i * spacing)
        py = cy
        # subtle "orbit dot" line — a faint dashed track from sun to planet
        draw.line((sun_cx + sun_r, cy, px - r, cy),
                  fill=theme.muted, width=1)
        # planet body
        draw.ellipse((px - r, py - r, px + r, py + r), fill=color)
        # rings for Saturn
        if has_rings:
            ring_w = int(r * 2.2)
            ring_h = int(r * 0.4)
            draw.ellipse(
                (px - ring_w, py - ring_h, px + ring_w, py + ring_h),
                outline=(230, 210, 170), width=3,
            )
        # label below
        _label(draw, name, px, py + r + 12, body_font_path, 18, theme.fg)


# ----------------------------- photosynthesis --------------------------- #

@register("photosynthesis")
def photosynthesis(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,
    body_font_path: str,
) -> None:
    """A leaf in the middle with CO₂ + water + sunlight arrows in, and
    glucose + O₂ arrows out. Scales to whatever bounding box the slide
    layout gives us — the whiteboard panel allocates ~260px of height
    which is less than the diagram's design size."""
    cx = x + w // 2

    # Scale leaf size to fit. Reserve ~40% of height for the leaf, the
    # remainder for stem + sun + labels.
    leaf_w = min(200, w // 3)
    leaf_h = max(70, min(130, int(h * 0.40)))
    # cy is the leaf centre, biased upwards so the stem has room below.
    cy = y + leaf_h // 2 + max(40, int(h * 0.10))

    leaf_box = (cx - leaf_w // 2, cy - leaf_h // 2,
                cx + leaf_w // 2, cy + leaf_h // 2)
    draw.ellipse(leaf_box, fill=(80, 160, 80))
    draw.line((cx - leaf_w // 2, cy, cx + leaf_w // 2, cy),
              fill=(50, 110, 50), width=3)

    # Sun in the top-right
    sun_x, sun_y, sr = x + w - 90, y + 50, min(38, max(24, h // 8))
    draw.ellipse((sun_x - sr, sun_y - sr, sun_x + sr, sun_y + sr),
                 fill=(255, 200, 60))
    for k in range(8):
        ang = k * math.pi / 4
        x1 = sun_x + int(math.cos(ang) * (sr + 6))
        y1 = sun_y + int(math.sin(ang) * (sr + 6))
        x2 = sun_x + int(math.cos(ang) * (sr + 22))
        y2 = sun_y + int(math.sin(ang) * (sr + 22))
        draw.line((x1, y1, x2, y2), fill=(255, 200, 60), width=3)
    _label(draw, "Sunlight", sun_x, sun_y + sr + 12, body_font_path, 18, theme.fg)
    draw.line((sun_x, sun_y + sr, cx + 30, cy - leaf_h // 2 - 4),
              fill=(255, 200, 60), width=3)

    # CO₂ in (left side)
    co2_y = max(y + 30, cy - leaf_h // 2 - 30)
    _label(draw, "CO2 in", x + 90, co2_y, body_font_path, 20, theme.fg)
    _arrow(draw, x + 130, co2_y + 24,
           cx - leaf_w // 2 - 6, cy - leaf_h // 4, theme.accent)

    # Stem + roots — only draw if there's vertical room
    stem_top = cy + leaf_h // 2
    label_y = y + h - 24
    stem_bottom = label_y - 30
    if stem_bottom > stem_top:
        draw.rectangle((cx - 8, stem_top, cx + 8, stem_bottom),
                       fill=(120, 90, 60))
        _label(draw, "Water from roots", cx, label_y - 18,
               body_font_path, 18, theme.fg)
        _arrow(draw, cx, stem_bottom, cx, stem_top + 2, theme.accent)

    # O₂ out (upper right)
    o2_y = co2_y + 70
    _label(draw, "O2 out", x + w - 110, o2_y, body_font_path, 20, theme.fg)
    _arrow(draw, cx + leaf_w // 2 + 4, cy - 10,
           x + w - 130, o2_y + 8, theme.accent)

    # Glucose out (lower right)
    glu_y = min(y + h - 50, cy + leaf_h // 2 + 30)
    _label(draw, "Glucose", x + w - 110, glu_y, body_font_path, 20, theme.fg)
    _arrow(draw, cx + leaf_w // 2 + 4, cy + 10,
           x + w - 130, glu_y + 4, theme.accent)


def _arrow(
    draw: ImageDraw.ImageDraw,
    x1: int, y1: int, x2: int, y2: int,
    color: tuple[int, int, int],
    width: int = 3,
) -> None:
    """Line + tiny triangle arrowhead at (x2, y2)."""
    draw.line((x1, y1, x2, y2), fill=color, width=width)
    ang = math.atan2(y2 - y1, x2 - x1)
    head = 10
    p1 = (x2, y2)
    p2 = (x2 - int(head * math.cos(ang - math.pi / 7)),
          y2 - int(head * math.sin(ang - math.pi / 7)))
    p3 = (x2 - int(head * math.cos(ang + math.pi / 7)),
          y2 - int(head * math.sin(ang + math.pi / 7)))
    draw.polygon([p1, p2, p3], fill=color)


# ----------------------------- water_cycle ------------------------------ #

@register("water_cycle")
def water_cycle(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,
    body_font_path: str,
) -> None:
    """Ocean → evaporation → cloud → rain → ocean, with the sun in the corner."""
    # Sun in top-left
    sun_x, sun_y, sr = x + 80, y + 60, 32
    draw.ellipse((sun_x - sr, sun_y - sr, sun_x + sr, sun_y + sr),
                 fill=(255, 200, 60))
    for k in range(8):
        ang = k * math.pi / 4
        x1 = sun_x + int(math.cos(ang) * (sr + 4))
        y1 = sun_y + int(math.sin(ang) * (sr + 4))
        x2 = sun_x + int(math.cos(ang) * (sr + 18))
        y2 = sun_y + int(math.sin(ang) * (sr + 18))
        draw.line((x1, y1, x2, y2), fill=(255, 200, 60), width=3)

    # Cloud in the centre-top
    cx, cy = x + w // 2 + 60, y + 120
    for dx, dy, rr in [(0, 0, 38), (-46, 8, 28), (44, 10, 30), (20, -20, 26)]:
        draw.ellipse((cx + dx - rr, cy + dy - rr, cx + dx + rr, cy + dy + rr),
                     fill=(230, 230, 240))
    _label(draw, "Cloud", cx, cy + 50, body_font_path, 20, theme.fg)

    # Ocean (bottom band)
    ocean_top = y + h - 80
    draw.rectangle((x, ocean_top, x + w, y + h), fill=(50, 110, 180))
    # wave lines
    for k in range(0, w, 60):
        draw.arc((x + k, ocean_top - 6, x + k + 60, ocean_top + 6),
                 start=0, end=180, fill=(120, 170, 220), width=2)
    _label(draw, "Ocean", x + w // 2, y + h - 36, body_font_path, 22, (240, 240, 245))

    # Evaporation arrow: ocean → cloud
    _arrow(draw, x + 250, ocean_top - 4, cx - 60, cy + 30, theme.accent)
    _label(draw, "Evaporation", x + 240, ocean_top - 50, body_font_path, 18, theme.fg)

    # Rain drops from cloud to ocean
    for k in range(5):
        rx = cx - 50 + k * 26
        ry_top = cy + 36
        ry_bot = ocean_top - 8
        draw.line((rx, ry_top, rx + 4, ry_bot), fill=(120, 170, 220), width=2)
    _label(draw, "Rain", cx + 130, cy + 110, body_font_path, 20, theme.fg)


# ----------------------------- atom ------------------------------------ #

@register("addition_dots")
def addition_dots(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,
    body_font_path: str,
) -> None:
    """Visualise '3 + 2 = 5' (or whichever sum is hard-coded here) with
    rows of coloured dots, the '+' sign, and the '=' sign. Kid-friendly."""
    # left group: 3 dots in blue
    # plus sign
    # right group: 2 dots in red
    # equals
    # answer: 5 dots in green
    left_n, right_n, total_n = 3, 2, 5
    dot_r = 22
    cy = y + h // 2

    # left group (centred in its column)
    col1_cx = x + 90
    for i in range(left_n):
        dy = (i - (left_n - 1) / 2) * (dot_r * 2 + 8)
        draw.ellipse(
            (col1_cx - dot_r, cy + dy - dot_r,
             col1_cx + dot_r, cy + dy + dot_r),
            fill=(80, 130, 220),
        )

    # plus sign
    plus_cx = col1_cx + 110
    bar = 8
    arm = 28
    draw.rectangle(
        (plus_cx - arm, cy - bar // 2, plus_cx + arm, cy + bar // 2),
        fill=theme.accent,
    )
    draw.rectangle(
        (plus_cx - bar // 2, cy - arm, plus_cx + bar // 2, cy + arm),
        fill=theme.accent,
    )

    # right group
    col2_cx = plus_cx + 110
    for i in range(right_n):
        dy = (i - (right_n - 1) / 2) * (dot_r * 2 + 8)
        draw.ellipse(
            (col2_cx - dot_r, cy + dy - dot_r,
             col2_cx + dot_r, cy + dy + dot_r),
            fill=(220, 90, 90),
        )

    # equals sign (two horizontal bars)
    eq_cx = col2_cx + 110
    draw.rectangle((eq_cx - arm, cy - 18, eq_cx + arm, cy - 10), fill=theme.accent)
    draw.rectangle((eq_cx - arm, cy + 10, eq_cx + arm, cy + 18), fill=theme.accent)

    # total group
    col3_cx = eq_cx + 110
    for i in range(total_n):
        dy = (i - (total_n - 1) / 2) * (dot_r * 2 + 6)
        draw.ellipse(
            (col3_cx - dot_r, cy + dy - dot_r,
             col3_cx + dot_r, cy + dy + dot_r),
            fill=(90, 170, 90),
        )

    # captions under each group
    _label(draw, "3",   col1_cx, y + h - 22, body_font_path, 28, theme.fg)
    _label(draw, "+",   plus_cx, y + h - 22, body_font_path, 28, theme.fg)
    _label(draw, "2",   col2_cx, y + h - 22, body_font_path, 28, theme.fg)
    _label(draw, "=",   eq_cx,   y + h - 22, body_font_path, 28, theme.fg)
    _label(draw, "5",   col3_cx, y + h - 22, body_font_path, 28, theme.fg)


@register("atom")
def atom(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,
    body_font_path: str,
) -> None:
    """Nucleus (protons + neutrons) with 2 electron shells. Bohr-style."""
    cx, cy = x + w // 2, y + h // 2 - 10

    # Two elliptical orbits at angles
    for k, (rx, ry, rot_deg) in enumerate([(180, 70, 0), (240, 90, 45)]):
        # PIL has no rotated-ellipse primitive; approximate with a polygon
        pts = []
        for t_deg in range(0, 360, 6):
            t = math.radians(t_deg)
            ex = math.cos(t) * rx
            ey = math.sin(t) * ry
            rot = math.radians(rot_deg)
            xr = ex * math.cos(rot) - ey * math.sin(rot)
            yr = ex * math.sin(rot) + ey * math.cos(rot)
            pts.append((cx + xr, cy + yr))
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            draw.line((x1, y1, x2, y2), fill=theme.muted, width=2)
        # electron on this orbit
        ang = math.radians(40 + k * 110)
        ex = math.cos(ang) * rx
        ey = math.sin(ang) * ry
        rot = math.radians(rot_deg)
        ex_r = ex * math.cos(rot) - ey * math.sin(rot)
        ey_r = ex * math.sin(rot) + ey * math.cos(rot)
        ex_x, ey_y = int(cx + ex_r), int(cy + ey_r)
        draw.ellipse((ex_x - 12, ey_y - 12, ex_x + 12, ey_y + 12),
                     fill=(80, 160, 255))
        _label(draw, "e⁻", ex_x, ey_y + 14, body_font_path, 16, theme.fg)

    # Nucleus: cluster of protons (+) and neutrons (0)
    for dx, dy, color, sym in [
        (-14, -8, (220, 70, 60), "+"),
        (14, -8, (220, 70, 60), "+"),
        (0, 10, (220, 70, 60), "+"),
        (-12, 14, (180, 180, 180), "0"),
        (14, 16, (180, 180, 180), "0"),
    ]:
        draw.ellipse((cx + dx - 14, cy + dy - 14, cx + dx + 14, cy + dy + 14),
                     fill=color)
        _label(draw, sym, cx + dx, cy + dy - 10, body_font_path, 14, (255, 255, 255))

    _label(draw, "Nucleus", cx, cy + 50, body_font_path, 18, theme.fg)
    _label(draw, "Electron shells", cx, cy + h // 2 - 30,
           body_font_path, 18, theme.fg)
