"""A simple cartoon teacher figure drawn with PIL primitives.

We keep this small enough that the avatar can be re-drawn per reveal frame
in different poses, producing a sense of animation without dragging in a
real character-animation toolchain.

Four poses are supported and cycle automatically across reveal stages:
    neutral  — both arms relaxed
    point    — right arm out, pointing toward the whiteboard
    explain  — right arm raised in a gesture
    write    — right arm bent forward, marker in hand

Style is deliberately stick-figure-ish: warm beige skin, dark hair, a
single bright shirt color, navy trousers. The point is to read clearly
at thumbnail size, not to be art-quality."""

from __future__ import annotations

from PIL import ImageDraw

# A pose cycles in this order one per reveal stage.
POSE_CYCLE = ("explain", "point", "write", "explain", "point", "neutral")

# Colors
SKIN = (220, 195, 165)
HAIR = (40, 30, 30)
SHIRT = (140, 80, 160)        # purple
TROUSERS = (50, 60, 100)      # navy
MARKER = (60, 60, 60)
LINE = (20, 20, 20)


def draw_teacher(
    draw: ImageDraw.ImageDraw,
    cx: int,
    base_y: int,
    pose: str = "neutral",
    height: int = 360,
    mouth_open: bool = False,
) -> None:
    """Draw the teacher centred horizontally on `cx`, feet at `base_y`,
    sized to roughly `height` pixels tall.

    When `mouth_open` is True the closed-mouth smile is replaced with a
    small filled oval, the classic 2-state 'lip flap' that — toggled in
    sync with audio amplitude — reads as 'talking' even though it's only
    two frames."""
    h = height / 360  # base scale factor
    # Anatomy in scaled units
    head_r = int(28 * h)
    body_h = int(140 * h)
    body_w = int(78 * h)
    leg_h = int(80 * h)
    arm_len = int(82 * h)
    arm_w = int(16 * h)

    # legs
    leg_top = base_y - leg_h
    draw.rectangle((cx - int(22 * h), leg_top, cx - int(4 * h), base_y),
                   fill=TROUSERS)
    draw.rectangle((cx + int(4 * h), leg_top, cx + int(22 * h), base_y),
                   fill=TROUSERS)
    # shoes
    draw.rectangle((cx - int(26 * h), base_y - int(6 * h),
                    cx - int(2 * h), base_y + int(2 * h)), fill=LINE)
    draw.rectangle((cx + int(2 * h), base_y - int(6 * h),
                    cx + int(26 * h), base_y + int(2 * h)), fill=LINE)

    # torso (slightly tapered)
    torso_bottom = leg_top
    torso_top = torso_bottom - body_h
    draw.polygon([
        (cx - body_w // 2, torso_bottom),
        (cx + body_w // 2, torso_bottom),
        (cx + body_w // 2 - int(8 * h), torso_top),
        (cx - body_w // 2 + int(8 * h), torso_top),
    ], fill=SHIRT)

    # neck
    draw.rectangle(
        (cx - int(8 * h), torso_top - int(14 * h),
         cx + int(8 * h), torso_top + 1),
        fill=SKIN,
    )

    # head
    head_cy = torso_top - int(14 * h) - head_r + int(3 * h)
    draw.ellipse(
        (cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r),
        fill=SKIN,
    )

    # hair: top dome arc
    draw.pieslice(
        (cx - head_r - 2, head_cy - head_r - 6,
         cx + head_r + 2, head_cy + head_r - int(head_r * 0.6)),
        200, 340, fill=HAIR,
    )

    # eyes
    eye_r = max(2, int(2 * h))
    eye_y = head_cy - int(2 * h)
    draw.ellipse((cx - int(11 * h) - eye_r, eye_y - eye_r,
                  cx - int(11 * h) + eye_r, eye_y + eye_r), fill=LINE)
    draw.ellipse((cx + int(11 * h) - eye_r, eye_y - eye_r,
                  cx + int(11 * h) + eye_r, eye_y + eye_r), fill=LINE)

    # mouth: closed smile vs open oval
    mouth_box = (
        cx - int(9 * h), head_cy + int(6 * h),
        cx + int(9 * h), head_cy + int(15 * h),
    )
    if mouth_open:
        draw.ellipse(mouth_box, fill=(70, 25, 25))
        # tiny teeth highlight
        draw.rectangle(
            (mouth_box[0] + 2, mouth_box[1] + 2,
             mouth_box[2] - 2, mouth_box[1] + max(2, int(3 * h))),
            fill=(220, 220, 220),
        )
    else:
        draw.arc(mouth_box, start=10, end=170, fill=(80, 30, 30), width=2)

    shoulder_l = (cx - body_w // 2 + int(6 * h), torso_top + int(8 * h))
    shoulder_r = (cx + body_w // 2 - int(6 * h), torso_top + int(8 * h))

    def thick_line(p1, p2, color=SHIRT, width=arm_w):
        draw.line((p1[0], p1[1], p2[0], p2[1]), fill=color, width=width)

    def hand(p):
        r = int(9 * h)
        draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=SKIN)

    if pose == "neutral":
        # both arms hang down, slight angle outward
        l_end = (shoulder_l[0] - int(6 * h), shoulder_l[1] + arm_len)
        r_end = (shoulder_r[0] + int(6 * h), shoulder_r[1] + arm_len)
        thick_line(shoulder_l, l_end)
        thick_line(shoulder_r, r_end)
        hand(l_end)
        hand(r_end)
    elif pose == "point":
        # right arm extended toward whiteboard (to the right)
        r_end = (shoulder_r[0] + arm_len + int(10 * h),
                 shoulder_r[1] - int(18 * h))
        thick_line(shoulder_r, r_end)
        hand(r_end)
        # extended index finger as a small extra line
        thick_line(r_end, (r_end[0] + int(14 * h), r_end[1] - int(2 * h)),
                   color=SKIN, width=max(3, int(5 * h)))
        # left arm hangs
        l_end = (shoulder_l[0] - int(6 * h), shoulder_l[1] + arm_len)
        thick_line(shoulder_l, l_end)
        hand(l_end)
    elif pose == "explain":
        # right arm up and out, palm open
        r_end = (shoulder_r[0] + int(55 * h), shoulder_r[1] - int(65 * h))
        thick_line(shoulder_r, r_end)
        hand(r_end)
        # left arm slightly out
        l_end = (shoulder_l[0] - int(28 * h), shoulder_l[1] + arm_len - int(20 * h))
        thick_line(shoulder_l, l_end)
        hand(l_end)
    elif pose == "write":
        # right arm bent forward holding a marker, pointing right
        elbow = (shoulder_r[0] + int(28 * h), shoulder_r[1] + int(28 * h))
        wrist = (shoulder_r[0] + int(70 * h), shoulder_r[1] + int(8 * h))
        thick_line(shoulder_r, elbow)
        thick_line(elbow, wrist)
        hand(wrist)
        # marker
        draw.rectangle(
            (wrist[0] + int(6 * h), wrist[1] - int(4 * h),
             wrist[0] + int(26 * h), wrist[1] + int(4 * h)),
            fill=MARKER,
        )
        l_end = (shoulder_l[0] - int(6 * h), shoulder_l[1] + arm_len)
        thick_line(shoulder_l, l_end)
        hand(l_end)
    else:
        # fall back to neutral
        draw_teacher(draw, cx, base_y, "neutral", height)
