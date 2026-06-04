"""J2 — Procedural diagram generator (Mermaid + custom SVG).

Existing `padhai/diagrams.py` has in-render PNG/Cairo templates the
renderer uses for fixed-layout slides. This module is the v1.5
addition: a procedural generator that takes a Claude-emitted spec
(`mermaid`, `svg_spec`, or `latex_tikz`) and produces SVG to embed
in slides + HTML.

Catalog of supported diagram types:
  - flowchart       (Mermaid)
  - sequence        (Mermaid)
  - cycle           (custom SVG)
  - bar_chart       (custom SVG)
  - tree            (Mermaid)
  - food_chain      (custom SVG)

External deps:
  - mermaid-cli (`mmdc`) — for Mermaid → SVG. Optional; gated on
    `MERMAID_BIN` env var.
  - The custom SVG paths have no deps — pure Python string templating.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_TYPES = (
    "flowchart", "sequence", "cycle", "bar_chart",
    "tree", "food_chain",
)


@dataclass(frozen=True)
class GeneratedDiagram:
    spec_lang: str        # 'mermaid' | 'svg_spec'
    spec: str             # the actual spec content (string)
    alt_text: str         # accessibility + TTS fallback
    diagram_type: str = "flowchart"
    width: int = 800
    height: int = 600


def is_mermaid_available() -> bool:
    binp = os.environ.get("MERMAID_BIN")
    return bool(binp) and os.path.exists(binp)


def render_mermaid(spec: str, *, width: int = 800, height: int = 600) -> str:
    if not is_mermaid_available():
        raise RuntimeError("MERMAID_BIN not configured")
    binp = os.environ["MERMAID_BIN"]
    with tempfile.NamedTemporaryFile(suffix=".mmd", mode="w", delete=False) as fin:
        fin.write(spec)
        in_path = fin.name
    out_path = in_path + ".svg"
    try:
        subprocess.run(
            [binp, "-i", in_path, "-o", out_path,
             "-w", str(width), "-H", str(height)],
            check=True, capture_output=True, timeout=15,
        )
        return Path(out_path).read_text(encoding="utf-8")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"mermaid render failed: {e.stderr.decode()}") from e
    finally:
        for p in (in_path, out_path):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p)


# ---------- Custom SVG renderers (no external deps) ----------

def render_cycle(items: list[str], *, width: int = 600, height: int = 400,
                 color: str = "#5E60CE") -> str:
    """Circular flow — 3-8 nodes evenly spaced, arrows clockwise."""
    import math
    n = max(3, min(len(items), 8))
    cx, cy = width / 2, height / 2
    radius = min(width, height) / 2.5
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<defs><marker id="ah" markerWidth="10" markerHeight="7" refX="10" '
        'refY="3.5" orient="auto">'
        '<polygon points="0 0, 10 3.5, 0 7" fill="#374151"/></marker></defs>',
    ]
    positions = []
    for i in range(n):
        angle = (2 * math.pi * i / n) - math.pi / 2
        positions.append((cx + radius * math.cos(angle),
                          cy + radius * math.sin(angle)))
    for i in range(n):
        x1, y1 = positions[i]
        x2, y2 = positions[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        d = max(1.0, (dx * dx + dy * dy) ** 0.5)
        ux, uy = dx / d, dy / d
        sx, sy = x1 + ux * 42, y1 + uy * 42
        ex, ey = x2 - ux * 42, y2 - uy * 42
        parts.append(
            f'<line x1="{sx:.0f}" y1="{sy:.0f}" x2="{ex:.0f}" y2="{ey:.0f}" '
            f'stroke="#374151" stroke-width="2" marker-end="url(#ah)"/>',
        )
    for i, (x, y) in enumerate(positions):
        label = (items[i] if i < len(items) else f"Step {i+1}")[:18]
        parts.append(
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="38" fill="{color}" '
            f'stroke="#1F2937" stroke-width="2"/>'
            f'<text x="{x:.0f}" y="{y+5:.0f}" font-family="sans-serif" '
            f'font-size="14" fill="white" text-anchor="middle">'
            f'{_escape(label)}</text>',
        )
    parts.append("</svg>")
    return "".join(parts)


def render_bar_chart(data: list[tuple[str, float]], *,
                     width: int = 600, height: int = 400,
                     color: str = "#5E60CE") -> str:
    if not data:
        return '<svg xmlns="http://www.w3.org/2000/svg"/>'
    pad_l, pad_b, pad_t = 60, 50, 30
    chart_w = width - pad_l - 30
    chart_h = height - pad_t - pad_b
    max_v = max(v for _, v in data) or 1
    bar_w = chart_w / len(data) * 0.7
    gap = chart_w / len(data) * 0.3
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{height - pad_b}" '
        f'stroke="#9CA3AF"/>',
        f'<line x1="{pad_l}" y1="{height - pad_b}" x2="{width - 20}" '
        f'y2="{height - pad_b}" stroke="#9CA3AF"/>',
    ]
    for i, (label, val) in enumerate(data):
        bx = pad_l + gap / 2 + i * (bar_w + gap)
        bh = (val / max_v) * chart_h
        by = height - pad_b - bh
        parts.append(
            f'<rect x="{bx:.0f}" y="{by:.0f}" width="{bar_w:.0f}" '
            f'height="{bh:.0f}" fill="{color}"/>'
            f'<text x="{bx + bar_w/2:.0f}" y="{by - 4:.0f}" '
            f'font-family="sans-serif" font-size="11" text-anchor="middle" '
            f'fill="#1F2937">{val:g}</text>'
            f'<text x="{bx + bar_w/2:.0f}" y="{height - pad_b + 18:.0f}" '
            f'font-family="sans-serif" font-size="12" text-anchor="middle" '
            f'fill="#374151">{_escape(label[:12])}</text>',
        )
    parts.append("</svg>")
    return "".join(parts)


def render_food_chain(species: list[str], *,
                      width: int = 700, height: int = 200,
                      color: str = "#10B981") -> str:
    n = max(2, min(len(species), 6))
    box_w = (width - 80) / n - 20
    box_h = 80
    y = (height - box_h) / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<defs><marker id="fc" markerWidth="10" markerHeight="7" '
        'refX="10" refY="3.5" orient="auto">'
        '<polygon points="0 0, 10 3.5, 0 7" fill="#1F2937"/></marker></defs>',
    ]
    for i in range(n):
        x = 30 + i * (box_w + 20)
        label = (species[i] if i < len(species) else f"Level {i+1}")[:14]
        parts.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{box_w:.0f}" '
            f'height="{box_h:.0f}" fill="{color}" stroke="#064E3B" '
            f'stroke-width="2" rx="8"/>'
            f'<text x="{x + box_w/2:.0f}" y="{y + box_h/2 + 5:.0f}" '
            f'font-family="sans-serif" font-size="14" fill="white" '
            f'text-anchor="middle">{_escape(label)}</text>',
        )
        if i < n - 1:
            ax = x + box_w + 2
            ay = y + box_h / 2
            parts.append(
                f'<line x1="{ax:.0f}" y1="{ay:.0f}" x2="{ax + 16:.0f}" '
                f'y2="{ay:.0f}" stroke="#1F2937" stroke-width="2.5" '
                f'marker-end="url(#fc)"/>',
            )
    parts.append("</svg>")
    return "".join(parts)


# ---------- Master entry ----------

def render(d: GeneratedDiagram) -> str:
    try:
        if d.spec_lang == "mermaid" and is_mermaid_available():
            return render_mermaid(d.spec, width=d.width, height=d.height)
        if d.spec_lang == "svg_spec":
            spec = json.loads(d.spec) if isinstance(d.spec, str) else d.spec
            t = spec.get("type", d.diagram_type)
            if t == "cycle":
                return render_cycle(spec.get("items") or [],
                                    width=d.width, height=d.height)
            if t == "bar_chart":
                rows = [(r["label"], float(r["value"]))
                        for r in spec.get("data") or []]
                return render_bar_chart(rows, width=d.width, height=d.height)
            if t == "food_chain":
                return render_food_chain(spec.get("species") or [],
                                         width=d.width, height=d.height)
    except Exception as e:  # noqa: BLE001
        print(f"[diagram_generator] render failed: {e}; using placeholder")
    return _fallback_svg(d.alt_text, width=d.width, height=d.height)


def _fallback_svg(alt: str, *, width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="#F3F4F6" '
        f'stroke="#D1D5DB" stroke-width="2" stroke-dasharray="6,4"/>'
        f'<text x="50%" y="50%" font-family="sans-serif" font-size="16" '
        f'fill="#4B5563" text-anchor="middle">{_escape(alt[:80])}</text>'
        f'</svg>'
    )


def _escape(s: str) -> str:
    import html
    return html.escape(s)


def normalize(d) -> GeneratedDiagram:
    if isinstance(d, dict):
        return GeneratedDiagram(
            spec_lang=d.get("spec_lang", "svg_spec"),
            spec=d.get("spec", "{}"),
            alt_text=d.get("alt_text", "diagram"),
            diagram_type=d.get("diagram_type", "flowchart"),
            width=int(d.get("width", 800)),
            height=int(d.get("height", 600)),
        )
    return d
