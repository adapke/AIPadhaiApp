"""Preview the kindergarten slide + celebration filler."""
from padhai.pedagogy import Scene
from padhai.render import _draw_celebration_slide, _draw_slide, _find_font
from padhai.themes import KINDERGARTEN

scene = Scene(
    title="The Sun helps plants grow",
    narration="placeholder",
    bullets=[
        "Plants love the Sun",
        "Sun gives them food",
        "We say thank you, Sun!",
    ],
)

title_font, body_font = _find_font("en", theme=KINDERGARTEN)

kg_slide = _draw_slide(
    scene, 1, 3, title_font, body_font, "Photosynthesis",
    theme=KINDERGARTEN,
)
kg_slide.save("kg_slide_preview.png")

celeb = _draw_celebration_slide("Yay!", title_font, body_font, KINDERGARTEN)
celeb.save("kg_celebration_preview.png")
print("OK — kg_slide_preview.png, kg_celebration_preview.png")
