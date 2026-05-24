"""Render question + reveal quiz slides to PNG to verify layout."""
from pathlib import Path

from padhai.render import _draw_quiz_slide, _find_font

question = {
    "question": "Which pigment in plant leaves traps sunlight to drive photosynthesis?",
    "options": {
        "A": "Hemoglobin",
        "B": "Chlorophyll",
        "C": "Melanin",
        "D": "Carotene",
    },
    "answer": "B",
}

title_font, body_font = _find_font("en")
ask = _draw_quiz_slide(question, 1, 3, title_font, body_font, "Photosynthesis", reveal_answer=False)
ask.save("quiz_ask_preview.png")
reveal = _draw_quiz_slide(question, 1, 3, title_font, body_font, "Photosynthesis", reveal_answer=True)
reveal.save("quiz_reveal_preview.png")
print("OK — wrote quiz_ask_preview.png and quiz_reveal_preview.png")
