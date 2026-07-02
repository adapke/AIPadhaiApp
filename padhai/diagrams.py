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
    title_font_path: str,  # noqa: ARG001
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
    title_font_path: str,  # noqa: ARG001
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
    title_font_path: str,  # noqa: ARG001
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
    x: int, y: int, w: int, h: int,  # noqa: ARG001
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
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
    title_font_path: str,  # noqa: ARG001
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


# ----------------------------- division_groups ------------------------- #

@register("division_groups")
def division_groups(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
    body_font_path: str,
) -> None:
    """Visualise '12 ÷ 3 = 4' as sharing: twelve dots shared equally into
    3 groups, four dots per group. The narration carries the real example;
    this is the always-correct illustration of what division *means*."""
    groups, per = 3, 4
    dot_r = 15
    cy = y + h // 2 - 18
    gap = max(24, w // 24)
    box_w = min(160, max(90, (w - (groups + 1) * gap) // groups))
    box_h = min(int(h * 0.55), 2 * (dot_r * 2 + 12) + 24)
    total_w = groups * box_w + (groups - 1) * gap
    start_x = x + (w - total_w) // 2

    for g in range(groups):
        bx = start_x + g * (box_w + gap)
        by = cy - box_h // 2
        draw.rectangle((bx, by, bx + box_w, by + box_h),
                       outline=theme.accent, width=3)
        dcx = bx + box_w // 2
        dcy = by + box_h // 2
        for i in range(per):
            row, col = divmod(i, 2)
            ddx = (col - 0.5) * (dot_r * 2 + 12)
            ddy = (row - 0.5) * (dot_r * 2 + 12)
            draw.ellipse(
                (dcx + ddx - dot_r, dcy + ddy - dot_r,
                 dcx + ddx + dot_r, dcy + ddy + dot_r),
                fill=(80, 130, 220),
            )
        _label(draw, "4", dcx, by + box_h + 6, body_font_path, 22, theme.fg)

    _label(draw, "12 ÷ 3 = 4", x + w // 2, y + h - 30,
           body_font_path, 30, theme.fg)


# ----------------------------- multiplication_array -------------------- #

@register("multiplication_array")
def multiplication_array(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
    body_font_path: str,
) -> None:
    """Visualise '3 × 4 = 12' as a rectangular array — 3 rows of 4 dots.
    An array is the clearest mental model of multiplication for kids."""
    rows, cols = 3, 4
    dot_r = 16
    step = dot_r * 2 + 14
    grid_w = (cols - 1) * step
    grid_h = (rows - 1) * step
    start_x = x + (w - grid_w) // 2
    start_y = y + (h - grid_h) // 2 - 16

    for r in range(rows):
        for c in range(cols):
            px = start_x + c * step
            py = start_y + r * step
            draw.ellipse((px - dot_r, py - dot_r, px + dot_r, py + dot_r),
                         fill=(90, 170, 90))

    # brace-ish labels: rows on the left, cols on top
    _label(draw, "3 rows", x + 70, start_y + grid_h // 2 - 12,
           body_font_path, 18, theme.fg)
    _label(draw, "4 in each row", start_x + grid_w // 2, start_y - 40,
           body_font_path, 18, theme.fg)
    _label(draw, "3 × 4 = 12", x + w // 2, y + h - 30,
           body_font_path, 30, theme.fg)


# ----------------------------- subtraction_dots ------------------------ #

@register("subtraction_dots")
def subtraction_dots(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
    body_font_path: str,
) -> None:
    """Visualise '5 − 2 = 3' as taking away: five dots, the last two crossed
    out, three remain. Take-away is how subtraction is first taught."""
    total, remove = 5, 2
    dot_r = 22
    cy = y + h // 2 - 10
    step = dot_r * 2 + 20
    row_w = (total - 1) * step
    start_x = x + (w - row_w) // 2

    for i in range(total):
        px = start_x + i * step
        taken = i >= (total - remove)
        draw.ellipse((px - dot_r, cy - dot_r, px + dot_r, cy + dot_r),
                     fill=(150, 150, 160) if taken else (80, 130, 220))
        if taken:
            # red cross to show "taken away"
            draw.line((px - dot_r, cy - dot_r, px + dot_r, cy + dot_r),
                      fill=(220, 70, 60), width=4)
            draw.line((px - dot_r, cy + dot_r, px + dot_r, cy - dot_r),
                      fill=(220, 70, 60), width=4)

    _label(draw, "5 − 2 = 3", x + w // 2, y + h - 30,
           body_font_path, 30, theme.fg)


# ----------------------------- fraction_circle ------------------------- #

@register("fraction_circle")
def fraction_circle(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
    body_font_path: str,
) -> None:
    """Visualise the fraction 3/4 — a circle cut into 4 equal slices with 3
    shaded. The canonical 'parts of a whole' picture."""
    parts, shaded = 4, 3
    r = min(w, h) // 2 - 46
    r = max(60, r)
    cx = x + w // 2
    cy = y + h // 2 - 12
    box = (cx - r, cy - r, cx + r, cy + r)

    for i in range(parts):
        start = i * (360 // parts) - 90
        end = start + (360 // parts)
        fill = theme.accent if i < shaded else theme.bg
        draw.pieslice(box, start, end, fill=fill, outline=theme.fg, width=3)

    _label(draw, "3/4", cx, y + h - 46, body_font_path, 32, theme.fg)
    _label(draw, "3 of 4 equal parts", cx, y + h - 20,
           body_font_path, 16, theme.muted)


def _dashed_line(
    draw: ImageDraw.ImageDraw,
    x1: int, y1: int, x2: int, y2: int,
    color: tuple[int, int, int], width: int = 2, dash: int = 10,
) -> None:
    """A dashed line — PIL has no native dash, so step along the segment."""
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return
    steps = max(1, int(length // dash))
    for s in range(0, steps, 2):
        t0 = s / steps
        t1 = min(1.0, (s + 1) / steps)
        draw.line(
            (x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
             x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1),
            fill=color, width=width,
        )


# ----------------------------- pythagoras ------------------------------ #

@register("pythagoras")
def pythagoras(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
    body_font_path: str,
) -> None:
    """Right triangle with legs a, b and hypotenuse c, plus a² + b² = c².
    The single most-drawn figure in secondary geometry."""
    m = 70
    base = min(360, w - 2 * m)
    height = min(210, h - 2 * m - 30)
    ax, ay = x + m, y + m + height           # right-angle corner
    bx, by = ax + base, ay                    # along the bottom
    cx2, cy2 = ax, ay - height                # up the left side

    draw.polygon([(ax, ay), (bx, by), (cx2, cy2)], fill=theme.accent)
    for p, q in [((ax, ay), (bx, by)), ((ax, ay), (cx2, cy2)),
                 ((bx, by), (cx2, cy2))]:
        draw.line((p[0], p[1], q[0], q[1]), fill=theme.fg, width=3)

    sq = 18  # right-angle marker
    draw.rectangle((ax, ay - sq, ax + sq, ay), outline=theme.fg, width=2)

    _label(draw, "b", (ax + bx) // 2, ay + 10, body_font_path, 22, theme.fg)
    _label(draw, "a", ax - 28, (ay + cy2) // 2 - 12, body_font_path, 22, theme.fg)
    _label(draw, "c", (bx + cx2) // 2 + 14, (by + cy2) // 2 - 22,
           body_font_path, 22, theme.fg)
    _label(draw, "a² + b² = c²", x + w // 2, y + h - 28,
           body_font_path, 30, theme.fg)


# ----------------------------- triangle_area --------------------------- #

@register("triangle_area")
def triangle_area(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
    body_font_path: str,
) -> None:
    """A triangle with base b and a dashed height h, plus Area = ½ × b × h."""
    m = 70
    base = min(360, w - 2 * m)
    ax, ay = x + m, y + h - m - 34            # base-left
    bx, by = ax + base, ay                    # base-right
    apex_x = ax + int(base * 0.62)            # off-centre apex
    apex_y = y + m

    draw.polygon([(ax, ay), (bx, by), (apex_x, apex_y)], fill=theme.accent)
    for p, q in [((ax, ay), (bx, by)), ((ax, ay), (apex_x, apex_y)),
                 ((bx, by), (apex_x, apex_y))]:
        draw.line((p[0], p[1], q[0], q[1]), fill=theme.fg, width=3)

    # dashed perpendicular height from apex down to the base
    _dashed_line(draw, apex_x, apex_y, apex_x, ay, theme.fg, width=2)
    draw.rectangle((apex_x, ay - 14, apex_x + 14, ay), outline=theme.fg, width=2)

    _label(draw, "b", (ax + bx) // 2, ay + 10, body_font_path, 22, theme.fg)
    _label(draw, "h", apex_x + 22, (apex_y + ay) // 2, body_font_path, 22, theme.fg)
    _label(draw, "Area = ½ × b × h", x + w // 2, y + h - 26,
           body_font_path, 28, theme.fg)


# ----------------------------- number_line ----------------------------- #

@register("number_line")
def number_line(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
    body_font_path: str,
) -> None:
    """A −4…4 number line with arrowheads, ticks, and one highlighted point
    at +2. The mental model for integers / negative numbers / ordering."""
    cy = y + h // 2
    x0, x1 = x + 70, x + w - 70
    draw.line((x0, cy, x1, cy), fill=theme.fg, width=3)
    _arrow(draw, x0 + 30, cy, x0, cy, theme.fg)      # left arrowhead
    _arrow(draw, x1 - 30, cy, x1, cy, theme.fg)      # right arrowhead

    values = list(range(-4, 5))
    n = len(values)
    highlight = 2
    for i, val in enumerate(values):
        tx = int(x0 + i * (x1 - x0) / (n - 1))
        draw.line((tx, cy - 12, tx, cy + 12), fill=theme.fg, width=2)
        _label(draw, str(val), tx, cy + 18, body_font_path, 18, theme.fg)
        if val == highlight:
            draw.ellipse((tx - 11, cy - 11, tx + 11, cy + 11), fill=theme.accent)

    _label(draw, "Number line", x + w // 2, y + h - 26,
           body_font_path, 26, theme.fg)


# ----------------------------- linear_graph ---------------------------- #

@register("linear_graph")
def linear_graph(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
    title_font_path: str,  # noqa: ARG001
    body_font_path: str,
) -> None:
    """x–y axes with a straight line through the origin (y = x) and two marked
    points — the picture behind linear equations / slope / coordinate geometry."""
    m = 56
    ox, oy = x + m + 30, y + h - m - 26        # origin (bottom-left area)
    ax_right = x + w - m
    ax_top = y + m

    # axes with arrowheads
    draw.line((ox, oy, ax_right, oy), fill=theme.fg, width=3)   # x-axis
    draw.line((ox, oy, ox, ax_top), fill=theme.fg, width=3)     # y-axis
    _arrow(draw, ax_right - 30, oy, ax_right, oy, theme.fg)
    _arrow(draw, ox, ax_top + 30, ox, ax_top, theme.fg)
    _label(draw, "x", ax_right - 6, oy + 12, body_font_path, 20, theme.fg)
    _label(draw, "y", ox - 22, ax_top, body_font_path, 20, theme.fg)

    # line y = x from origin up-right (equal pixel run/rise for a 45° look)
    span = min(ax_right - ox, oy - ax_top) - 20
    draw.line((ox, oy, ox + span, oy - span), fill=theme.accent, width=4)

    # two marked points on the line
    for frac in (0.45, 0.9):
        px, py = int(ox + span * frac), int(oy - span * frac)
        draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=theme.fg)

    _label(draw, "y = x", ox + span - 40, oy - span - 6,
           body_font_path, 22, theme.accent)
    _label(draw, "Straight-line graph", x + w // 2, y + h - 22,
           body_font_path, 24, theme.fg)
