"""prod-192 — SAT (US Digital SAT) exam hub.

A self-contained, server-rendered `/sat` page that brings the four
pillars of the SAT section together for US / NRI / US-bound students:

  * Details   — accurate Digital SAT format, scoring, timing, sections
  * Videos    — curated, oembed-verified SAT-prep YouTube embeds
  * Flashcards — interactive flip cards (math formulas / vocab / grammar)
  * Test      — inline practice test wired to POST /api/practice/generate
                (exam="sat") + /submit, backed by the seeded SAT question
                bank (subjects sat_math / sat_reading_writing).

Public (no auth) so the details / videos / flashcards render before
sign-in; the practice test prompts sign-in when no token is present
(the practice API itself is auth-gated).
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

# Curated, oembed-verified SAT videos (also seeded into concept_videos
# via scripts/seed_sat_videos.py). Embedded here by id for reliable
# display independent of the catalog search.
_VIDEOS = {
    'Digital SAT — Overview': [
        {"title": 'Digital SAT — Format, Structure & Scoring', "id": 'aNjoBgqrKvE'},
        {"title": 'Khan Academy — Official SAT Prep Overview', "id": 'cSD0qVybO9s'},
        {"title": 'Digital SAT App: Live Demo and Official Practice', "id": '33jFcagTHpc'},
        {"title": "What's a Good Digital SAT Score in 2024-2025", "id": 'UuUIoihME_M'},
        {"title": 'Digital SAT Study Guide: How the Scoring Algorithm Works', "id": 'pFf7HcJ6Q0U'},
        {"title": 'How To Interpret Your Digital SAT Score Report', "id": '43SiM6YVes4'},
        {"title": 'Digital SAT - Everything You Need to Know in 4 Minutes', "id": 'DTBT4bMQXOU'},
        {"title": 'EVERYTHING You Need to Know about the SAT (2026)', "id": '9IC3WMCAAwc'},
        {"title": 'EVERYTHING You Need to Know about the Digital SAT', "id": '06Y6OtRj0n8'},
        {"title": 'ULTIMATE DIGITAL SAT GUIDE! scoring algorithm, adaptive test, the new structure, and so mu', "id": 'm_X5gRzXSiA'},
        {"title": 'Know How the Digital SAT is Scored to Hit Your Goal in 2024! (Algorithm Explained)', "id": 'HXP17Be8TRY'},
    ],
    'Bluebook & Test Day': [
        {"title": 'Getting Started with the SAT "Bluebook" APP for your digital SAT practice', "id": '1bl8BI-3bQs'},
        {"title": 'How to use Bluebook for Digital SAT Practice', "id": 'bUVI0iK740o'},
        {"title": 'What to expect with the digital SAT on a school day', "id": 'HpXSadvUvOM'},
        {"title": 'What is Digital SAT Adaptive Testing?', "id": 'IFYjH9NzhJU'},
        {"title": 'Digital SAT Full Walkthrough Bluebook Official Practice Exam 11 Reading & Writing Module 1', "id": 'grBJUKXUyLU'},
        {"title": 'Desmos embedded in the Digital SAT!', "id": '74xnNRK-ARs'},
        {"title": 'How to use Bluebook App for Digital SAT Practice', "id": 'bpZ7ZY2EP4w'},
        {"title": 'Digital SAT: Live Bluebook Demo and Official Practice Resources', "id": 'OXfZTfynbuk'},
    ],
    'Desmos Calculator': [
        {"title": 'Desmos Calculator Guide', "id": '-pGNBb8M3LQ'},
        {"title": 'The Ultimate Desmos SAT Math Guide (12 Game-Changing Strategies)', "id": 'l7YOKxT3qJg'},
        {"title": 'EVERY DESMOS HACK in Under 9 Minutes | SAT Math', "id": 'ZYDcmWtfLkk'},
        {"title": "The Last SAT Desmos Guide You'll Need", "id": 'e-O4nwVHQ-Y'},
        {"title": 'Ultimate Desmos Guide to Digital SAT Math-Part 3: Core Skills', "id": 'kPrClHiw-BM'},
        {"title": 'SAT Math: You Can Use Desmos on THESE?!', "id": 'ufz3jVNKT18'},
        {"title": 'How to Use DESMOS for SAT Math: Solving Single Variable Equations & Number of Solutions', "id": '4hCjnen9Cfs'},
        {"title": 'Full SAT Desmos Guide (7 Minutes)', "id": 'k2UZX09zwSQ'},
        {"title": 'Every SAT Math Desmos Hack in 14 Minutes', "id": 'ISIAw2DrdxM'},
        {"title": 'DESMOS Digital SAT Calculator Strategies', "id": 'axFmkTY_O4M'},
    ],
    'Strategy & Score Boost': [
        {"title": 'SAT Math — Strategies for an 800', "id": 'Dp291sIwtYk'},
        {"title": 'R&W — Tips to Break 1500', "id": 'FS_nvzsoIyE'},
        {"title": 'How to Get a Perfect Score on the Digital SAT', "id": 'OAyM5pRjNwY'},
        {"title": 'How to guess on the Digital SAT and ACT (to raise your score!)', "id": 'KbK9E4dIMqA'},
        {"title": 'How to CRAM for the Digital SAT', "id": 'N8RP5I2OOxs'},
        {"title": 'How to Self-Study for the Digital SAT', "id": 'hMiiB4PgltQ'},
        {"title": 'How to Score a 1500+ on the SAT (2025)', "id": 'j3LPqlkeP6k'},
        {"title": 'Time-Saving Tips for the Digital SAT (2024)', "id": 'w2cSswdtkjU'},
        {"title": 'How to Manage Time on the Digital SAT (10 Proven Strategies)', "id": 'gMGQHSUFRCU'},
        {"title": 'Digital SAT Time Management: Two Strategies for Success!', "id": 'Pqi1i-HiIrs'},
        {"title": '7 Tips to Improve Your SAT Score | The Princeton Review', "id": 'u94HWW4zMsY'},
        {"title": 'Digital SAT Time Management: 60 Seconds Per Question?', "id": 'QBkLRdijiM8'},
    ],
    'Math · Algebra': [
        {"title": 'Linear Equations (Algebra)', "id": 'wBA0TpNy0Wo'},
        {"title": 'Systems of Linear Equations', "id": 'iYs57D4Ko0s'},
        {"title": 'SAT Math on Khan Academy: Solving Linear Equations and Inequalities (Foundations)', "id": 'vrPbC9fao6Y'},
        {"title": 'Solving linear equations — Basic example | Math | New SAT', "id": 'EkBUTZe_SiM'},
        {"title": 'Advanced SAT Math: Systems of Linear Equations Word Problems', "id": 'wwJntieAN7Y'},
        {"title": 'SAT Math Secrets to Solve Systems of Linear Equations FAST (Heart Of Algebra)', "id": 'FPRmlWU93vQ'},
        {"title": 'SAT Math: Solving Linear Functions Like a Pro! (Heart Of Algebra Section)', "id": '1SBTKwUDAtk'},
        {"title": 'SAT Practice Questions: Solve Math and Linear Equations Questions', "id": 'YAgzJwJ2uMI'},
        {"title": 'SAT MATH || Heart of Algebra || Solving Linear Equations', "id": '8acvma5oVnQ'},
        {"title": 'SAT Math section - Heart of Algebra - solving linear equations and inequalities', "id": 'juie0cTHPfk'},
        {"title": 'Systems of Linear Equations on the SAT — Fast & Easy Desmos Calculator Tricks, 2 examples ', "id": '3fOwcTwxeFQ'},
        {"title": 'SAT Math Lesson: Systems of Linear Equations, Solving for an Expression', "id": 'ttcAQceD9jQ'},
    ],
    'Math · Advanced Math': [
        {"title": 'SAT Math on Khan Academy: Solving Quadratic Equations (Foundations)', "id": 'zvczFfWx40k'},
        {"title": 'SAT Math on Khan Academy: Quadratic and Exponential Word Problems (Foundations)', "id": 'YdvF3fVjLQk'},
        {"title": 'SAT Math on Khan Academy: Factoring Quadratic and Polynomial Expressions (Foundations)', "id": 'bOe7t_ibbJM'},
        {"title": 'SAT Math on Khan Academy: Exponential Graphs (Foundations)', "id": 'WlW6oZ6_GNY'},
        {"title": 'SAT Math Part 9 - Properties of Exponents and Powers', "id": '52P9nqQFSJ4'},
        {"title": 'SAT Math Part 18 - Polynomials and Quadratic Functions', "id": '6h4xeeonHu4'},
        {"title": 'Solving Rational Equations and Factoring Trinomials - SAT Math Part 3', "id": 'iZ2-yXzLqCg'},
        {"title": 'Long Division of Polynomials - SAT Math Part 19', "id": 'xbrEEnmXQvg'},
        {"title": 'How to HACK the Hard Math Section of the Digital SAT', "id": 'XZKYc12t3oE'},
        {"title": 'SAT Quadratic Equations Guide 2023: What You Need to Know', "id": 'fQG8oAsNy6g'},
    ],
    'Math · Data Analysis': [
        {"title": 'Ratios, rates, and proportions — Basic example | Math | SAT | Khan Academy', "id": '96fLrtyAiD8'},
        {"title": 'Ratios, rates, and proportions — Harder example | Math | SAT | Khan Academy', "id": 'qX0YQwCw5l4'},
        {"title": 'SAT Math on Khan Academy: Ratios, Rates, and Proportions (Foundations)', "id": 'HFticqOjvH4'},
        {"title": 'SAT Math on Khan Academy: Scatterplots (Foundations)', "id": 'EBsGtgRs2YY'},
        {"title": 'SAT Math on Khan Academy: Percentages (Foundations)', "id": 'EuzcYMTIDjg'},
        {"title": 'Data & Statistics - Mean, Median, Mode, Range, & Standard Deviation - SAT Math Part 44', "id": 'W8NaUtkM46o'},
        {"title": 'SAT MATH: Two-Variable Data: Models & Scatterplots', "id": 'mVEgpc2UHvQ'},
        {"title": 'SAT Math Practice: Mean, Median, Mode, and Standard Deviation Problems', "id": 'xOu4xDpFjNE'},
    ],
    'Math · Geometry & Trig': [
        {"title": 'Geometry & Trigonometry', "id": 'Vwtux_sW9Zs'},
        {"title": 'Digital SAT Math - Skills Insight #4: Geometry & Trigonometry', "id": 'MKcBn4AJvK0'},
        {"title": 'Digital SAT Math Medium: Geometry and Trigonometry', "id": 'j1D6abbz1Kk'},
        {"title": 'Digital SAT Math Advanced: Geometry and Trigonometry', "id": 'WGJ_-cMFtlo'},
        {"title": 'Overcoming Digital SAT Math - Trigonometry', "id": '4d6vzhYE0bc'},
        {"title": 'SAT MATH: Lines, Angles, and Triangles', "id": 'zRJfZ9UUwR4'},
        {"title": 'SAT MATH: Right Triangles & Trigonometry', "id": 'L2Jd-ndBA_M'},
        {"title": 'SAT MATH: Special Right Triangle Rules', "id": 'GXOZslaKx8c'},
        {"title": 'SAT Practice | Lines, Angles, & Triangles', "id": '8pM3dLsMdPo'},
        {"title": 'ALL Trigonometry on the SAT!', "id": 'eTTfZnfdBOQ'},
        {"title": 'All of SAT Geometry and Trigonometry', "id": 'u6LL3Pbo3HI'},
        {"title": 'Right triangle trigonometry — Harder example | Math | SAT', "id": 'ZE40akTB6oo'},
    ],
    'Math · Full Reviews': [
        {"title": 'SAT Math — Full Review', "id": 'ty7B8VyCnFY'},
        {"title": 'SAT Math Test Prep Online Crash Course Algebra & Geometry Study Guide Review, Functions', "id": 'yBCAv_NzzPQ'},
        {"title": '🌟 The ULTIMATE Digital SAT Math Video 🌟 - 38 Strategies to Nail an 800 on Digital SAT Math', "id": '5VdaJ6HYbD8'},
        {"title": 'SAT Math FULL REVIEW for May SAT 2023! Everything you need for an 800!!', "id": 'T2RBmzk3Xvk'},
        {"title": '2026 SAT Math FULL Review & Exam Prep (EVERYTHING YOU NEED TO KNOW!!)', "id": 'qy9htgwZDkg'},
        {"title": 'All of SAT Math Explained in 26 Minutes', "id": '1bTkbmHx944'},
        {"title": 'How to get a PERFECT 800 on the SAT Math (2024)', "id": 'gqoXJPNftAc'},
        {"title": 'November SAT Math Crash Course: 9 Concepts You Will See On Test Day!', "id": 'OKGrV3e9aZk'},
        {"title": 'Digital SAT Math Crash Course - Day 1 (Part 1) - Taking Advantage of the Desmos Calculator', "id": 'f2t2fVmBZ9Q'},
        {"title": 'ANYONE can get an 800 SAT math, just give me 20 minutes of your time', "id": 's0hKu71T4Wg'},
    ],
    'R&W · Craft & Structure': [
        {"title": 'Words in Context (Vocab) Questions on the Digital SAT: Strategies & Practice', "id": 'dFQaoOGYhTk'},
        {"title": 'Digital SAT Reading Strategy: Vocab-in-Context Questions', "id": 'tIlTZwt5VQA'},
        {"title": 'Avoid This Trap on Digital SAT Reading and Writing Test Structure and Purpose Questions', "id": '25KmWnTXu8o'},
        {"title": 'Khan Academy "Text Structure & Purpose" Questions (Advanced)', "id": '1Iz8AbdIPgc'},
        {"title": 'How to Solve DSAT Text Structure and Purpose Questions', "id": 'U3qWNZgoejU'},
        {"title": 'SAT Cross-Text Connections (Double Passages) Strategies & Practice', "id": 'hBbZtGoBA44'},
        {"title": 'Cross-Text Connections (SAT Question Bank)', "id": 'FcsbLmdlyX8'},
        {"title": 'FREE Digital SAT English Class 2 (Paired Passages, Sentence Function, Review)', "id": 'T5ie7Ln6ShM'},
    ],
    'R&W · Information & Ideas': [
        {"title": 'Command of Evidence Questions on the Digital SAT: Strategies & Practice', "id": 'POcYofMngBw'},
        {"title": 'Inference Questions on the Digital SAT: Strategies & Practice (SAT Reading Prep)', "id": 'ANkK_ecpZZY'},
        {"title": 'Digital SAT: Command of Quantitative Evidence (GRAPHS)', "id": 'J7Ka3-JwGxY'},
        {"title": 'Digital SAT Command of Evidence Scientific! Use Simple Reasoning to Support Or Weaken Expl', "id": 'X0dl0a77RaA'},
        {"title": 'Digital SAT Hardest Reading Questions SOLVED!', "id": '972usSOy9o4'},
        {"title": 'Ultimate Inferences Hack for Digital SAT Reading Exam', "id": 'j2HtBo_33do'},
        {"title": 'A Better Way to Solve Quantitative Command of Evidence Questions - SAT Reading', "id": '59AWeIE8jQc'},
        {"title": 'SAT Inference Question Strategies: Raise Your English Score by 80 Points', "id": 'PWgMcZLDpkE'},
        {"title": 'SAT English Hacks | Command of Evidence Textual', "id": 'CsBB1CWLvJs'},
        {"title": 'Digital SAT Reading & Writing Tips: Logically Complete the Text', "id": 'eu6kmFdl7O8'},
        {"title": 'DSAT R&W: A Recurring Pattern on Inference ("Logically Completing the Text") Questions', "id": '9UjGEN1akXU'},
    ],
    'R&W · Grammar & Conventions': [
        {"title": 'Every Grammar Rule in 15 Minutes', "id": 'NLz8CRdMvuI'},
        {"title": '5 Grammar Hacks', "id": '4jgnbFnXiYs'},
        {"title": 'Digital SAT Standard English Conventions: Boundaries', "id": 'M4EOqgOC4ks'},
        {"title": 'Digital SAT Standard English Conventions: Verb Forms', "id": '1ScLbJUnbzk'},
        {"title": 'Every SAT Punctuation Rule You Need (in 30 minutes)', "id": 'pi19m9uIAh8'},
        {"title": 'Digital SAT Writing: Verb Tenses (Full Guide)', "id": 'TBDxpwtH0J4'},
        {"title": 'All SAT Punctuation Rules in 15 Minutes', "id": 'WCHyeJKWD84'},
        {"title": "The Last SAT Punctuation Guide You'll Need", "id": 'WL61t23IOyE'},
        {"title": 'Parallelism | SAT/ACT Crash Course', "id": '4i43Qk_-TyU'},
        {"title": 'EVERY SAT Punctuation Rule in 37 Minutes', "id": 'ruOYTZfxiqk'},
        {"title": 'Digital SAT Reading & Writing: Standard English Conventions - Boundaries (Worked Examples)', "id": 'zeS4pEjPoxg'},
        {"title": 'Punctuation — overview of the rules and strategies', "id": 'uNW4b0yBF40'},
    ],
    'R&W · Expression of Ideas': [
        {"title": 'Rhetorical synthesis — Worked example', "id": 'FJnzYoKM_tk'},
        {"title": 'Cracking Rhetorical Synthesis Questions for the Digital SAT (Path to 1600 Series: DSAT Tip', "id": 'XhVGMSiK4wk'},
        {"title": 'Digital SAT English - Complete Rhetorical Synthesis Strategy Guide', "id": 'q3XpAcPpJAE'},
        {"title": 'Get Ready for the March Digital SAT! Rhetorical Synthesis COMPLETE Breakthrough Part 1! DS', "id": 'seBwtH3MrWA'},
        {"title": 'Khan Academy Rhetorical Synthesis Questions (Advanced)', "id": 'sVtASxq4hng'},
        {"title": 'SAT Transitions — Full Strategy Guide', "id": 'WvpDSRB6NNs'},
        {"title": 'Mastering Logical Transitions on the Digital SAT: Your Complete Guide to Grammar Success!', "id": '6MZo4YvrFIE'},
        {"title": 'Transitions Questions on the Digital SAT: Strategies & Practice', "id": 'kz_P_b3H0oA'},
        {"title": 'Digital SAT: Transitions (Full 2025 Guide)', "id": 'eJ-PMwmareM'},
        {"title": 'Never Miss Another SAT Transition Question', "id": 'BhDFdrPA368'},
        {"title": 'Precision and Concision: Evidence Based Writing | Turito | SAT Prep | English', "id": 'ZJ6A9scXGqw'},
        {"title": 'SAT Prep: Expression of Ideas', "id": '3gAVah_2n-c'},
    ],
    'R&W · Full Reviews': [
        {"title": 'Reading & Writing — Study Guide', "id": '1EHXD2eVKzA'},
        {"title": 'R&W — Question Types & Strategies', "id": '8PrSFbEJXvY'},
        {"title": '4 Tips to ROCK the SAT Reading Section', "id": 'HfG9vxPzvPM'},
        {"title": 'All of SAT Reading & Writing in 22 Minutes (2026)', "id": 'jTkfge6FeE8'},
        {"title": 'SAT Reading Strategies To Score a 700+', "id": 'Q4ylJ4HxgbE'},
        {"title": 'SAT Reading Strategies to Score 750+', "id": '-XqvhhnrjGk'},
        {"title": 'SAT Reading & Writing Section - How to Ace It', "id": '0fIGJ_KU8xQ'},
        {"title": "The Only SAT Reading & Writing Guide You'll Ever Need", "id": 'eq4A5_34ueA'},
        {"title": '15 SAT Reading and Writing Section Techniques Every 1600 Scorer Knows', "id": 'tXEIoN2ALN4'},
    ],
}

_FLASHCARDS = {
    "Math formulas": [
        {"q": "Slope of a line through (x1,y1) and (x2,y2)", "a": "m = (y2 − y1) / (x2 − x1)"},
        {"q": "Quadratic formula", "a": "x = (−b ± √(b² − 4ac)) / 2a"},
        {"q": "Area of a circle", "a": "A = πr²"},
        {"q": "Pythagorean theorem", "a": "a² + b² = c²  (right triangle)"},
        {"q": "Slope-intercept form of a line", "a": "y = mx + b  (m = slope, b = y-intercept)"},
        {"q": "Percent change", "a": "(new − old) / old × 100%"},
        {"q": "Average (arithmetic mean)", "a": "sum of the values ÷ number of values"},
        {"q": "Distance, rate, time", "a": "distance = rate × time"},
    ],
    "Vocabulary": [
        {"q": "lucid", "a": "clear and easy to understand"},
        {"q": "compelling", "a": "convincing; commanding attention"},
        {"q": "ambiguous", "a": "open to more than one interpretation; unclear"},
        {"q": "candid", "a": "honest and direct"},
        {"q": "condescending", "a": "showing a patronizing, superior attitude"},
        {"q": "scrutinize", "a": "to examine closely and critically"},
        {"q": "novel (adjective)", "a": "new or original; not seen before"},
        {"q": "undermine", "a": "to weaken or erode gradually"},
    ],
    "Grammar rules": [
        {"q": "Comma splice", "a": "Don't join two independent clauses with only a comma — use a period, a semicolon, or a comma + conjunction."},
        {"q": "its vs it's", "a": "its = possessive; it's = it is / it has."},
        {"q": "Subject–verb agreement", "a": "The verb agrees with the subject, not the nearest noun: 'The list of items IS…'"},
        {"q": "Semicolon", "a": "Joins two closely related independent clauses: 'I studied; I passed.'"},
        {"q": "Colon", "a": "Use after a complete sentence to introduce a list or explanation."},
        {"q": "Parallel structure", "a": "Items in a series share the same form: 'hiking, swimming, and biking.'"},
        {"q": "who vs whom", "a": "who = subject of the verb; whom = object."},
        {"q": "Dangling modifier", "a": "The opening modifier must describe the subject right after the comma."},
    ],
}

# Accurate Digital SAT facts (College Board, 2024+ digital format).
_FACTS = [
    ("Total score", "400–1600"),
    ("Sections", "R&W + Math (200–800 each)"),
    ("Questions", "98 · 2 h 14 m"),
    ("Format", "Digital & adaptive (Bluebook)"),
    ("Calculator", "Desmos on all of Math"),
    ("Essay", "None (since 2021)"),
]

# Full College Board content map — both sections, all 8 domains + skills.
# Rendered server-side into an in-page syllabus so students never leave
# the app to see "what's on the test."
_SYLLABUS = {
    "SAT Math — 44 questions · 70 min": {
        "Algebra (~35%)": [
            "Linear equations in one and two variables",
            "Systems of linear equations",
            "Linear inequalities",
            "Linear functions and word problems",
        ],
        "Advanced Math (~35%)": [
            "Quadratic and polynomial expressions",
            "Exponents, radicals and rational exponents",
            "Nonlinear and rational equations",
            "Function notation and graphs",
        ],
        "Problem-Solving and Data Analysis (~15%)": [
            "Ratios, rates, proportions and units",
            "Percentages",
            "Mean / median / mode and spread",
            "Scatterplots, tables and probability",
        ],
        "Geometry and Trigonometry (~15%)": [
            "Lines, angles and triangles",
            "Circles, area and volume",
            "Right-triangle trigonometry",
            "The Pythagorean theorem",
        ],
    },
    "SAT Reading & Writing — 54 questions · 64 min": {
        "Craft and Structure (~28%)": [
            "Words in context (vocabulary)",
            "Text structure and purpose",
            "Cross-text connections",
        ],
        "Information and Ideas (~26%)": [
            "Central ideas and details",
            "Command of evidence (textual and quantitative)",
            "Inferences",
        ],
        "Standard English Conventions (~26%)": [
            "Sentence boundaries and punctuation",
            "Subject-verb and pronoun agreement",
            "Verb tense, modifiers and parallelism",
        ],
        "Expression of Ideas (~20%)": [
            "Rhetorical synthesis",
            "Transitions and logical flow",
            "Word choice and concision",
        ],
    },
}


def _syllabus_section() -> str:
    cols = []
    for section, domains in _SYLLABUS.items():
        blocks = ""
        for dom, skills in domains.items():
            lis = "".join(f"<li>{s}</li>" for s in skills)
            blocks += (
                f'<div style="margin-bottom:10px"><b>{dom}</b>'
                f'<ul class="tight">{lis}</ul></div>'
            )
        cols.append(
            f'<div class="card"><h3 style="margin:0 0 8px">{section}</h3>{blocks}</div>'
        )
    return (
        '<section><h2>Full SAT syllabus</h2>'
        '<p class="sub">The complete College Board content map — both sections, '
        "all 8 domains, and the skills each one tests — right here in the app. "
        'Browse the deeper chapter tree on the <a href="/syllabus">Syllabus</a> page.</p>'
        '<div class="grid2">' + "".join(cols) + "</div></section>"
    )


_PROLOGUE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SAT Prep · AI Pathshala</title>
<!-- prod-226: warm the YouTube connections so thumbnails paint instantly and
     the first click starts playback fast. -->
<link rel="preconnect" href="https://i.ytimg.com" crossorigin />
<link rel="preconnect" href="https://www.youtube-nocookie.com" crossorigin />
<link rel="dns-prefetch" href="https://i.ytimg.com" />
<link rel="dns-prefetch" href="https://www.youtube-nocookie.com" />
<style>
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    color:#0f2540;background:#f4f7fb;line-height:1.55}
  a{color:#0077c8}
  header{background:#0a2a52;color:#fff;padding:14px 22px;display:flex;
    justify-content:space-between;align-items:center;position:sticky;top:0;z-index:20}
  header .brand{font-weight:800;font-size:17px}
  header nav a{color:#cfe3f7;margin-left:16px;text-decoration:none;font-size:13px}
  .hero{background:linear-gradient(135deg,#0a2a52,#0077c8);color:#fff;padding:34px 22px}
  .hero .wrap{max-width:1000px;margin:0 auto}
  .hero h1{margin:0 0 6px;font-size:30px}
  .hero p{margin:0;opacity:.92;font-size:15px;max-width:640px}
  .factbar{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}
  .fact{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.25);
    border-radius:10px;padding:8px 12px;font-size:12.5px}
  .fact b{display:block;font-size:13.5px;margin-top:2px}
  main{max-width:1000px;margin:0 auto;padding:24px 22px 60px}
  section{margin-bottom:30px}
  h2{font-size:21px;margin:0 0 4px}
  .sub{color:#5b7088;font-size:13.5px;margin:0 0 16px}
  .card{background:#fff;border:1px solid #e1e9f2;border-radius:14px;padding:20px;
    box-shadow:0 1px 3px rgba(10,42,82,.05)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
  ul.tight{margin:8px 0 0;padding-left:18px}
  ul.tight li{margin-bottom:7px;font-size:14px}
  .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
  .tab{background:#e6eef7;color:#0a2a52;border:0;border-radius:999px;padding:7px 15px;
    font-weight:700;font-size:13px;cursor:pointer}
  .tab.on{background:#0077c8;color:#fff}
  .vgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
  .vid{background:#fff;border:1px solid #e1e9f2;border-radius:12px;overflow:hidden}
  /* prod-226: thumbnail facade — the grid paints instantly from lightweight
     YouTube thumbnails; the heavy iframe loads only when a card is clicked. */
  .vid .fr{position:relative;padding-top:56.25%;background:#0a2a52 center/cover no-repeat;cursor:pointer}
  .vid .fr .pbtn{position:absolute;inset:0;display:flex;align-items:center;justify-content:center}
  .vid .fr .pbtn::before{content:"";width:62px;height:44px;border-radius:12px;
    background:rgba(0,0,0,.62);transition:background .12s}
  .vid .fr:hover .pbtn::before,.vid .fr:focus .pbtn::before{background:#f00}
  .vid .fr .pbtn::after{content:"";position:absolute;border-style:solid;
    border-width:11px 0 11px 19px;border-color:transparent transparent transparent #fff}
  .vid iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
  .vid .t{padding:10px 12px;font-size:13.5px;font-weight:600}
  .fcrow{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
  .fc{perspective:1000px;height:140px;cursor:pointer}
  .fc-in{position:relative;width:100%;height:100%;transition:transform .5s;
    transform-style:preserve-3d}
  .fc.flip .fc-in{transform:rotateY(180deg)}
  .fc-f,.fc-b{position:absolute;inset:0;backface-visibility:hidden;border-radius:12px;
    padding:14px;display:flex;align-items:center;justify-content:center;text-align:center;
    border:1px solid #e1e9f2;font-size:14px}
  .fc-f{background:#fff;font-weight:700}
  .fc-b{background:#0a2a52;color:#fff;transform:rotateY(180deg);font-size:13px}
  .btn{background:#0077c8;color:#fff;border:0;border-radius:9px;padding:11px 20px;
    font-weight:800;font-size:14px;cursor:pointer}
  .btn:hover{background:#0a63a8}
  .btn:disabled{opacity:.55;cursor:not-allowed}
  select{padding:10px 12px;border:1px solid #c5d4e6;border-radius:9px;font-size:14px;
    background:#fff;color:#0f2540}
  .q{border:1px solid #e1e9f2;border-radius:10px;padding:14px;margin-bottom:12px;background:#fff}
  .q .qt{font-weight:600;margin-bottom:10px}
  .opt{display:block;padding:8px 10px;border:1px solid #dde6f1;border-radius:8px;
    margin-bottom:7px;cursor:pointer;font-size:14px}
  .opt:hover{background:#f0f6fc}
  .opt.sel{border-color:#0077c8;background:#e8f3fc}
  .opt.correct{border-color:#1a9c5b;background:#e7f7ee}
  .opt.wrong{border-color:#d23b3b;background:#fcebeb}
  .expl{margin-top:8px;padding:8px 11px;background:#eef6ff;border-left:3px solid #0077c8;border-radius:6px;font-size:13px;color:#0f2540;line-height:1.5}
  .pill{display:inline-block;background:#e6eef7;border-radius:999px;padding:2px 9px;
    font-size:11px;font-weight:700;color:#0a2a52;margin-left:6px}
  .scorebox{background:#0a2a52;color:#fff;border-radius:12px;padding:18px;text-align:center;margin-bottom:14px}
  .scorebox .big{font-size:34px;font-weight:800}
  .note{font-size:12.5px;color:#5b7088;margin-top:10px}
  .err{background:#fcebeb;color:#9c2b2b;padding:10px 12px;border-radius:8px;margin-top:10px;font-size:13.5px}
  .spin{display:inline-block;width:16px;height:16px;border:3px solid #cfe0f0;
    border-top-color:#0077c8;border-radius:50%;animation:sp .8s linear infinite;vertical-align:-3px}
  @keyframes sp{to{transform:rotate(360deg)}}
  @media(max-width:720px){.grid2{grid-template-columns:1fr}.hero h1{font-size:24px}}
</style>
</head>
<body>
<header>
  <div class="brand">AI Pathshala</div>
  <nav>
    <a href="/home">Home</a>
    <a href="/dashboard">Dashboard</a>
    <a href="/concept">Concept videos</a>
    <a href="/chat">AI Tutor</a>
  </nav>
</header>
<div class="hero"><div class="wrap">
  <h1>SAT Prep — Digital SAT</h1>
  <p>Everything you need for the US Digital SAT in one place: how the test
     works, video lessons, quick-revision flashcards, and a real practice
     test that scores you instantly.</p>
  <div class="factbar" id="factbar"></div>
</div></div>
<main>
"""

_EPILOGUE = "</main></body></html>"

_ABOUT = """
<section>
  <h2>About the Digital SAT</h2>
  <p class="sub">The SAT moved fully digital in 2024. Here is what to expect on test day.</p>
  <div class="grid2">
    <div class="card">
      <h3 style="margin:0 0 6px">Structure &amp; timing</h3>
      <ul class="tight">
        <li><b>Reading &amp; Writing</b> — 2 modules, 32 min each (64 min, 54 questions). Short passages, one question each.</li>
        <li><b>Math</b> — 2 modules, 35 min each (70 min, 44 questions). Calculator allowed on every question — built-in Desmos with a scientific ↔ graphing toggle you can switch any time (2026 update).</li>
        <li><b>Section-adaptive</b> — your performance on module 1 sets the difficulty of module 2.</li>
        <li><b>~2 h 14 m</b> total, plus a 10-minute break. Taken in the College Board <b>Bluebook</b> app.</li>
      </ul>
    </div>
    <div class="card">
      <h3 style="margin:0 0 6px">Scoring &amp; content</h3>
      <ul class="tight">
        <li><b>400–1600</b> total: Reading &amp; Writing (200–800) + Math (200–800).</li>
        <li><b>R&amp;W domains:</b> Craft &amp; Structure, Information &amp; Ideas, Standard English Conventions, Expression of Ideas.</li>
        <li><b>Math domains:</b> Algebra, Advanced Math, Problem-Solving &amp; Data Analysis, Geometry &amp; Trigonometry.</li>
        <li><b>No penalty</b> for wrong answers — always answer every question.</li>
        <li><b>No separate essay</b> — the optional SAT Essay was discontinued in 2021.</li>
        <li><b>Where you stand:</b> US average ≈ 1050 (~49th percentile) · 1300 ≈ 86th · 1500 ≈ 98th.</li>
      </ul>
    </div>
  </div>
</section>
"""

_LOGISTICS = """
<section>
  <h2>Dates, registration &amp; scores (US)</h2>
  <p class="sub">Everything you need to actually sit the test in the United States.</p>
  <div class="grid2">
    <div class="card">
      <h3 style="margin:0 0 6px">When &amp; how to register</h3>
      <ul class="tight">
        <li><b>2026–27 dates:</b> one Saturday each in Aug, Sep, Oct, Nov &amp; Dec 2026 and Mar, May &amp; Jun 2027 (~8 sittings). Check College Board for the exact days &amp; deadlines.</li>
        <li><b>Register</b> at <a href="https://satsuite.collegeboard.org/sat/registration" target="_blank" rel="noopener">College Board</a>: create an account, pick a date &amp; center, upload a photo, pay.</li>
        <li><b>Fee:</b> $68 (US) · $111 (international) · +$38 late. <b>Fee waivers</b> for eligible US students.</li>
        <li><b>Device:</b> taken on the College Board <b>Bluebook</b> app on your own laptop/tablet — or borrow one (request ≥30 days ahead).</li>
      </ul>
    </div>
    <div class="card">
      <h3 style="margin:0 0 6px">Scores &amp; official prep</h3>
      <ul class="tight">
        <li><b>Scores</b> post within about a few days to two weeks; send free reports to colleges. Many colleges <b>superscore</b> and allow <b>Score Choice</b>.</li>
        <li><b>Practice right here</b> — a timed full-length mock (both sections, scored to 400–1600) lives in the Practice section below, with no app-switching. The official 6 full-lengths also exist in College Board's Bluebook app if you want the exact test-day software.</li>
        <li><b>Bluebook test-day tools:</b> highlighter/annotation, line reader, answer-eliminator, flag-for-review, and the built-in Desmos calculator.</li>
        <li><b>Accommodations</b> (extended time, extra breaks, etc.) via College Board <b>SSD</b> — now incl. text-to-speech &amp; screen-reader support for Math (Spring 2026).</li>
      </ul>
    </div>
  </div>
  <div class="card" style="margin-top:14px">
    <b>New here? Start with a diagnostic.</b> Take the quick practice test below to gauge your level, drill the videos &amp; flashcards for weak topics, then take a full-length mock right here in the app.
    &nbsp; <span class="pill">PSAT / NMSQT</span> The PSAT is the SAT's practice run (grades 10–11) and qualifies juniors for National Merit — same skills, same prep here.
  </div>
  <div class="card" style="margin-top:14px">
    <b>What's new for 2026–27:</b> the Bluebook timer now pauses briefly if you accidentally exit the app, College Board added another official full-length practice test, and the Desmos calculator lets you switch between scientific and graphing modes at any time.
  </div>
</section>
"""

_BODY_REST = """
<section>
  <h2>Video lessons</h2>
  <p class="sub">Curated, verified SAT-prep videos. Tap a tab to switch topic.</p>
  <div class="tabs" id="vtabs"></div>
  <div class="vgrid" id="vgrid"></div>
</section>

<section>
  <h2>Flashcards</h2>
  <p class="sub">Tap a card to flip. Quick revision for the formulas, words, and rules that show up most.</p>
  <div class="tabs" id="ftabs"></div>
  <div class="fcrow" id="fcrow"></div>
</section>

<section id="practice-sec">
  <h2>Practice tests — in-app, no app-switching</h2>
  <p class="sub">Take a timed <b>full-length mock</b> (both sections, scored to 400–1600) or a quick single-section diagnostic — all inside AI Pathshala. Sign in to score and track your progress.</p>
  <div class="card">
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <button class="btn" id="mockBtn" onclick="startFullMock()">Start full-length mock (both sections)</button>
      <span style="color:#5b7088;font-size:13px">or a quick diagnostic:</span>
      <select id="subj">
        <option value="sat_math">SAT Math</option>
        <option value="sat_reading_writing">SAT Reading &amp; Writing</option>
      </select>
      <button class="btn" id="startBtn" style="background:#e6eef7;color:#0a2a52" onclick="startPractice()">Quick diagnostic</button>
      <span id="pstatus" style="font-size:13px;color:#5b7088"></span>
    </div>
    <div id="mockbar" style="display:none;margin-top:14px;font-size:14px"></div>
    <div id="practice"></div>
  </div>
</section>
"""

_SCRIPT = """
<script>
(function(){
  var D = window.SAT_DATA;
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

  // ---- fact bar ----
  document.getElementById('factbar').innerHTML = D.facts.map(function(f){
    return '<div class="fact">'+esc(f[0])+'<b>'+esc(f[1])+'</b></div>';
  }).join('');

  // ---- videos ----
  var vsecs = Object.keys(D.videos);
  function playFr(fr){
    var id = fr.getAttribute('data-id');
    if(!id || fr.querySelector('iframe')) return;
    fr.style.backgroundImage='none';
    fr.innerHTML='<iframe src="https://www.youtube-nocookie.com/embed/'+id+
      '?autoplay=1&rel=0" title="Video lesson" allowfullscreen '+
      'allow="autoplay; encrypted-media; picture-in-picture"></iframe>';
  }
  function renderVideos(sec){
    document.getElementById('vgrid').innerHTML = D.videos[sec].map(function(v){
      var thumb='https://i.ytimg.com/vi/'+esc(v.id)+'/hqdefault.jpg';
      return '<div class="vid"><div class="fr" data-id="'+esc(v.id)+'" '+
        'role="button" tabindex="0" aria-label="Play: '+esc(v.title)+'" '+
        'style="background-image:url('+thumb+')"><span class="pbtn"></span></div>'+
        '<div class="t">'+esc(v.title)+'</div></div>';
    }).join('');
    Array.prototype.forEach.call(document.querySelectorAll('#vgrid .fr'),function(fr){
      fr.addEventListener('click',function(){playFr(fr);});
      fr.addEventListener('keydown',function(e){
        if(e.key==='Enter'||e.key===' '){e.preventDefault();playFr(fr);}
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('#vtabs .tab'),function(b){
      b.classList.toggle('on', b.dataset.sec===sec);
    });
  }
  document.getElementById('vtabs').innerHTML = vsecs.map(function(s,i){
    return '<button class="tab'+(i===0?' on':'')+'" data-sec="'+esc(s)+'">'+esc(s)+'</button>';
  }).join('');
  Array.prototype.forEach.call(document.querySelectorAll('#vtabs .tab'),function(b){
    b.onclick=function(){renderVideos(b.dataset.sec);};
  });
  renderVideos(vsecs[0]);

  // ---- flashcards ----
  var fsecs = Object.keys(D.flashcards);
  function renderCards(sec){
    document.getElementById('fcrow').innerHTML = D.flashcards[sec].map(function(c){
      return '<div class="fc" onclick="this.classList.toggle(\\'flip\\')">'+
        '<div class="fc-in"><div class="fc-f">'+esc(c.q)+'</div>'+
        '<div class="fc-b">'+esc(c.a)+'</div></div></div>';
    }).join('');
    Array.prototype.forEach.call(document.querySelectorAll('#ftabs .tab'),function(b){
      b.classList.toggle('on', b.dataset.sec===sec);
    });
  }
  document.getElementById('ftabs').innerHTML = fsecs.map(function(s,i){
    return '<button class="tab'+(i===0?' on':'')+'" data-sec="'+esc(s)+'">'+esc(s)+'</button>';
  }).join('');
  Array.prototype.forEach.call(document.querySelectorAll('#ftabs .tab'),function(b){
    b.onclick=function(){renderCards(b.dataset.sec);};
  });
  renderCards(fsecs[0]);

  // ---- practice test ----
  var LETTERS = ['A','B','C','D','E','F'];
  var curTest = null;     // current generated test {id, subject, questions}
  var answers = {};
  var curMode = 'diag';   // 'diag' | 'mock'
  var mock = null;        // {idx, plan:[...], scores:[...]}
  var timer = null, timeLeft = 0;

  function authHeaders(){
    var t = localStorage.getItem('pathshala_token');
    return t ? {'Authorization':'Bearer '+t} : null;
  }
  function signinMsg(){
    return '<div class="err">Please <a href="/landing">sign in</a> to take a scored test. The lessons, videos, flashcards and syllabus above are free to use.</div>';
  }
  function setBusy(b, msg){
    var sb=document.getElementById('startBtn'), mb=document.getElementById('mockBtn');
    if(sb) sb.disabled=b; if(mb) mb.disabled=b;
    document.getElementById('pstatus').innerHTML = b ? ('<span class="spin"></span> '+(msg||'')) : '';
  }
  async function genTest(subject, minutes){
    var fd = new URLSearchParams();
    fd.set('exam','sat'); fd.set('subject',subject); fd.set('target_minutes',String(minutes));
    var r = await fetch('/api/practice/generate',{method:'POST',
      headers:Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, authHeaders()),
      body:fd.toString()});
    if(!r.ok) throw new Error('HTTP '+r.status+' '+(await r.text()).slice(0,160));
    return await r.json();
  }
  function sectionEst(score){ return Math.max(200, Math.min(800, Math.round(200 + (score.pct||0)*600))); }
  function stopTimer(){ if(timer){ clearInterval(timer); timer=null; } }
  function startTimer(secs, label){
    stopTimer(); timeLeft = secs;
    var bar = document.getElementById('mockbar');
    function tick(){
      var m=Math.floor(timeLeft/60), s=timeLeft%60;
      bar.innerHTML = '<b>'+esc(label)+'</b> &nbsp; ⏱ '+m+':'+(s<10?'0':'')+s+
        ' <span class="note" style="margin:0">(auto-submits at 0:00)</span>';
      if(timeLeft<=0){ stopTimer(); window.submitPractice(); return; }
      timeLeft--;
    }
    tick(); timer = setInterval(tick, 1000);
  }

  window.startPractice = async function(){
    if(!authHeaders()){ document.getElementById('practice').innerHTML = signinMsg(); return; }
    curMode = 'diag'; stopTimer(); document.getElementById('mockbar').style.display='none';
    var subject = document.getElementById('subj').value;
    setBusy(true,'Building your test…');
    document.getElementById('practice').innerHTML=''; answers={};
    try{ curTest = await genTest(subject, 20); renderTest(curTest); }
    catch(e){ document.getElementById('practice').innerHTML = '<div class="err">Could not start the test: '+esc(e.message)+'</div>'; }
    finally{ setBusy(false); }
  };

  window.startFullMock = async function(){
    if(!authHeaders()){ document.getElementById('practice').innerHTML = signinMsg(); return; }
    curMode = 'mock';
    mock = { idx:0, scores:[], plan:[
      {subject:'sat_reading_writing', label:'Section 1 of 2 · Reading & Writing', gen:40, mins:35},
      {subject:'sat_math', label:'Section 2 of 2 · Math', gen:33, mins:30}
    ]};
    setBusy(true,'Building your full-length mock…');
    document.getElementById('practice').innerHTML='';
    try{ await startMockSection(); }
    catch(e){ document.getElementById('practice').innerHTML = '<div class="err">Could not start the mock: '+esc(e.message)+'</div>'; }
    finally{ setBusy(false); }
  };

  async function startMockSection(){
    var s = mock.plan[mock.idx];
    answers = {};
    curTest = await genTest(s.subject, s.gen);
    document.getElementById('mockbar').style.display='block';
    renderTest(curTest, s.label);
    startTimer(s.mins*60, s.label);
  }

  function renderTest(t, banner){
    var qs = t.questions || [];
    var html = banner ? '<div class="note" style="margin:0 0 8px;font-weight:700;color:#0a2a52">'+esc(banner)+'</div>' : '';
    html += '<div class="note" style="margin:0 0 6px">'+qs.length+' questions</div>';
    qs.forEach(function(q,qi){
      html += '<div class="q" id="q_'+esc(q.id)+'"><div class="qt">'+(qi+1)+'. '+esc(q.question_text)+'</div>';
      var opts = q.options || [];
      if(opts.length){
        opts.forEach(function(o,oi){
          var letter = LETTERS[oi];
          html += '<label class="opt" data-q="'+esc(q.id)+'" data-l="'+letter+'">'+
            '<input type="radio" name="r_'+esc(q.id)+'" value="'+letter+'" style="margin-right:8px">'+
            '<b>'+letter+'.</b> '+esc(o)+'</label>';
        });
      } else {
        html += '<div class="note">'+esc(q.question_text)+'</div>';
      }
      html += '</div>';
    });
    html += '<button class="btn" id="submitBtn" onclick="submitPractice()">Submit &amp; score</button>';
    document.getElementById('practice').innerHTML = html;
    Array.prototype.forEach.call(document.querySelectorAll('.opt'),function(lab){
      lab.onclick=function(){
        var qid=lab.dataset.q, l=lab.dataset.l;
        answers[qid]=l;
        Array.prototype.forEach.call(document.querySelectorAll('.opt[data-q="'+CSS.escape(qid)+'"]'),function(o){o.classList.remove('sel');});
        lab.classList.add('sel');
      };
    });
  }

  window.submitPractice = async function(){
    if(!curTest) return;
    stopTimer();
    var btn = document.getElementById('submitBtn');
    if(btn){ btn.disabled=true; btn.innerHTML='<span class="spin"></span> Scoring…'; }
    try{
      var r = await fetch('/api/practice/'+encodeURIComponent(curTest.id)+'/submit',{method:'POST',
        headers:Object.assign({'Content-Type':'application/json'}, authHeaders()),
        body:JSON.stringify(answers)});
      if(!r.ok) throw new Error('HTTP '+r.status+' '+(await r.text()).slice(0,160));
      var data = await r.json();
      if(curMode==='mock') handleMockSection(data.score); else showScore(data.score);
    }catch(e){
      document.getElementById('practice').insertAdjacentHTML('beforeend','<div class="err">Could not submit: '+esc(e.message)+'</div>');
      if(btn){ btn.disabled=false; btn.textContent='Submit & score'; }
    }
  };

  function annotate(score){
    (score.per_question||[]).forEach(function(pq){
      var qel = document.getElementById('q_'+pq.question_id); if(!qel) return;
      Array.prototype.forEach.call(qel.querySelectorAll('.opt'),function(o){
        var l=(o.dataset.l||'').toLowerCase(), corr=(pq.correct||'').toLowerCase(), chose=(pq.chosen||'').toLowerCase();
        if(l && corr && l.charAt(0)===corr.charAt(0)) o.classList.add('correct');
        else if(l && chose && l.charAt(0)===chose.charAt(0)) o.classList.add('wrong');
        var inp=o.querySelector('input'); if(inp) inp.disabled=true;
      });
      var tag = pq.is_correct ? '<span class="pill" style="background:#e7f7ee;color:#1a7a48">Correct</span>'
                              : '<span class="pill" style="background:#fcebeb;color:#9c2b2b">Review</span>';
      var qt=qel.querySelector('.qt'); if(qt) qt.insertAdjacentHTML('beforeend', tag);
      if(pq.explanation){ qel.insertAdjacentHTML('beforeend', '<div class="expl"><b>Why:</b> '+esc(pq.explanation)+'</div>'); }
    });
    var sb=document.getElementById('submitBtn'); if(sb) sb.remove();
  }

  function showScore(score){
    var pct = Math.round((score.pct||0)*100);
    var subj = (curTest && curTest.subject==='sat_reading_writing') ? 'Reading & Writing' : 'Math';
    var est = sectionEst(score);
    var head = '<div class="scorebox"><div class="big">'+score.total+' / '+score.max+'</div>'+
      '<div>'+pct+'% correct</div>'+
      '<div style="margin-top:8px;font-size:15px">Estimated SAT '+subj+' score: <b>'+est+'</b> <span style="opacity:.7">/ 800</span></div>'+
      '<div style="font-size:12px;color:#cfe0f0;margin-top:5px">Rough estimate from this short diagnostic. The real SAT scores Math + Reading &amp; Writing together out of 1600 (US average around 1050).</div></div>';
    document.getElementById('practice').insertAdjacentHTML('afterbegin', head);
    annotate(score);
    document.getElementById('practice').insertAdjacentHTML('beforeend','<div class="note">Want a full lesson on a topic you missed? Open the <a href="/chat">AI Tutor</a> or rewatch a video above.</div>');
  }

  function findEst(subject){ for(var i=0;i<mock.scores.length;i++){ if(mock.scores[i].subject===subject) return mock.scores[i].est; } return 0; }
  function handleMockSection(score){
    var s = mock.plan[mock.idx];
    mock.scores.push({subject:s.subject, est:sectionEst(score)});
    annotate(score);
    mock.idx++;
    if(mock.idx < mock.plan.length){
      document.getElementById('practice').insertAdjacentHTML('afterbegin',
        '<div class="scorebox"><div>Section complete — estimated <b>'+sectionEst(score)+'</b>/800.</div>'+
        '<div class="note" style="color:#cfe0f0;margin-top:4px">Loading the next section…</div></div>');
      setTimeout(function(){ startMockSection(); }, 1500);
    } else { finishMock(); }
  }
  function finishMock(){
    document.getElementById('mockbar').style.display='none';
    var rwE=findEst('sat_reading_writing'), maE=findEst('sat_math'), total=rwE+maE;
    var head = '<div class="scorebox"><div class="big">'+total+' <span style="opacity:.7;font-size:18px">/ 1600</span></div>'+
      '<div>Estimated full SAT score</div>'+
      '<div style="margin-top:8px;font-size:14px">Reading &amp; Writing <b>'+rwE+'</b> · Math <b>'+maE+'</b> <span style="opacity:.7">(each / 800)</span></div>'+
      '<div style="font-size:12px;color:#cfe0f0;margin-top:5px">Estimate from this mock. US average ≈ 1050 · 1300 ≈ 86th pct · 1500 ≈ 98th.</div></div>';
    document.getElementById('practice').insertAdjacentHTML('afterbegin', head);
    document.getElementById('practice').insertAdjacentHTML('beforeend','<div class="note">Missed topics? Open the <a href="/chat">AI Tutor</a>, rewatch a video, or drill flashcards — then run another mock.</div>');
  }
})();
</script>
"""


def _build_html() -> str:
    data = json.dumps({"videos": _VIDEOS, "flashcards": _FLASHCARDS, "facts": _FACTS})
    return (
        _PROLOGUE
        + _ABOUT
        + _syllabus_section()
        + _LOGISTICS
        + _BODY_REST
        + "<script>window.SAT_DATA = " + data + ";</script>"
        + _SCRIPT
        + _EPILOGUE
    )


_HTML = _build_html()
_NO_CACHE = {"Cache-Control": "no-store, max-age=0"}


@router.get("/sat", response_class=HTMLResponse)
def sat_hub() -> HTMLResponse:
    """Public SAT exam hub — details, videos, flashcards, practice test."""
    return HTMLResponse(_HTML, headers=_NO_CACHE)
