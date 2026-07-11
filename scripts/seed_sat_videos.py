"""prod-192 — seed SAT (US Digital SAT) concept videos into the catalog.

Part of the SAT exam section. Adds real, recognised SAT-prep YouTube
videos as `verified` concept_videos rows with board="SAT". Each id is
oembed-gated here (live + embeddable) before insert; title + channel
come from the oembed response so we store real metadata. Idempotent via
concept_videos.upsert() on the natural key.

The /sat hub page embeds a curated subset of these by id directly;
seeding them here additionally surfaces them in the concept catalog and
ships them via data/concept_videos_seed.json.

After running, re-run scripts/export_concept_seed.py so the new rows land
in data/concept_videos_seed.json and ship to production.

Usage:
    python scripts/seed_sat_videos.py            # verify + insert
    python scripts/seed_sat_videos.py --dry-run  # verify only
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# concept, subject, grade_min, grade_max, youtube_id.
# All board="SAT". Web-sourced from recognised SAT-prep channels;
# oembed-gated at runtime. Subjects align with practice_test SAT
# subjects (sat_math / sat_reading_writing) plus sat_overview.
SAT_VIDEOS = [
    # --- sat_overview ---
    ('Digital SAT — Overview — Digital SAT — Format, Structure & Scoring', 'sat_overview', 11, 12, 'aNjoBgqrKvE'),
    ('Digital SAT — Overview — Khan Academy — Official SAT Prep Overview', 'sat_overview', 11, 12, 'cSD0qVybO9s'),
    ('Digital SAT — Overview — Digital SAT App: Live Demo and Official Practice', 'sat_overview', 11, 12, '33jFcagTHpc'),
    ("Digital SAT — Overview — What's a Good Digital SAT Score in 2024-2025", 'sat_overview', 11, 12, 'UuUIoihME_M'),
    ('Digital SAT — Overview — Digital SAT Study Guide: How the Scoring Algorithm Works', 'sat_overview', 11, 12, 'pFf7HcJ6Q0U'),
    ('Digital SAT — Overview — How To Interpret Your Digital SAT Score Report', 'sat_overview', 11, 12, '43SiM6YVes4'),
    ('Digital SAT — Overview — Digital SAT - Everything You Need to Know in 4 Minutes', 'sat_overview', 11, 12, 'DTBT4bMQXOU'),
    ('Digital SAT — Overview — EVERYTHING You Need to Know about the SAT (2026)', 'sat_overview', 11, 12, '9IC3WMCAAwc'),
    ('Digital SAT — Overview — EVERYTHING You Need to Know about the Digital SAT', 'sat_overview', 11, 12, '06Y6OtRj0n8'),
    ('Digital SAT — Overview — ULTIMATE DIGITAL SAT GUIDE! scoring algorithm, adaptive test, the new structure, and ', 'sat_overview', 11, 12, 'm_X5gRzXSiA'),
    ('Digital SAT — Overview — Know How the Digital SAT is Scored to Hit Your Goal in 2024! (Algorithm Explained)', 'sat_overview', 11, 12, 'HXP17Be8TRY'),
    ('Bluebook & Test Day — Getting Started with the SAT "Bluebook" APP for your digital SAT practice', 'sat_overview', 11, 12, '1bl8BI-3bQs'),
    ('Bluebook & Test Day — How to use Bluebook for Digital SAT Practice', 'sat_overview', 11, 12, 'bUVI0iK740o'),
    ('Bluebook & Test Day — What to expect with the digital SAT on a school day', 'sat_overview', 11, 12, 'HpXSadvUvOM'),
    ('Bluebook & Test Day — What is Digital SAT Adaptive Testing?', 'sat_overview', 11, 12, 'IFYjH9NzhJU'),
    ('Bluebook & Test Day — Digital SAT Full Walkthrough Bluebook Official Practice Exam 11 Reading & Writing Module', 'sat_overview', 11, 12, 'grBJUKXUyLU'),
    ('Bluebook & Test Day — Desmos embedded in the Digital SAT!', 'sat_overview', 11, 12, '74xnNRK-ARs'),
    ('Bluebook & Test Day — How to use Bluebook App for Digital SAT Practice', 'sat_overview', 11, 12, 'bpZ7ZY2EP4w'),
    ('Bluebook & Test Day — Digital SAT: Live Bluebook Demo and Official Practice Resources', 'sat_overview', 11, 12, 'OXfZTfynbuk'),
    ('Desmos Calculator — Desmos Calculator Guide', 'sat_overview', 11, 12, '-pGNBb8M3LQ'),
    ('Desmos Calculator — The Ultimate Desmos SAT Math Guide (12 Game-Changing Strategies)', 'sat_overview', 11, 12, 'l7YOKxT3qJg'),
    ('Desmos Calculator — EVERY DESMOS HACK in Under 9 Minutes | SAT Math', 'sat_overview', 11, 12, 'ZYDcmWtfLkk'),
    ("Desmos Calculator — The Last SAT Desmos Guide You'll Need", 'sat_overview', 11, 12, 'e-O4nwVHQ-Y'),
    ('Desmos Calculator — Ultimate Desmos Guide to Digital SAT Math-Part 3: Core Skills', 'sat_overview', 11, 12, 'kPrClHiw-BM'),
    ('Desmos Calculator — SAT Math: You Can Use Desmos on THESE?!', 'sat_overview', 11, 12, 'ufz3jVNKT18'),
    ('Desmos Calculator — How to Use DESMOS for SAT Math: Solving Single Variable Equations & Number of Solutions', 'sat_overview', 11, 12, '4hCjnen9Cfs'),
    ('Desmos Calculator — Full SAT Desmos Guide (7 Minutes)', 'sat_overview', 11, 12, 'k2UZX09zwSQ'),
    ('Desmos Calculator — Every SAT Math Desmos Hack in 14 Minutes', 'sat_overview', 11, 12, 'ISIAw2DrdxM'),
    ('Desmos Calculator — DESMOS Digital SAT Calculator Strategies', 'sat_overview', 11, 12, 'axFmkTY_O4M'),
    ('Strategy & Score Boost — SAT Math — Strategies for an 800', 'sat_overview', 11, 12, 'Dp291sIwtYk'),
    ('Strategy & Score Boost — R&W — Tips to Break 1500', 'sat_overview', 11, 12, 'FS_nvzsoIyE'),
    ('Strategy & Score Boost — How to Get a Perfect Score on the Digital SAT', 'sat_overview', 11, 12, 'OAyM5pRjNwY'),
    ('Strategy & Score Boost — How to guess on the Digital SAT and ACT (to raise your score!)', 'sat_overview', 11, 12, 'KbK9E4dIMqA'),
    ('Strategy & Score Boost — How to CRAM for the Digital SAT', 'sat_overview', 11, 12, 'N8RP5I2OOxs'),
    ('Strategy & Score Boost — How to Self-Study for the Digital SAT', 'sat_overview', 11, 12, 'hMiiB4PgltQ'),
    ('Strategy & Score Boost — How to Score a 1500+ on the SAT (2025)', 'sat_overview', 11, 12, 'j3LPqlkeP6k'),
    ('Strategy & Score Boost — Time-Saving Tips for the Digital SAT (2024)', 'sat_overview', 11, 12, 'w2cSswdtkjU'),
    ('Strategy & Score Boost — How to Manage Time on the Digital SAT (10 Proven Strategies)', 'sat_overview', 11, 12, 'gMGQHSUFRCU'),
    ('Strategy & Score Boost — Digital SAT Time Management: Two Strategies for Success!', 'sat_overview', 11, 12, 'Pqi1i-HiIrs'),
    ('Strategy & Score Boost — 7 Tips to Improve Your SAT Score | The Princeton Review', 'sat_overview', 11, 12, 'u94HWW4zMsY'),
    ('Strategy & Score Boost — Digital SAT Time Management: 60 Seconds Per Question?', 'sat_overview', 11, 12, 'QBkLRdijiM8'),
    # --- sat_math ---
    ('Math · Algebra — Linear Equations (Algebra)', 'sat_math', 11, 12, 'wBA0TpNy0Wo'),
    ('Math · Algebra — Systems of Linear Equations', 'sat_math', 11, 12, 'iYs57D4Ko0s'),
    ('Math · Algebra — SAT Math on Khan Academy: Solving Linear Equations and Inequalities (Foundations)', 'sat_math', 11, 12, 'vrPbC9fao6Y'),
    ('Math · Algebra — Solving linear equations — Basic example | Math | New SAT', 'sat_math', 11, 12, 'EkBUTZe_SiM'),
    ('Math · Algebra — Advanced SAT Math: Systems of Linear Equations Word Problems', 'sat_math', 11, 12, 'wwJntieAN7Y'),
    ('Math · Algebra — SAT Math Secrets to Solve Systems of Linear Equations FAST (Heart Of Algebra)', 'sat_math', 11, 12, 'FPRmlWU93vQ'),
    ('Math · Algebra — SAT Math: Solving Linear Functions Like a Pro! (Heart Of Algebra Section)', 'sat_math', 11, 12, '1SBTKwUDAtk'),
    ('Math · Algebra — SAT Practice Questions: Solve Math and Linear Equations Questions', 'sat_math', 11, 12, 'YAgzJwJ2uMI'),
    ('Math · Algebra — SAT MATH || Heart of Algebra || Solving Linear Equations', 'sat_math', 11, 12, '8acvma5oVnQ'),
    ('Math · Algebra — SAT Math section - Heart of Algebra - solving linear equations and inequalities', 'sat_math', 11, 12, 'juie0cTHPfk'),
    ('Math · Algebra — Systems of Linear Equations on the SAT — Fast & Easy Desmos Calculator Tricks, 2 examples ', 'sat_math', 11, 12, '3fOwcTwxeFQ'),
    ('Math · Algebra — SAT Math Lesson: Systems of Linear Equations, Solving for an Expression', 'sat_math', 11, 12, 'ttcAQceD9jQ'),
    ('Math · Advanced Math — SAT Math on Khan Academy: Solving Quadratic Equations (Foundations)', 'sat_math', 11, 12, 'zvczFfWx40k'),
    ('Math · Advanced Math — SAT Math on Khan Academy: Quadratic and Exponential Word Problems (Foundations)', 'sat_math', 11, 12, 'YdvF3fVjLQk'),
    ('Math · Advanced Math — SAT Math on Khan Academy: Factoring Quadratic and Polynomial Expressions (Foundations)', 'sat_math', 11, 12, 'bOe7t_ibbJM'),
    ('Math · Advanced Math — SAT Math on Khan Academy: Exponential Graphs (Foundations)', 'sat_math', 11, 12, 'WlW6oZ6_GNY'),
    ('Math · Advanced Math — SAT Math Part 9 - Properties of Exponents and Powers', 'sat_math', 11, 12, '52P9nqQFSJ4'),
    ('Math · Advanced Math — SAT Math Part 18 - Polynomials and Quadratic Functions', 'sat_math', 11, 12, '6h4xeeonHu4'),
    ('Math · Advanced Math — Solving Rational Equations and Factoring Trinomials - SAT Math Part 3', 'sat_math', 11, 12, 'iZ2-yXzLqCg'),
    ('Math · Advanced Math — Long Division of Polynomials - SAT Math Part 19', 'sat_math', 11, 12, 'xbrEEnmXQvg'),
    ('Math · Advanced Math — How to HACK the Hard Math Section of the Digital SAT', 'sat_math', 11, 12, 'XZKYc12t3oE'),
    ('Math · Advanced Math — SAT Quadratic Equations Guide 2023: What You Need to Know', 'sat_math', 11, 12, 'fQG8oAsNy6g'),
    ('Math · Data Analysis — Ratios, rates, and proportions — Basic example | Math | SAT | Khan Academy', 'sat_math', 11, 12, '96fLrtyAiD8'),
    ('Math · Data Analysis — Ratios, rates, and proportions — Harder example | Math | SAT | Khan Academy', 'sat_math', 11, 12, 'qX0YQwCw5l4'),
    ('Math · Data Analysis — SAT Math on Khan Academy: Ratios, Rates, and Proportions (Foundations)', 'sat_math', 11, 12, 'HFticqOjvH4'),
    ('Math · Data Analysis — SAT Math on Khan Academy: Scatterplots (Foundations)', 'sat_math', 11, 12, 'EBsGtgRs2YY'),
    ('Math · Data Analysis — SAT Math on Khan Academy: Percentages (Foundations)', 'sat_math', 11, 12, 'EuzcYMTIDjg'),
    ('Math · Data Analysis — Data & Statistics - Mean, Median, Mode, Range, & Standard Deviation - SAT Math Part 44', 'sat_math', 11, 12, 'W8NaUtkM46o'),
    ('Math · Data Analysis — SAT MATH: Two-Variable Data: Models & Scatterplots', 'sat_math', 11, 12, 'mVEgpc2UHvQ'),
    ('Math · Data Analysis — SAT Math Practice: Mean, Median, Mode, and Standard Deviation Problems', 'sat_math', 11, 12, 'xOu4xDpFjNE'),
    ('Math · Geometry & Trig — Geometry & Trigonometry', 'sat_math', 11, 12, 'Vwtux_sW9Zs'),
    ('Math · Geometry & Trig — Digital SAT Math - Skills Insight #4: Geometry & Trigonometry', 'sat_math', 11, 12, 'MKcBn4AJvK0'),
    ('Math · Geometry & Trig — Digital SAT Math Medium: Geometry and Trigonometry', 'sat_math', 11, 12, 'j1D6abbz1Kk'),
    ('Math · Geometry & Trig — Digital SAT Math Advanced: Geometry and Trigonometry', 'sat_math', 11, 12, 'WGJ_-cMFtlo'),
    ('Math · Geometry & Trig — Overcoming Digital SAT Math - Trigonometry', 'sat_math', 11, 12, '4d6vzhYE0bc'),
    ('Math · Geometry & Trig — SAT MATH: Lines, Angles, and Triangles', 'sat_math', 11, 12, 'zRJfZ9UUwR4'),
    ('Math · Geometry & Trig — SAT MATH: Right Triangles & Trigonometry', 'sat_math', 11, 12, 'L2Jd-ndBA_M'),
    ('Math · Geometry & Trig — SAT MATH: Special Right Triangle Rules', 'sat_math', 11, 12, 'GXOZslaKx8c'),
    ('Math · Geometry & Trig — SAT Practice | Lines, Angles, & Triangles', 'sat_math', 11, 12, '8pM3dLsMdPo'),
    ('Math · Geometry & Trig — ALL Trigonometry on the SAT!', 'sat_math', 11, 12, 'eTTfZnfdBOQ'),
    ('Math · Geometry & Trig — All of SAT Geometry and Trigonometry', 'sat_math', 11, 12, 'u6LL3Pbo3HI'),
    ('Math · Geometry & Trig — Right triangle trigonometry — Harder example | Math | SAT', 'sat_math', 11, 12, 'ZE40akTB6oo'),
    ('Math · Full Reviews — SAT Math — Full Review', 'sat_math', 11, 12, 'ty7B8VyCnFY'),
    ('Math · Full Reviews — SAT Math Test Prep Online Crash Course Algebra & Geometry Study Guide Review, Functions', 'sat_math', 11, 12, 'yBCAv_NzzPQ'),
    ('Math · Full Reviews — 🌟 The ULTIMATE Digital SAT Math Video 🌟 - 38 Strategies to Nail an 800 on Digital SAT Ma', 'sat_math', 11, 12, '5VdaJ6HYbD8'),
    ('Math · Full Reviews — SAT Math FULL REVIEW for May SAT 2023! Everything you need for an 800!!', 'sat_math', 11, 12, 'T2RBmzk3Xvk'),
    ('Math · Full Reviews — 2026 SAT Math FULL Review & Exam Prep (EVERYTHING YOU NEED TO KNOW!!)', 'sat_math', 11, 12, 'qy9htgwZDkg'),
    ('Math · Full Reviews — All of SAT Math Explained in 26 Minutes', 'sat_math', 11, 12, '1bTkbmHx944'),
    ('Math · Full Reviews — How to get a PERFECT 800 on the SAT Math (2024)', 'sat_math', 11, 12, 'gqoXJPNftAc'),
    ('Math · Full Reviews — November SAT Math Crash Course: 9 Concepts You Will See On Test Day!', 'sat_math', 11, 12, 'OKGrV3e9aZk'),
    ('Math · Full Reviews — Digital SAT Math Crash Course - Day 1 (Part 1) - Taking Advantage of the Desmos Calculat', 'sat_math', 11, 12, 'f2t2fVmBZ9Q'),
    ('Math · Full Reviews — ANYONE can get an 800 SAT math, just give me 20 minutes of your time', 'sat_math', 11, 12, 's0hKu71T4Wg'),
    # --- sat_reading_writing ---
    ('R&W · Craft & Structure — Words in Context (Vocab) Questions on the Digital SAT: Strategies & Practice', 'sat_reading_writing', 11, 12, 'dFQaoOGYhTk'),
    ('R&W · Craft & Structure — Digital SAT Reading Strategy: Vocab-in-Context Questions', 'sat_reading_writing', 11, 12, 'tIlTZwt5VQA'),
    ('R&W · Craft & Structure — Avoid This Trap on Digital SAT Reading and Writing Test Structure and Purpose Questi', 'sat_reading_writing', 11, 12, '25KmWnTXu8o'),
    ('R&W · Craft & Structure — Khan Academy "Text Structure & Purpose" Questions (Advanced)', 'sat_reading_writing', 11, 12, '1Iz8AbdIPgc'),
    ('R&W · Craft & Structure — How to Solve DSAT Text Structure and Purpose Questions', 'sat_reading_writing', 11, 12, 'U3qWNZgoejU'),
    ('R&W · Craft & Structure — SAT Cross-Text Connections (Double Passages) Strategies & Practice', 'sat_reading_writing', 11, 12, 'hBbZtGoBA44'),
    ('R&W · Craft & Structure — Cross-Text Connections (SAT Question Bank)', 'sat_reading_writing', 11, 12, 'FcsbLmdlyX8'),
    ('R&W · Craft & Structure — FREE Digital SAT English Class 2 (Paired Passages, Sentence Function, Review)', 'sat_reading_writing', 11, 12, 'T5ie7Ln6ShM'),
    ('R&W · Information & Ideas — Command of Evidence Questions on the Digital SAT: Strategies & Practice', 'sat_reading_writing', 11, 12, 'POcYofMngBw'),
    ('R&W · Information & Ideas — Inference Questions on the Digital SAT: Strategies & Practice (SAT Reading Prep)', 'sat_reading_writing', 11, 12, 'ANkK_ecpZZY'),
    ('R&W · Information & Ideas — Digital SAT: Command of Quantitative Evidence (GRAPHS)', 'sat_reading_writing', 11, 12, 'J7Ka3-JwGxY'),
    ('R&W · Information & Ideas — Digital SAT Command of Evidence Scientific! Use Simple Reasoning to Support Or Wea', 'sat_reading_writing', 11, 12, 'X0dl0a77RaA'),
    ('R&W · Information & Ideas — Digital SAT Hardest Reading Questions SOLVED!', 'sat_reading_writing', 11, 12, '972usSOy9o4'),
    ('R&W · Information & Ideas — Ultimate Inferences Hack for Digital SAT Reading Exam', 'sat_reading_writing', 11, 12, 'j2HtBo_33do'),
    ('R&W · Information & Ideas — A Better Way to Solve Quantitative Command of Evidence Questions - SAT Reading', 'sat_reading_writing', 11, 12, '59AWeIE8jQc'),
    ('R&W · Information & Ideas — SAT Inference Question Strategies: Raise Your English Score by 80 Points', 'sat_reading_writing', 11, 12, 'PWgMcZLDpkE'),
    ('R&W · Information & Ideas — SAT English Hacks | Command of Evidence Textual', 'sat_reading_writing', 11, 12, 'CsBB1CWLvJs'),
    ('R&W · Information & Ideas — Digital SAT Reading & Writing Tips: Logically Complete the Text', 'sat_reading_writing', 11, 12, 'eu6kmFdl7O8'),
    ('R&W · Information & Ideas — DSAT R&W: A Recurring Pattern on Inference ("Logically Completing the Text") Quest', 'sat_reading_writing', 11, 12, '9UjGEN1akXU'),
    ('R&W · Grammar & Conventions — Every Grammar Rule in 15 Minutes', 'sat_reading_writing', 11, 12, 'NLz8CRdMvuI'),
    ('R&W · Grammar & Conventions — 5 Grammar Hacks', 'sat_reading_writing', 11, 12, '4jgnbFnXiYs'),
    ('R&W · Grammar & Conventions — Digital SAT Standard English Conventions: Boundaries', 'sat_reading_writing', 11, 12, 'M4EOqgOC4ks'),
    ('R&W · Grammar & Conventions — Digital SAT Standard English Conventions: Verb Forms', 'sat_reading_writing', 11, 12, '1ScLbJUnbzk'),
    ('R&W · Grammar & Conventions — Every SAT Punctuation Rule You Need (in 30 minutes)', 'sat_reading_writing', 11, 12, 'pi19m9uIAh8'),
    ('R&W · Grammar & Conventions — Digital SAT Writing: Verb Tenses (Full Guide)', 'sat_reading_writing', 11, 12, 'TBDxpwtH0J4'),
    ('R&W · Grammar & Conventions — All SAT Punctuation Rules in 15 Minutes', 'sat_reading_writing', 11, 12, 'WCHyeJKWD84'),
    ("R&W · Grammar & Conventions — The Last SAT Punctuation Guide You'll Need", 'sat_reading_writing', 11, 12, 'WL61t23IOyE'),
    ('R&W · Grammar & Conventions — Parallelism | SAT/ACT Crash Course', 'sat_reading_writing', 11, 12, '4i43Qk_-TyU'),
    ('R&W · Grammar & Conventions — EVERY SAT Punctuation Rule in 37 Minutes', 'sat_reading_writing', 11, 12, 'ruOYTZfxiqk'),
    ('R&W · Grammar & Conventions — Digital SAT Reading & Writing: Standard English Conventions - Boundaries (Worked', 'sat_reading_writing', 11, 12, 'zeS4pEjPoxg'),
    ('R&W · Grammar & Conventions — Punctuation — overview of the rules and strategies', 'sat_reading_writing', 11, 12, 'uNW4b0yBF40'),
    ('R&W · Expression of Ideas — Rhetorical synthesis — Worked example', 'sat_reading_writing', 11, 12, 'FJnzYoKM_tk'),
    ('R&W · Expression of Ideas — Cracking Rhetorical Synthesis Questions for the Digital SAT (Path to 1600 Series: ', 'sat_reading_writing', 11, 12, 'XhVGMSiK4wk'),
    ('R&W · Expression of Ideas — Digital SAT English - Complete Rhetorical Synthesis Strategy Guide', 'sat_reading_writing', 11, 12, 'q3XpAcPpJAE'),
    ('R&W · Expression of Ideas — Get Ready for the March Digital SAT! Rhetorical Synthesis COMPLETE Breakthrough Pa', 'sat_reading_writing', 11, 12, 'seBwtH3MrWA'),
    ('R&W · Expression of Ideas — Khan Academy Rhetorical Synthesis Questions (Advanced)', 'sat_reading_writing', 11, 12, 'sVtASxq4hng'),
    ('R&W · Expression of Ideas — SAT Transitions — Full Strategy Guide', 'sat_reading_writing', 11, 12, 'WvpDSRB6NNs'),
    ('R&W · Expression of Ideas — Mastering Logical Transitions on the Digital SAT: Your Complete Guide to Grammar S', 'sat_reading_writing', 11, 12, '6MZo4YvrFIE'),
    ('R&W · Expression of Ideas — Transitions Questions on the Digital SAT: Strategies & Practice', 'sat_reading_writing', 11, 12, 'kz_P_b3H0oA'),
    ('R&W · Expression of Ideas — Digital SAT: Transitions (Full 2025 Guide)', 'sat_reading_writing', 11, 12, 'eJ-PMwmareM'),
    ('R&W · Expression of Ideas — Never Miss Another SAT Transition Question', 'sat_reading_writing', 11, 12, 'BhDFdrPA368'),
    ('R&W · Expression of Ideas — Precision and Concision: Evidence Based Writing | Turito | SAT Prep | English', 'sat_reading_writing', 11, 12, 'ZJ6A9scXGqw'),
    ('R&W · Expression of Ideas — SAT Prep: Expression of Ideas', 'sat_reading_writing', 11, 12, '3gAVah_2n-c'),
    ('R&W · Full Reviews — Reading & Writing — Study Guide', 'sat_reading_writing', 11, 12, '1EHXD2eVKzA'),
    ('R&W · Full Reviews — R&W — Question Types & Strategies', 'sat_reading_writing', 11, 12, '8PrSFbEJXvY'),
    ('R&W · Full Reviews — 4 Tips to ROCK the SAT Reading Section', 'sat_reading_writing', 11, 12, 'HfG9vxPzvPM'),
    ('R&W · Full Reviews — All of SAT Reading & Writing in 22 Minutes (2026)', 'sat_reading_writing', 11, 12, 'jTkfge6FeE8'),
    ('R&W · Full Reviews — SAT Reading Strategies To Score a 700+', 'sat_reading_writing', 11, 12, 'Q4ylJ4HxgbE'),
    ('R&W · Full Reviews — SAT Reading Strategies to Score 750+', 'sat_reading_writing', 11, 12, '-XqvhhnrjGk'),
    ('R&W · Full Reviews — SAT Reading & Writing Section - How to Ace It', 'sat_reading_writing', 11, 12, '0fIGJ_KU8xQ'),
    ("R&W · Full Reviews — The Only SAT Reading & Writing Guide You'll Ever Need", 'sat_reading_writing', 11, 12, 'eq4A5_34ueA'),
    ('R&W · Full Reviews — 15 SAT Reading and Writing Section Techniques Every 1600 Scorer Knows', 'sat_reading_writing', 11, 12, 'tXEIoN2ALN4'),
]


def _oembed(video_id: str) -> dict | None:
    url = (
        "https://www.youtube.com/oembed?url="
        f"https://www.youtube.com/watch?v={video_id}&format=json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
            return {"title": d.get("title", ""), "author": d.get("author_name", "")}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_REPO, ".env"))

    from padhai import concept_videos as cv

    added, dead = 0, 0
    print(f"seeding {len(SAT_VIDEOS)} SAT concept videos…")
    print("=" * 64)
    for concept, subject, gmin, gmax, vid in SAT_VIDEOS:
        meta = _oembed(vid)
        if not meta:
            print(f"  [dead]  {concept[:34]:36} {vid} failed oembed")
            dead += 1
            continue
        label = f"{concept[:34]:36} [{meta['author'][:16]}] {meta['title'][:30]}"
        if args.dry_run:
            print(f"  [would] {label}")
            added += 1
            continue
        cv.upsert(
            concept=concept, source="youtube",
            source_url=f"https://www.youtube.com/watch?v={vid}",
            title=meta["title"], channel=meta["author"],
            subject=subject, board="SAT", grade_min=gmin, grade_max=gmax,
            quality_tier="verified", language="en",
            curator_note="prod-192: SAT exam section, oembed-verified",
        )
        print(f"  [added] {label}")
        added += 1

    print("=" * 64)
    print(f"{'would add' if args.dry_run else 'added'}: {added}   dead-skipped: {dead}")
    with contextlib.suppress(Exception):
        st = cv.stats()
        print(f"catalog now: {st.get('by_quality_tier')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
