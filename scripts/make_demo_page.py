"""Generate a synthetic 'textbook page' image so the pipeline can be tested
without a real scan. Mimics a Class 6 NCERT Science page on photosynthesis."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1240, 1754  # ~A4 at 150dpi
MARGIN = 90
BG = (252, 250, 244)
INK = (28, 28, 30)
ACCENT = (180, 60, 40)


def main() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    serif_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
    serif = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
    sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    f_chapter = ImageFont.truetype(sans, 22)
    f_h1 = ImageFont.truetype(serif_bold, 44)
    f_h2 = ImageFont.truetype(serif_bold, 28)
    f_body = ImageFont.truetype(serif, 22)

    y = MARGIN
    draw.text((MARGIN, y), "Chapter 7 — Getting to Know Plants", fill=ACCENT, font=f_chapter)
    y += 50
    draw.text((MARGIN, y), "7.3  Photosynthesis", fill=INK, font=f_h1)
    y += 80

    paragraphs = [
        ("",
         "Plants are the only living organisms that can prepare their own food. "
         "The process by which green plants make their food using sunlight, water "
         "and carbon dioxide is called photosynthesis. The word comes from two "
         "Greek words: 'photo' meaning light, and 'synthesis' meaning to put together."),
        ("Where does it happen?",
         "Photosynthesis takes place mainly in the leaves of a plant. The green "
         "colour of leaves comes from a pigment called chlorophyll. Chlorophyll "
         "is present inside small structures called chloroplasts. It is the "
         "chlorophyll that traps sunlight and uses its energy to drive the "
         "reaction."),
        ("What goes in and what comes out?",
         "The raw materials needed for photosynthesis are water and carbon "
         "dioxide. Water is absorbed by the roots from the soil and carried "
         "to the leaves through the stem. Carbon dioxide enters the leaves "
         "from the air through tiny openings called stomata. The products "
         "of photosynthesis are glucose (a kind of sugar) and oxygen. The "
         "plant stores the glucose as starch, and releases the oxygen back "
         "into the air — which is why forests are sometimes called the "
         "lungs of our planet."),
        ("A simple equation",
         "Carbon dioxide + Water  ——sunlight, chlorophyll——>  Glucose + Oxygen"),
    ]

    for heading, body in paragraphs:
        if heading:
            draw.text((MARGIN, y), heading, fill=INK, font=f_h2)
            y += 50
        # naive word-wrap
        words = body.split()
        line = ""
        max_w = WIDTH - 2 * MARGIN
        for word in words:
            test = (line + " " + word).strip()
            if f_body.getlength(test) <= max_w:
                line = test
            else:
                draw.text((MARGIN, y), line, fill=INK, font=f_body)
                y += 32
                line = word
        if line:
            draw.text((MARGIN, y), line, fill=INK, font=f_body)
            y += 40

    out = Path(__file__).resolve().parent.parent / "demo_page.png"
    img.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
