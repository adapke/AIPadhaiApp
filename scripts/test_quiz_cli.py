"""Unit-test the voice-quiz matcher and feedback script generators."""

from padhai.quiz_cli import _feedback_script, _format_question_script, match_answer

options = {
    "A": "Hemoglobin",
    "B": "Chlorophyll",
    "C": "Melanin",
    "D": "Carotene",
}

# direct letter recognition
assert match_answer("B", options) == "B"
assert match_answer("the answer is B", options) == "B"
assert match_answer("bee", options) == "B"
assert match_answer("option D", options) == "D"

# substring match against option text
assert match_answer("Chlorophyll", options) == "B"
assert match_answer("I think it's chlorophyll", options) == "B"
assert match_answer("melanin", options) == "C"

# unclear input → None
assert match_answer("um I don't know", options) is None
assert match_answer("", options) is None
assert match_answer("hello", options) is None

# scripts
q = {"question": "What traps sunlight?", "options": options, "answer": "B"}
text = _format_question_script(q, 1, kid_mode=False)
assert "Question 1" in text and "A: Hemoglobin" in text

kid_text = _format_question_script(q, 1, kid_mode=True)
assert "Option A: Hemoglobin" in kid_text and "What do you think" in kid_text

# feedback: kid mode is always encouraging
assert "Yay" in _feedback_script("B", "B", options, kid_mode=True)
assert "Good try" in _feedback_script("B", "A", options, kid_mode=True)
assert "Good try" in _feedback_script("B", None, options, kid_mode=True)

# adult mode is more direct
assert "Correct" in _feedback_script("B", "B", options, kid_mode=False)
assert "Not quite" in _feedback_script("B", "A", options, kid_mode=False)

print("OK — all quiz_cli matcher + script tests pass")
