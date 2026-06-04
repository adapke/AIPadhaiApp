"""Render Lesson → MP4 via PIL slides + gTTS audio + ffmpeg.

Supports two rendering modes:
- "static": one slide per scene (fast, cheap, simpler look)
- "reveal": progressive bullet-by-bullet build-up timed to the narration
  (whiteboard-style)
And a Theme system that swaps colors, fonts, and decorations without
touching the layout logic."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from . import diagrams
from .audio_envelope import mouth_timeline
from .avatar import POSE_CYCLE, draw_teacher
from .pedagogy import Lesson, Scene
from .themes import DARK_ACADEMIC, Theme
from .tts import TTSProvider, get_provider

if TYPE_CHECKING:
    from .cache import Cache


def synthesise_audio(
    text: str,
    language_code: str,
    out_path: Path,
    cache: Cache | None = None,
    provider: TTSProvider | None = None,
) -> None:
    """Render `text` to an MP3 at out_path via the configured TTS provider.

    Cache-aware; cache entries are keyed by provider name so gTTS and
    Bhashini outputs don't collide."""
    provider = provider or get_provider()
    if cache is not None and cache.get_audio(text, language_code, provider.name, out_path):
        return
    provider.synthesise(text, language_code, out_path)
    if cache is not None:
        cache.put_audio(text, language_code, provider.name, out_path)

# Render canvas dimensions — module-level so all the slide-drawing helpers
# can read them without threading W/H through every signature. The runtime
# default is 16:9 1280×720 (PRD §6.7 "16:9 YouTube/classroom"). Callers
# can change it via `set_canvas_dimensions()` before calling render_lesson()
# to produce 9:16 Reels (720×1280) or 1:1 social (720×720).
WIDTH, HEIGHT = 1280, 720


def set_canvas_dimensions(width: int, height: int) -> None:
    """Override the render canvas size for the next render_lesson() call.
    Used by /api/v2/video-requests to honour profile.output_dimensions."""
    global WIDTH, HEIGHT
    WIDTH, HEIGHT = int(width), int(height)


def _find_font(language_code: str, theme: Theme = DARK_ACADEMIC) -> tuple[str, str]:
    """Pick title + body font paths.

    Priority: theme's preferred fonts (if installed) → language-script Noto
    fonts (for Indic etc.) → DejaVu fallback."""
    candidates_by_lang = {
        # Order matters: Noto first (best for production), then commonly
        # pre-installed open-source Indic fonts (Lohit / Samyak / Annapurna)
        # so renders work even when Noto isn't available (e.g. minimal
        # sandboxes or distros that don't ship Noto by default).
        "hi": ["NotoSansDevanagari", "NotoSerifDevanagari",
               "Lohit Devanagari", "Samyak Devanagari", "Annapurna"],
        "mr": ["NotoSansDevanagari", "NotoSerifDevanagari",
               "Lohit Devanagari", "Samyak Devanagari"],
        "ta": ["NotoSansTamil", "NotoSerifTamil", "Lohit Tamil"],
        "te": ["NotoSansTelugu", "NotoSerifTelugu", "Lohit Telugu"],
        "kn": ["NotoSansKannada", "NotoSerifKannada", "Lohit Kannada"],
        "bn": ["NotoSansBengali", "NotoSerifBengali", "Lohit Bengali"],
        "gu": ["NotoSansGujarati", "NotoSerifGujarati", "Lohit Gujarati"],
        "pa": ["NotoSansGurmukhi", "NotoSerifGurmukhi", "Lohit Punjabi"],
        "ml": ["NotoSansMalayalam", "NotoSerifMalayalam", "Lohit Malayalam"],
    }
    fallback = ["DejaVuSans-Bold", "DejaVuSans"]

    # If a theme has preferred fonts AND the script isn't an Indic script
    # that needs Noto, try the theme fonts first.
    script_needs_noto = language_code in candidates_by_lang
    title_pref = (
        list(theme.title_font_keywords)
        + candidates_by_lang.get(language_code, [])
        + fallback
    )
    body_pref = (
        list(theme.body_font_keywords)
        + candidates_by_lang.get(language_code, [])
        + fallback
    )
    if script_needs_noto:
        # Indic scripts need Noto fonts to render glyphs at all — promote them.
        title_pref = (
            candidates_by_lang.get(language_code, [])
            + list(theme.title_font_keywords)
            + fallback
        )
        body_pref = title_pref

    found = subprocess.run(
        ["fc-list", ":lang=" + language_code if language_code != "en" else ""],
        capture_output=True, text=True,
    ).stdout

    def pick(keywords: list[str]) -> str:
        for kw in keywords:
            for line in found.splitlines() or []:
                if kw.lower() in line.lower() and ".ttf" in line.lower():
                    return line.split(":")[0].strip()
        # broad fallback
        for kw in keywords:
            for line in subprocess.run(
                ["fc-list"], capture_output=True, text=True
            ).stdout.splitlines():
                if kw.lower() in line.lower() and ".ttf" in line.lower():
                    return line.split(":")[0].strip()
        return ""

    title_path = pick(title_pref) or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    body_path = pick(body_pref) or "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return title_path, body_path


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if font.getlength(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_quiz_slide(
    question: dict,
    q_num: int,
    q_total: int,
    title_font_path: str,
    body_font_path: str,
    lesson_title: str,
    reveal_answer: bool,
    theme: Theme = DARK_ACADEMIC,
) -> Image.Image:
    """Render a quiz card. If reveal_answer is True, highlight the correct
    option in theme.accent."""
    img = Image.new("RGB", (WIDTH, HEIGHT), theme.bg)
    draw = ImageDraw.Draw(img)
    if theme.show_accent_stripe:
        draw.rectangle((0, 0, 12, HEIGHT), fill=theme.accent)
    if theme.show_emoji:
        _draw_kg_decorations(draw, theme)

    header_font = ImageFont.truetype(body_font_path, theme.header_size)
    badge_font = ImageFont.truetype(title_font_path, 32)
    question_font = ImageFont.truetype(title_font_path, 36)
    option_font = ImageFont.truetype(body_font_path, max(28, theme.body_size - 2))
    footer_font = ImageFont.truetype(body_font_path, 20)

    draw.text((48, 32), lesson_title, fill=theme.muted, font=header_font)
    if not theme.show_emoji:
        label = "Answer" if reveal_answer else "Quiz"
        draw.text(
            (WIDTH - 240, 32),
            f"{label}  {q_num}/{q_total}",
            fill=theme.muted, font=header_font,
        )

    draw.text((48, 100), f"Q{q_num}.", fill=theme.accent, font=badge_font)

    y = 160
    for line in _wrap(question["question"], question_font, WIDTH - 100):
        draw.text((48, y), line, fill=theme.fg, font=question_font)
        y += question_font.size + 8

    y += 30
    correct = question["answer"]
    for letter in ("A", "B", "C", "D"):
        text = f"  {letter}.   {question['options'][letter]}"
        is_correct = reveal_answer and letter == correct
        fill = theme.accent if is_correct else theme.fg
        prefix = "►" if is_correct else " "
        draw.text((48, y), prefix, fill=theme.accent, font=option_font)
        for line in _wrap(text, option_font, WIDTH - 140):
            draw.text((80, y), line, fill=fill, font=option_font)
            y += option_font.size + 6
        y += 8

    if not reveal_answer:
        draw.text(
            (WIDTH // 2 - 140, HEIGHT - 80),
            "Think it through…",
            fill=theme.muted, font=option_font,
        )
    draw.text(
        (48, HEIGHT - 40),
        "PadhAI — your AI tutor",
        fill=theme.muted, font=footer_font,
    )
    return img


def _draw_kg_decorations(draw: ImageDraw.ImageDraw, theme: Theme) -> None:
    """Sun + clouds + stars for the kindergarten theme. PIL primitives only —
    no font emoji needed, so it renders identically regardless of installed
    fonts."""
    # sun in top-right
    sx, sy, sr = WIDTH - 110, 80, 50
    draw.ellipse((sx - sr, sy - sr, sx + sr, sy + sr), fill=(255, 200, 60))
    # sun rays
    import math
    for k in range(8):
        ang = k * math.pi / 4
        x1 = sx + int(math.cos(ang) * (sr + 8))
        y1 = sy + int(math.sin(ang) * (sr + 8))
        x2 = sx + int(math.cos(ang) * (sr + 26))
        y2 = sy + int(math.sin(ang) * (sr + 26))
        draw.line((x1, y1, x2, y2), fill=(255, 200, 60), width=5)
    # cloud (left, mid-height)
    cx, cy = 1100, 200
    for dx, dy, rr in [(0, 0, 28), (-30, 8, 22), (30, 8, 22)]:
        draw.ellipse(
            (cx + dx - rr, cy + dy - rr, cx + dx + rr, cy + dy + rr),
            fill=(255, 255, 255),
        )
    # confetti stars in corners
    stars = [(60, HEIGHT - 130), (160, HEIGHT - 150), (WIDTH - 80, HEIGHT - 140)]
    for px, py in stars:
        # simple 5-point star approximation as a polygon
        pts = []
        for k in range(10):
            ang = k * math.pi / 5 - math.pi / 2
            r = 14 if k % 2 == 0 else 6
            pts.append((px + math.cos(ang) * r, py + math.sin(ang) * r))
        draw.polygon(pts, fill=theme.accent)


def _draw_celebration_slide(
    message: str,
    title_font_path: str,
    body_font_path: str,
    theme: Theme,
) -> Image.Image:
    """A short between-scenes 'Yay!' filler for the kindergarten mode."""
    img = Image.new("RGB", (WIDTH, HEIGHT), theme.bg)
    draw = ImageDraw.Draw(img)
    _draw_kg_decorations(draw, theme)

    big = ImageFont.truetype(title_font_path, 110)
    small = ImageFont.truetype(body_font_path, 36)

    # centred big message
    bbox = draw.textbbox((0, 0), message, font=big)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((WIDTH - w) // 2, (HEIGHT - h) // 2 - 30), message,
              fill=theme.accent, font=big)
    sub = "Now let's learn more!"
    bbox2 = draw.textbbox((0, 0), sub, font=small)
    w2 = bbox2[2] - bbox2[0]
    draw.text(((WIDTH - w2) // 2, (HEIGHT + h) // 2 + 20), sub,
              fill=theme.fg, font=small)
    return img


def _draw_whiteboard_panel(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    theme: Theme,
) -> None:
    """A subtle whiteboard frame inside the slide: light off-white fill +
    soft border + faint corner shadows. Only meaningful when the slide
    background is darker than the whiteboard."""
    # whiteboard surface color is always a clean off-white regardless of theme
    surface = (250, 248, 241)
    draw.rectangle((x, y, x + w, y + h), fill=surface)
    # thin frame
    draw.rectangle((x, y, x + w, y + h), outline=(120, 90, 60), width=4)
    # subtle border-tray at the bottom
    draw.rectangle((x - 6, y + h - 6, x + w + 6, y + h + 14),
                   fill=(140, 100, 60))


def _draw_slide(
    scene: Scene,
    scene_num: int,
    total_scenes: int,
    title_font_path: str,
    body_font_path: str,
    lesson_title: str,
    theme: Theme = DARK_ACADEMIC,
    bullets_to_show: int | None = None,
    show_title: bool = True,
    show_teacher: bool = False,
    teacher_pose: str = "neutral",
    partial_bullets: list[str] | None = None,
    display_title: str | None = None,
    mouth_open: bool = False,
    show_cartoon_teacher: bool = True,
) -> Image.Image:
    """Render a teaching slide.

    `bullets_to_show` controls how many bullets are visible (for progressive
    reveal). None means show all. `show_title` False suppresses the scene
    title (for an initial 'blank' frame). `show_teacher` draws the cartoon
    teacher in the bottom-left and re-frames the content as a whiteboard
    panel on the right."""
    img = Image.new("RGB", (WIDTH, HEIGHT), theme.bg)
    draw = ImageDraw.Draw(img)
    if theme.show_accent_stripe:
        draw.rectangle((0, 0, 12, HEIGHT), fill=theme.accent)
    if theme.show_emoji:
        _draw_kg_decorations(draw, theme)

    header_font = ImageFont.truetype(body_font_path, theme.header_size)
    title_font = ImageFont.truetype(title_font_path, theme.title_size)
    bullet_font = ImageFont.truetype(body_font_path, theme.body_size)
    footer_font = ImageFont.truetype(body_font_path, 20)

    draw.text((48, 32), lesson_title, fill=theme.muted, font=header_font)
    if not theme.show_emoji:
        draw.text(
            (WIDTH - 200, 32),
            f"Scene {scene_num}/{total_scenes}",
            fill=theme.muted, font=header_font,
        )

    if show_teacher:
        # === teacher-on-whiteboard layout ===
        # v0.12 D1: detect 9:16 / 1:1 vs 16:9 and switch composition.
        # Vertical (Reels / Shorts): whiteboard takes the full width
        # above the teacher; teacher is bottom-center. Fonts +35% so
        # text reads on a phone screen.
        is_vertical = HEIGHT > WIDTH
        is_square = HEIGHT == WIDTH
        if is_vertical:
            wb_x, wb_y = 30, 60
            wb_w, wb_h = WIDTH - 60, HEIGHT - wb_y - 280
            wb_title_size, wb_bullet_size = 56, 38
        elif is_square:
            wb_x, wb_y = 30, 50
            wb_w, wb_h = WIDTH - 60, HEIGHT - wb_y - 270
            wb_title_size, wb_bullet_size = 42, 28
        else:
            # 16:9 (the original layout)
            wb_x, wb_y = 240, 90
            wb_w, wb_h = WIDTH - wb_x - 30, HEIGHT - wb_y - 50
            wb_title_size, wb_bullet_size = 40, 30
        _draw_whiteboard_panel(draw, wb_x, wb_y, wb_w, wb_h, theme)

        ink = (40, 38, 35)
        ink_muted = (130, 110, 90)
        ink_accent = (180, 60, 40)

        wb_title_font = ImageFont.truetype(title_font_path, wb_title_size)
        wb_bullet_font = ImageFont.truetype(body_font_path, wb_bullet_size)

        cx = wb_x + 28
        cy = wb_y + 28
        title_for_display = display_title if display_title is not None else (
            scene.title if show_title else ""
        )
        if title_for_display:
            for line in _wrap(title_for_display, wb_title_font, wb_w - 56):
                draw.text((cx, cy), line, fill=ink, font=wb_title_font)
                cy += wb_title_font.size + 6
            cy += 6
            draw.rectangle((cx, cy, wb_x + wb_w - 28, cy + 2), fill=ink_accent)
            cy += 18
        else:
            cy += wb_title_font.size + 6 + 18

        if scene.diagram:
            diag_fn = diagrams.get(scene.diagram)
            if diag_fn is not None:
                diag_top = cy + 6
                diag_h = int((wb_y + wb_h - diag_top) * 0.55)
                diag_fn(
                    draw, cx, diag_top, wb_w - 56, diag_h,
                    theme, title_font_path, body_font_path,
                )
                cy = diag_top + diag_h + 16

        # bullets: prefer partial_bullets (typewriter) over bullets_to_show
        if partial_bullets is not None:
            visible = [b for b in partial_bullets if b != ""]
        elif bullets_to_show is not None:
            visible = scene.bullets[:bullets_to_show]
        else:
            visible = scene.bullets
        for bullet in visible:
            for i, line in enumerate(_wrap(bullet, wb_bullet_font, wb_w - 90)):
                prefix = f"{theme.bullet_marker}  " if i == 0 else "    "
                draw.text(
                    (cx + 4, cy), prefix + line,
                    fill=ink if i == 0 else ink_muted, font=wb_bullet_font,
                )
                cy += wb_bullet_font.size + 6
            cy += 6

        # Teacher placement adapts to aspect ratio:
        #   16:9   teacher bottom-LEFT (existing)
        #   9:16   teacher bottom-CENTER, slightly smaller so it
        #          doesn't hog the phone screen
        #   1:1    teacher bottom-center, scaled to match
        #
        # When an external photoreal provider is rendering the teacher,
        # we skip the cartoon entirely and leave the slot empty so the
        # composite step can overlay the provider's clip.
        if show_cartoon_teacher:
            if is_vertical:
                teacher_cx = WIDTH // 2
                teacher_base = HEIGHT - 40
                teacher_height = 260
            elif is_square:
                teacher_cx = WIDTH // 2
                teacher_base = HEIGHT - 30
                teacher_height = 220
            else:
                teacher_cx = 130
                teacher_base = HEIGHT - 38
                teacher_height = 380
            draw_teacher(
                draw, teacher_cx, teacher_base,
                pose=teacher_pose, height=teacher_height, mouth_open=mouth_open,
            )

        draw.text(
            (40, HEIGHT - 28),
            "PadhAI — your AI tutor",
            fill=theme.muted, font=footer_font,
        )
        return img

    # === legacy single-column layout (no teacher) ===
    y = 110
    if show_title:
        for line in _wrap(scene.title, title_font, WIDTH - 100):
            draw.text((48, y), line, fill=theme.fg, font=title_font)
            y += title_font.size + 8
        y += 12
        draw.rectangle((48, y, WIDTH - 48, y + 2), fill=theme.accent)
        y += 28
    else:
        y += title_font.size + 12 + 28

    if scene.diagram:
        diag_fn = diagrams.get(scene.diagram)
        if diag_fn is not None:
            diag_h = 300
            diag_fn(
                draw, 48, y, WIDTH - 96, diag_h,
                theme, title_font_path, body_font_path,
            )
            y += diag_h + 20

    visible = scene.bullets if bullets_to_show is None else scene.bullets[:bullets_to_show]
    for bullet in visible:
        for i, line in enumerate(_wrap(bullet, bullet_font, WIDTH - 140)):
            prefix = f"{theme.bullet_marker}  " if i == 0 else "    "
            draw.text(
                (60, y), prefix + line,
                fill=theme.fg if i == 0 else theme.muted, font=bullet_font,
            )
            y += bullet_font.size + 8
        y += 10

    draw.text(
        (48, HEIGHT - 40),
        "PadhAI — your AI tutor",
        fill=theme.muted, font=footer_font,
    )
    return img


def _make_reveal_clip(
    scene: Scene,
    scene_num: int,
    total_scenes: int,
    title_font_path: str,
    body_font_path: str,
    lesson_title: str,
    theme: Theme,
    audio_path: Path,
    output: Path,
    work_dir: Path,
    tail_pad_seconds: float = 0.5,
    show_teacher: bool = False,
) -> None:
    """Progressive-reveal clip: title appears first, bullets appear one by
    one, all timed to fit inside the narration audio.

    When `show_teacher` is True the teacher's pose changes at each reveal
    stage (cycling through `avatar.POSE_CYCLE`), giving a sense of motion
    without a real animation pipeline."""
    audio_duration = max(_probe_duration(audio_path), 1.0)
    n_bullets = len(scene.bullets)
    n_stages = n_bullets + 1
    per_stage = audio_duration / n_stages

    frame_paths: list[Path] = []
    for k in range(n_stages):
        pose = POSE_CYCLE[k % len(POSE_CYCLE)] if show_teacher else "neutral"
        slide = _draw_slide(
            scene, scene_num, total_scenes, title_font_path, body_font_path,
            lesson_title, theme=theme, bullets_to_show=k,
            show_teacher=show_teacher, teacher_pose=pose,
        )
        frame_path = work_dir / f"reveal_{scene_num:02d}_{k:02d}.png"
        slide.save(frame_path)
        frame_paths.append(frame_path)

    # concat-demuxer manifest with per-image durations
    manifest = work_dir / f"reveal_{scene_num:02d}.txt"
    lines = []
    for fp in frame_paths:
        lines.append(f"file '{fp}'")
        lines.append(f"duration {per_stage:.3f}")
    # concat demuxer requires the last entry to be repeated without duration
    lines.append(f"file '{frame_paths[-1]}'")
    manifest.write_text("\n".join(lines))

    total_duration = audio_duration + tail_pad_seconds
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-i", str(audio_path),
            "-af", f"apad=pad_dur={tail_pad_seconds:.2f}",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{total_duration:.2f}", "-r", "24",
            "-vf", "fps=24",  # normalise variable-rate concat to constant fps
            str(output),
        ],
        check=True, capture_output=True,
    )


def _composite_external_teacher(
    background_clip: Path,
    teacher_clip: Path,
    audio_path: Path,
    output: Path,
    teacher_box: tuple[int, int, int, int] = (28, 320, 230, 380),
    tail_pad_seconds: float = 0.5,
) -> None:
    """Compose a slide background (whiteboard + content, no teacher) with
    an externally-rendered teacher clip (Wav2Lip / HeyGen / D-ID). The
    teacher is scaled to teacher_box=(x, y, w, h) and overlaid; the
    narration audio is muxed on top so the result is self-contained.

    Used when TalkingHeadProvider.in_process == False."""
    x, y, w, h = teacher_box
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(background_clip),
            "-i", str(teacher_clip),
            "-i", str(audio_path),
            "-filter_complex",
            f"[1:v]scale={w}:{h}[teacher];[0:v][teacher]overlay={x}:{y}[v];"
            f"[2:a]apad=pad_dur={tail_pad_seconds:.2f}[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            str(output),
        ],
        check=True, capture_output=True,
    )


def _make_animated_clip(
    scene: Scene,
    scene_num: int,
    total_scenes: int,
    title_font_path: str,
    body_font_path: str,
    lesson_title: str,
    theme: Theme,
    audio_path: Path,
    output: Path,
    work_dir: Path,
    fps: int = 12,
    chars_per_sec: float = 22.0,
    pose_seconds: float = 1.5,
    tail_pad_seconds: float = 0.5,
    draw_cartoon_teacher: bool = True,
) -> None:
    """Frame-by-frame animated clip with audio-driven lip flap.

    At `fps` frames per second we render every frame as its own PNG, then
    let ffmpeg encode the lot with the narration audio muxed on top. Each
    frame independently chooses:
      - which characters of the title and bullets are visible (typewriter
        'drawing on the board' effect, paced at `chars_per_sec`)
      - which body pose the teacher holds (cycles every `pose_seconds`)
      - whether the teacher's mouth is open, from the audio amplitude
        envelope produced by `audio_envelope.mouth_timeline`.

    Output is 1280x720 H.264/AAC. Frame count = (audio_duration +
    tail_pad_seconds) * fps. Render time is roughly 50ms per frame on a
    modest CPU."""
    audio_duration = max(_probe_duration(audio_path), 1.0)
    total_duration = audio_duration + tail_pad_seconds
    n_frames = int(total_duration * fps)

    timeline = mouth_timeline(audio_path, fps=fps)
    while len(timeline) < n_frames:
        timeline.append(False)

    title_done_t = min(1.2, audio_duration * 0.18)
    n_bullets = max(1, len(scene.bullets))
    bullet_window = max(0.6, (audio_duration - title_done_t) / n_bullets)

    frame_dir = work_dir / f"frames_{scene_num:02d}"
    frame_dir.mkdir(exist_ok=True)

    for frame_i in range(n_frames):
        t = frame_i / fps

        # title typewriter
        if t < title_done_t:
            chars = max(1, int((t / title_done_t) * len(scene.title)))
            display_title = scene.title[:chars]
        else:
            display_title = scene.title

        # bullets typewriter
        partial_bullets: list[str] = []
        for i, bullet in enumerate(scene.bullets):
            b_start = title_done_t + i * bullet_window
            if t < b_start:
                partial_bullets.append("")
            else:
                elapsed = t - b_start
                chars = int(elapsed * chars_per_sec)
                partial_bullets.append(bullet[:chars])

        pose = POSE_CYCLE[int(t / pose_seconds) % len(POSE_CYCLE)]
        mouth = timeline[frame_i] if frame_i < len(timeline) else False

        slide = _draw_slide(
            scene, scene_num, total_scenes,
            title_font_path, body_font_path, lesson_title,
            theme=theme, show_teacher=True, teacher_pose=pose,
            partial_bullets=partial_bullets,
            display_title=display_title,
            mouth_open=mouth if draw_cartoon_teacher else False,
            show_cartoon_teacher=draw_cartoon_teacher,
        )
        slide.save(frame_dir / f"f{frame_i:05d}.png")

    # Encode at variable framerate, audio muxed, total duration enforced
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", str(frame_dir / "f%05d.png"),
            "-i", str(audio_path),
            "-af", f"apad=pad_dur={tail_pad_seconds:.2f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-r", str(fps),
            "-t", f"{total_duration:.2f}",
            str(output),
        ],
        check=True, capture_output=True,
    )


def _probe_duration(audio_path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of",
            "default=noprint_wrappers=1:nokey=1", str(audio_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(probe.stdout.strip())


def _make_clip(
    image_path: Path,
    audio_path: Path | None,
    output: Path,
    tail_pad_seconds: float = 0.0,
) -> None:
    """Stitch one still image + optional narration into an MP4 clip.

    If `audio_path` is None, a silent track is generated. `tail_pad_seconds`
    adds extra silent time at the end (useful for quiz think time)."""
    if audio_path is not None:
        audio_duration = max(_probe_duration(audio_path), 1.0)
        total_duration = audio_duration + tail_pad_seconds
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image_path),
            "-i", str(audio_path),
            "-af", f"apad=pad_dur={tail_pad_seconds:.2f}",
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{total_duration:.2f}", "-r", "24",
            str(output),
        ]
    else:
        total_duration = max(tail_pad_seconds, 1.0)
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(image_path),
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-t", f"{total_duration:.2f}", "-r", "24",
            str(output),
        ]
    subprocess.run(cmd, check=True, capture_output=True)


def _quiz_narration(question: dict, q_num: int) -> tuple[str, str]:
    """Build the question-reading and answer-reveal narration scripts."""
    opts = question["options"]
    question_script = (
        f"Question {q_num}. {question['question']} "
        f"Your options are. A: {opts['A']}. B: {opts['B']}. "
        f"C: {opts['C']}. D: {opts['D']}."
    )
    correct = question["answer"]
    answer_script = f"The correct answer is {correct}: {opts[correct]}."
    return question_script, answer_script


def render_lesson(
    lesson: Lesson,
    output_path: Path,
    cache: Cache | None = None,
    include_quiz: bool = True,
    think_time_seconds: float = 4.0,
    theme: Theme = DARK_ACADEMIC,
    render_mode: str = "reveal",
    show_teacher: bool = False,
    talking_head_provider=None,
    dimensions: tuple[int, int] = (1280, 720),
) -> Path:
    """Render Lesson → MP4. Returns the output path.

    `render_mode`:
      - "reveal" (default) — whiteboard-style progressive build: title shows
        first, then bullets appear one by one timed to the narration.
      - "static" — one finished slide per scene (cheaper, less engaging).

    `theme` controls colors, fonts, and decorations. Quiz scenes are always
    rendered statically because their narration already structures the
    reveal.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    # Honour the caller's aspect ratio. Layout helpers read module-level
    # WIDTH/HEIGHT, so set them here once before any slide is drawn.
    set_canvas_dimensions(*dimensions)
    if render_mode not in ("reveal", "static", "animated"):
        raise ValueError(
            f"render_mode must be 'reveal', 'static', or 'animated', got {render_mode!r}"
        )

    title_font, body_font = _find_font(lesson.language_code, theme=theme)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        clips: list[Path] = []

        celebration_messages = ["Yay!", "Wow!", "Awesome!", "Great!"]
        for i, scene in enumerate(lesson.scenes, start=1):
            mp3_path = tmp_path / f"scene_{i:02d}.mp3"
            synthesise_audio(scene.narration, lesson.language_code, mp3_path, cache)
            clip_path = tmp_path / f"scene_{i:02d}.mp4"

            if render_mode == "animated":
                # External photoreal provider path: render whiteboard
                # background without the cartoon teacher, ask the provider
                # to render the teacher clip from the same audio, then
                # composite. Falls back to inline cartoon when there's no
                # provider or the provider is in-process.
                use_external = (
                    talking_head_provider is not None
                    and not getattr(talking_head_provider, "in_process", True)
                )
                if use_external:
                    bg_clip = tmp_path / f"scene_{i:02d}_bg.mp4"
                    teacher_clip = tmp_path / f"scene_{i:02d}_teacher.mp4"
                    _make_animated_clip(
                        scene, i, len(lesson.scenes), title_font, body_font,
                        lesson.title, theme, mp3_path, bg_clip, tmp_path,
                        tail_pad_seconds=0.5,
                        draw_cartoon_teacher=False,
                    )
                    talking_head_provider.render_clip(
                        mp3_path, scene.narration, lesson.language_code,
                        teacher_clip,
                    )
                    _composite_external_teacher(
                        bg_clip, teacher_clip, mp3_path, clip_path,
                        tail_pad_seconds=0.5,
                    )
                else:
                    _make_animated_clip(
                        scene, i, len(lesson.scenes), title_font, body_font,
                        lesson.title, theme, mp3_path, clip_path, tmp_path,
                        tail_pad_seconds=0.5,
                    )
            elif render_mode == "reveal":
                _make_reveal_clip(
                    scene, i, len(lesson.scenes), title_font, body_font,
                    lesson.title, theme, mp3_path, clip_path, tmp_path,
                    tail_pad_seconds=0.5, show_teacher=show_teacher,
                )
            else:
                slide = _draw_slide(
                    scene, i, len(lesson.scenes), title_font, body_font,
                    lesson.title, theme=theme, show_teacher=show_teacher,
                    teacher_pose="explain",
                )
                slide_path = tmp_path / f"scene_{i:02d}.png"
                slide.save(slide_path)
                _make_clip(slide_path, mp3_path, clip_path, tail_pad_seconds=0.5)
            clips.append(clip_path)

            # Kindergarten mode: insert a short cheerful filler between scenes
            # (but not after the last one — quiz takes over there).
            if theme.show_emoji and i < len(lesson.scenes):
                celeb_slide = _draw_celebration_slide(
                    celebration_messages[(i - 1) % len(celebration_messages)],
                    title_font, body_font, theme,
                )
                celeb_path = tmp_path / f"celeb_{i:02d}.png"
                celeb_slide.save(celeb_path)
                celeb_clip = tmp_path / f"celeb_{i:02d}.mp4"
                _make_clip(celeb_path, None, celeb_clip, tail_pad_seconds=2.0)
                clips.append(celeb_clip)

        if include_quiz and lesson.quiz:
            total = len(lesson.quiz)
            for qi, question in enumerate(lesson.quiz, start=1):
                question_script, answer_script = _quiz_narration(question, qi)

                ask_slide = _draw_quiz_slide(
                    question, qi, total, title_font, body_font, lesson.title,
                    reveal_answer=False, theme=theme,
                )
                ask_slide_path = tmp_path / f"quiz_ask_{qi:02d}.png"
                ask_slide.save(ask_slide_path)
                ask_mp3 = tmp_path / f"quiz_ask_{qi:02d}.mp3"
                synthesise_audio(question_script, lesson.language_code, ask_mp3, cache)
                ask_clip = tmp_path / f"quiz_ask_{qi:02d}.mp4"
                _make_clip(
                    ask_slide_path, ask_mp3, ask_clip,
                    tail_pad_seconds=think_time_seconds,
                )
                clips.append(ask_clip)

                reveal_slide = _draw_quiz_slide(
                    question, qi, total, title_font, body_font, lesson.title,
                    reveal_answer=True, theme=theme,
                )
                reveal_slide_path = tmp_path / f"quiz_reveal_{qi:02d}.png"
                reveal_slide.save(reveal_slide_path)
                reveal_mp3 = tmp_path / f"quiz_reveal_{qi:02d}.mp3"
                synthesise_audio(answer_script, lesson.language_code, reveal_mp3, cache)
                reveal_clip = tmp_path / f"quiz_reveal_{qi:02d}.mp4"
                _make_clip(
                    reveal_slide_path, reveal_mp3, reveal_clip,
                    tail_pad_seconds=0.8,
                )
                clips.append(reveal_clip)

        concat_list = tmp_path / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p}'" for p in clips))
        # re-encode on the final concat because reveal clips and static
        # clips may have come out of ffmpeg with slightly different stream
        # parameters; `-c copy` is unsafe across mixed pipelines.
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                str(output_path),
            ],
            check=True, capture_output=True,
        )

    return output_path
