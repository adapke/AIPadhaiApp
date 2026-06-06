#!/usr/bin/env python3
"""prod-12 — Emit PYQ seed JSON files across boards / exams / mediums.

Why the seed lives in Python (not raw JSON in data/pyq/):
  * Auditable in one place — translators / SMEs can review all batches
    side-by-side instead of hopping between 25+ files.
  * Easy to add new batches: extend the BATCHES list, re-run the script.
  * The script is the source of truth; the JSON files are derived
    artifacts that can be regenerated.

What these questions ARE:
  * Hand-written exam-style practice questions in the format of the
    target exam.
  * Cover the typical chapters / topics for each (board, grade, subject).
  * Mix of easy / medium / hard within each batch.

What these questions are NOT:
  * Verbatim past-year-paper questions (those need licensing or
    OCR + manual review of public papers — content-acquisition work).
  * Exhaustive coverage — each batch carries 5-10 questions, not the
    full 50-100 of a real paper.

To replace a batch with real PYQs later, just drop a JSON file in
data/pyq/ with the same shape; the existing `scripts/import_pyq.py`
loader handles it.

Usage:
  python scripts/build_pyq_seed.py            # write all batches
  python scripts/build_pyq_seed.py --check    # validate without writing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "pyq"


# =============================================================================
# BATCHES — each entry becomes one JSON file under data/pyq/
# =============================================================================
# Pre-existing seeds (preserved, not rewritten by this script):
#   jee_main_2024_mathematics.json    (20 q — prod-4)
#   jee_main_2024_physics.json        (20 q — prod-4)
#   jee_main_2024_chemistry.json      (20 q — prod-4)

BATCHES: list[dict] = []

# ----- JEE Advanced 2024 (national engineering entrance, harder than Main) ---
BATCHES.append({
    "filename": "jee_advanced_2024_mathematics.json",
    "source": "jee_advanced_2024_paper1_mathematics",
    "default_board": "jee", "default_grade": 12,
    "default_subject": "mathematics",
    "default_year": 2024, "default_paper": "advanced",
    "questions": [
        {
            "question_text": "If f(x) = x^3 - 3x + 1, the number of real roots is:",
            "options": ["1", "2", "3", "0"], "correct_answer": "C",
            "chapter": "Theory of Equations", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "The value of integral from 0 to 1 of x*e^x dx is:",
            "options": ["1", "e-1", "1/e", "e"], "correct_answer": "A",
            "chapter": "Integral Calculus", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "The number of complex solutions of z^4 = 1 is:",
            "options": ["1", "2", "3", "4"], "correct_answer": "D",
            "chapter": "Complex Numbers", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "If A is a 3x3 matrix with det(A) = 5, then det(2A) =",
            "options": ["10", "40", "30", "5"], "correct_answer": "B",
            "chapter": "Matrices and Determinants", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "The eccentricity of a hyperbola x^2/9 - y^2/16 = 1 is:",
            "options": ["3/5", "4/3", "5/3", "5/4"], "correct_answer": "C",
            "chapter": "Conic Sections", "difficulty": "hard", "marks": 4,
        },
        {
            "question_text": "If sin x + cos x = 1/2, then sin(2x) =",
            "options": ["-3/4", "3/4", "-1/4", "1/4"], "correct_answer": "A",
            "chapter": "Trigonometry", "difficulty": "hard", "marks": 4,
        },
        {
            "question_text": "Solve: log_2 (x) + log_2 (x-1) = 1",
            "options": ["2", "1", "-1", "no solution"], "correct_answer": "A",
            "chapter": "Logarithms", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "The vectors (1,2,3), (2,4,6), (1,1,1) are:",
            "options": ["linearly independent", "linearly dependent", "orthogonal", "unit vectors"],
            "correct_answer": "B",
            "chapter": "Vector Algebra", "difficulty": "easy", "marks": 4,
        },
    ],
})

BATCHES.append({
    "filename": "jee_advanced_2024_physics.json",
    "source": "jee_advanced_2024_paper1_physics",
    "default_board": "jee", "default_grade": 12,
    "default_subject": "physics",
    "default_year": 2024, "default_paper": "advanced",
    "questions": [
        {
            "question_text": "A particle in SHM has amplitude A and angular frequency ω. Max speed is:",
            "options": ["Aω", "A/ω", "Aω^2", "A^2 ω"], "correct_answer": "A",
            "chapter": "Oscillations", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Two waves of frequencies 256 Hz and 260 Hz beat together. Beat frequency is:",
            "options": ["2 Hz", "4 Hz", "8 Hz", "516 Hz"], "correct_answer": "B",
            "chapter": "Waves", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Photon of wavelength 600 nm — its energy is approximately:",
            "options": ["2.07 eV", "4.14 eV", "1.0 eV", "6.6 eV"], "correct_answer": "A",
            "chapter": "Dual Nature", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "Radioactive isotope half-life 5 years. After 15 years, fraction remaining:",
            "options": ["1/2", "1/4", "1/8", "1/16"], "correct_answer": "C",
            "chapter": "Nuclei", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Young's modulus has dimensions:",
            "options": ["ML^-1 T^-2", "MLT^-2", "ML^2 T^-2", "MT^-2"],
            "correct_answer": "A",
            "chapter": "Mechanical Properties of Solids", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "In Young's double-slit experiment, fringe width is proportional to:",
            "options": ["d", "1/d", "d^2", "1/d^2"], "correct_answer": "B",
            "chapter": "Wave Optics", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "A capacitor C charged to V is connected to identical uncharged C. Final V on each:",
            "options": ["V", "V/2", "2V", "0"], "correct_answer": "B",
            "chapter": "Capacitance", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "Lorentz force on a charge q moving at v in magnetic field B is:",
            "options": ["qvB", "qv x B (vector)", "qE", "q/vB"], "correct_answer": "B",
            "chapter": "Moving Charges", "difficulty": "easy", "marks": 4,
        },
    ],
})

BATCHES.append({
    "filename": "jee_advanced_2024_chemistry.json",
    "source": "jee_advanced_2024_paper1_chemistry",
    "default_board": "jee", "default_grade": 12,
    "default_subject": "chemistry",
    "default_year": 2024, "default_paper": "advanced",
    "questions": [
        {
            "question_text": "Number of σ bonds in benzene (C6H6) is:",
            "options": ["6", "9", "12", "3"], "correct_answer": "C",
            "chapter": "Hydrocarbons", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "pH of 0.001 M HCl solution is:",
            "options": ["1", "2", "3", "4"], "correct_answer": "C",
            "chapter": "Equilibrium", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Which has the highest first ionization energy?",
            "options": ["Na", "Mg", "Al", "Si"], "correct_answer": "D",
            "chapter": "Periodic Properties", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "Aldol condensation of acetaldehyde gives:",
            "options": ["acetone", "3-hydroxybutanal", "ethanol", "acetic acid"],
            "correct_answer": "B",
            "chapter": "Aldehydes and Ketones", "difficulty": "hard", "marks": 4,
        },
        {
            "question_text": "Coordination number of Na+ in NaCl crystal lattice is:",
            "options": ["4", "6", "8", "12"], "correct_answer": "B",
            "chapter": "Solid State", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "Atomic radius increases down a group because of:",
            "options": ["increasing nuclear charge", "added electron shells", "decreasing effective nuclear charge", "both B and C"],
            "correct_answer": "D",
            "chapter": "Periodic Properties", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Enthalpy of neutralization of strong acid + strong base is:",
            "options": ["-57.1 kJ/mol", "-285 kJ/mol", "0", "+57 kJ/mol"],
            "correct_answer": "A",
            "chapter": "Thermodynamics", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "Bohr radius (n=1, H atom) is approximately:",
            "options": ["0.529 Å", "1.06 Å", "5.29 Å", "0.0529 Å"],
            "correct_answer": "A",
            "chapter": "Atomic Structure", "difficulty": "easy", "marks": 4,
        },
    ],
})

# ----- NEET 2024 (national medical entrance) ---------------------------------
BATCHES.append({
    "filename": "neet_2024_biology.json",
    "source": "neet_2024_biology",
    "default_board": "neet", "default_grade": 12,
    "default_subject": "biology",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "The basic unit of life is:",
            "options": ["tissue", "cell", "organ", "molecule"], "correct_answer": "B",
            "chapter": "Cell Biology", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Which blood group is the universal donor?",
            "options": ["A+", "B+", "AB+", "O-"], "correct_answer": "D",
            "chapter": "Body Fluids", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Photosynthesis occurs in:",
            "options": ["mitochondria", "chloroplast", "nucleus", "ribosome"],
            "correct_answer": "B",
            "chapter": "Photosynthesis", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Number of chromosomes in normal human somatic cell:",
            "options": ["23", "46", "44", "48"], "correct_answer": "B",
            "chapter": "Cell Division", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Insulin is produced by which cells of the pancreas?",
            "options": ["alpha cells", "beta cells", "delta cells", "F cells"],
            "correct_answer": "B",
            "chapter": "Chemical Coordination", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "The functional unit of kidney is:",
            "options": ["neuron", "nephron", "alveolus", "villi"],
            "correct_answer": "B",
            "chapter": "Excretory System", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Mendel's law of segregation deals with:",
            "options": ["one gene", "two genes", "three genes", "chromosomes"],
            "correct_answer": "A",
            "chapter": "Genetics", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "DNA replication is:",
            "options": ["conservative", "semi-conservative", "dispersive", "non-conservative"],
            "correct_answer": "B",
            "chapter": "Molecular Biology", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "Which structure carries oxygen in red blood cells?",
            "options": ["plasma", "hemoglobin", "platelets", "WBC"],
            "correct_answer": "B",
            "chapter": "Body Fluids", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "The smallest functional unit of the lung is:",
            "options": ["bronchus", "alveolus", "trachea", "diaphragm"],
            "correct_answer": "B",
            "chapter": "Respiratory System", "difficulty": "easy", "marks": 4,
        },
    ],
})

BATCHES.append({
    "filename": "neet_2024_physics.json",
    "source": "neet_2024_physics",
    "default_board": "neet", "default_grade": 12,
    "default_subject": "physics",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "SI unit of electric current is:",
            "options": ["volt", "ampere", "ohm", "watt"], "correct_answer": "B",
            "chapter": "Current Electricity", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "A body falls freely. After 2 seconds its velocity is (g=10 m/s²):",
            "options": ["10 m/s", "20 m/s", "40 m/s", "5 m/s"],
            "correct_answer": "B",
            "chapter": "Kinematics", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Refractive index of vacuum is:",
            "options": ["0", "1", "1.5", "infinity"], "correct_answer": "B",
            "chapter": "Ray Optics", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Lens formula relating object distance u, image distance v, focal length f:",
            "options": ["1/f = 1/v - 1/u", "1/f = 1/v + 1/u", "f = u + v", "f = uv/(u+v)"],
            "correct_answer": "A",
            "chapter": "Ray Optics", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "Two resistors 6Ω and 3Ω in series. Equivalent resistance:",
            "options": ["2Ω", "9Ω", "18Ω", "0.5Ω"], "correct_answer": "B",
            "chapter": "Current Electricity", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Power dissipated when 5A flows through 4Ω resistor:",
            "options": ["20 W", "100 W", "1.25 W", "0.8 W"], "correct_answer": "B",
            "chapter": "Current Electricity", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "The wavelength of an electron with kinetic energy 100 eV is approximately:",
            "options": ["0.12 nm", "1.2 nm", "12 nm", "120 nm"],
            "correct_answer": "A",
            "chapter": "Dual Nature", "difficulty": "hard", "marks": 4,
        },
    ],
})

BATCHES.append({
    "filename": "neet_2024_chemistry.json",
    "source": "neet_2024_chemistry",
    "default_board": "neet", "default_grade": 12,
    "default_subject": "chemistry",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Number of moles in 18 g of water (H2O, molar mass 18 g/mol):",
            "options": ["0.5", "1", "2", "18"], "correct_answer": "B",
            "chapter": "Mole Concept", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Which gas is responsible for ozone layer depletion?",
            "options": ["CO2", "CFCs", "N2", "O2"], "correct_answer": "B",
            "chapter": "Environmental Chemistry", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Functional group of carboxylic acid is:",
            "options": ["-OH", "-COOH", "-CHO", "-NH2"], "correct_answer": "B",
            "chapter": "Organic Chemistry", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Which element exhibits maximum oxidation state?",
            "options": ["Cl (+7)", "F (+1)", "Na (+1)", "Mg (+2)"],
            "correct_answer": "A",
            "chapter": "Redox Reactions", "difficulty": "medium", "marks": 4,
        },
        {
            "question_text": "Acid rain is primarily caused by:",
            "options": ["CO2", "SO2 and NOx", "CFCs", "Methane"],
            "correct_answer": "B",
            "chapter": "Environmental Chemistry", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "VBT predicts the geometry of CH4 to be:",
            "options": ["linear", "trigonal planar", "tetrahedral", "octahedral"],
            "correct_answer": "C",
            "chapter": "Chemical Bonding", "difficulty": "easy", "marks": 4,
        },
        {
            "question_text": "Glucose belongs to which class of compounds?",
            "options": ["protein", "carbohydrate", "fat", "vitamin"],
            "correct_answer": "B",
            "chapter": "Biomolecules", "difficulty": "easy", "marks": 4,
        },
    ],
})

# ----- UPSC Prelims 2024 (civil services) ------------------------------------
BATCHES.append({
    "filename": "upsc_prelims_2024_polity.json",
    "source": "upsc_prelims_2024_gs1_polity",
    "default_board": "upsc", "default_grade": 0,
    "default_subject": "polity",
    "default_year": 2024, "default_paper": "prelims",
    "questions": [
        {
            "question_text": "The 73rd Constitutional Amendment Act, 1992 relates to:",
            "options": ["Panchayati Raj", "Municipalities", "Judiciary", "GST"],
            "correct_answer": "A",
            "chapter": "Local Self-Government", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Who is the ex-officio Chairman of the Rajya Sabha?",
            "options": ["President", "Vice-President", "PM", "Speaker"],
            "correct_answer": "B",
            "chapter": "Parliament", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Article 32 of the Constitution provides:",
            "options": ["Right to Equality", "Right to Constitutional Remedies", "Right to Education", "Right to Property"],
            "correct_answer": "B",
            "chapter": "Fundamental Rights", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "The Directive Principles of State Policy are contained in:",
            "options": ["Part III", "Part IV", "Part V", "Part VI"],
            "correct_answer": "B",
            "chapter": "DPSP", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Indian Constitution came into effect on:",
            "options": ["15 Aug 1947", "26 Jan 1950", "26 Nov 1949", "2 Oct 1947"],
            "correct_answer": "B",
            "chapter": "Constitutional History", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "How many Schedules does the Indian Constitution have today?",
            "options": ["10", "12", "14", "16"],
            "correct_answer": "B",
            "chapter": "Schedules", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Election Commission of India is established by:",
            "options": ["Article 324", "Article 280", "Article 312", "Article 368"],
            "correct_answer": "A",
            "chapter": "Constitutional Bodies", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Which Article deals with the appointment of the Prime Minister?",
            "options": ["Article 74", "Article 75", "Article 76", "Article 78"],
            "correct_answer": "B",
            "chapter": "Executive", "difficulty": "medium", "marks": 2,
        },
    ],
})

BATCHES.append({
    "filename": "upsc_prelims_2024_geography.json",
    "source": "upsc_prelims_2024_gs1_geography",
    "default_board": "upsc", "default_grade": 0,
    "default_subject": "geography",
    "default_year": 2024, "default_paper": "prelims",
    "questions": [
        {
            "question_text": "Tropic of Cancer passes through how many Indian states?",
            "options": ["6", "7", "8", "9"], "correct_answer": "C",
            "chapter": "India Physical", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Highest peak in India is:",
            "options": ["K2", "Kanchenjunga", "Nanda Devi", "Everest"],
            "correct_answer": "B",
            "chapter": "India Physical", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Which river is known as the Sorrow of Bihar?",
            "options": ["Ganga", "Kosi", "Brahmaputra", "Yamuna"],
            "correct_answer": "B",
            "chapter": "Rivers of India", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Largest delta in India is formed by:",
            "options": ["Kaveri", "Krishna", "Sundarbans (Ganga-Brahmaputra)", "Mahanadi"],
            "correct_answer": "C",
            "chapter": "Rivers of India", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Which line separates India from China?",
            "options": ["Radcliffe", "McMahon", "Durand", "Maginot"],
            "correct_answer": "B",
            "chapter": "Boundaries", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Maximum monsoon rainfall in India occurs in:",
            "options": ["June", "July", "August", "September"],
            "correct_answer": "B",
            "chapter": "Climate", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "The Western Ghats are also known as:",
            "options": ["Sahyadri", "Vindhya", "Satpura", "Aravalli"],
            "correct_answer": "A",
            "chapter": "India Physical", "difficulty": "easy", "marks": 2,
        },
    ],
})

BATCHES.append({
    "filename": "upsc_prelims_2024_history.json",
    "source": "upsc_prelims_2024_gs1_history",
    "default_board": "upsc", "default_grade": 0,
    "default_subject": "history",
    "default_year": 2024, "default_paper": "prelims",
    "questions": [
        {
            "question_text": "Who founded the Maurya empire?",
            "options": ["Ashoka", "Chandragupta Maurya", "Bindusara", "Bimbisara"],
            "correct_answer": "B",
            "chapter": "Ancient India", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Battle of Plassey was fought in:",
            "options": ["1757", "1764", "1857", "1761"], "correct_answer": "A",
            "chapter": "Modern India", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Permanent Settlement of Bengal was introduced by:",
            "options": ["Warren Hastings", "Cornwallis", "Wellesley", "Dalhousie"],
            "correct_answer": "B",
            "chapter": "Modern India", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Quit India Movement was launched in:",
            "options": ["1940", "1942", "1944", "1946"], "correct_answer": "B",
            "chapter": "Freedom Struggle", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Who was the founder of the Bahmani Kingdom?",
            "options": ["Alauddin Hasan", "Mahmud Gawan", "Firoz Shah", "Muhammad Shah"],
            "correct_answer": "A",
            "chapter": "Medieval India", "difficulty": "hard", "marks": 2,
        },
        {
            "question_text": "Indus Valley Civilization sites Dholavira is in:",
            "options": ["Punjab", "Gujarat", "Rajasthan", "Haryana"],
            "correct_answer": "B",
            "chapter": "Ancient India", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Vasco da Gama landed at Calicut in:",
            "options": ["1492", "1498", "1500", "1510"], "correct_answer": "B",
            "chapter": "Modern India", "difficulty": "easy", "marks": 2,
        },
    ],
})

BATCHES.append({
    "filename": "upsc_prelims_2024_economy.json",
    "source": "upsc_prelims_2024_gs1_economy",
    "default_board": "upsc", "default_grade": 0,
    "default_subject": "economy",
    "default_year": 2024, "default_paper": "prelims",
    "questions": [
        {
            "question_text": "RBI was nationalised in:",
            "options": ["1947", "1949", "1969", "1991"], "correct_answer": "B",
            "chapter": "Banking", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "GST is a:",
            "options": ["direct tax", "indirect tax", "wealth tax", "income tax"],
            "correct_answer": "B",
            "chapter": "Taxation", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Repo rate is the rate at which:",
            "options": ["RBI lends to banks", "banks lend to public", "banks lend to govt", "Govt lends to RBI"],
            "correct_answer": "A",
            "chapter": "Monetary Policy", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "First five-year plan focused on:",
            "options": ["industry", "agriculture", "services", "defense"],
            "correct_answer": "B",
            "chapter": "Planning", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Inflation measured by Wholesale Price Index is called:",
            "options": ["WPI inflation", "CPI inflation", "Core inflation", "Stagflation"],
            "correct_answer": "A",
            "chapter": "Inflation", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Fiscal deficit is:",
            "options": ["total expenditure - total revenue", "borrowings only", "revenue deficit + capital expenditure", "primary deficit + interest payments"],
            "correct_answer": "A",
            "chapter": "Public Finance", "difficulty": "medium", "marks": 2,
        },
    ],
})

# ----- CAT 2024 (MBA entrance) -----------------------------------------------
BATCHES.append({
    "filename": "cat_2024_quant.json",
    "source": "cat_2024_quant",
    "default_board": "cat", "default_grade": 0,
    "default_subject": "quantitative_aptitude",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "If x + y = 7 and xy = 12, what is x^2 + y^2?",
            "options": ["25", "37", "49", "13"], "correct_answer": "A",
            "chapter": "Algebra", "difficulty": "medium", "marks": 3,
        },
        {
            "question_text": "Speed of train 60 km/h. Distance covered in 90 min:",
            "options": ["60 km", "75 km", "90 km", "120 km"], "correct_answer": "C",
            "chapter": "Speed Time Distance", "difficulty": "easy", "marks": 3,
        },
        {
            "question_text": "30% of 250 is:",
            "options": ["50", "75", "60", "80"], "correct_answer": "B",
            "chapter": "Percentages", "difficulty": "easy", "marks": 3,
        },
        {
            "question_text": "If a sum doubles in 5 years at SI, rate is:",
            "options": ["10%", "20%", "5%", "25%"], "correct_answer": "B",
            "chapter": "Simple Interest", "difficulty": "medium", "marks": 3,
        },
        {
            "question_text": "Average of first 10 natural numbers is:",
            "options": ["5", "5.5", "6", "10"], "correct_answer": "B",
            "chapter": "Averages", "difficulty": "easy", "marks": 3,
        },
        {
            "question_text": "log base 2 of 32 equals:",
            "options": ["4", "5", "6", "8"], "correct_answer": "B",
            "chapter": "Logarithms", "difficulty": "easy", "marks": 3,
        },
    ],
})

BATCHES.append({
    "filename": "cat_2024_varc.json",
    "source": "cat_2024_varc",
    "default_board": "cat", "default_grade": 0,
    "default_subject": "verbal_ability",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Choose the synonym of 'ephemeral':",
            "options": ["lasting", "fleeting", "obvious", "important"],
            "correct_answer": "B",
            "chapter": "Vocabulary", "difficulty": "medium", "marks": 3,
        },
        {
            "question_text": "Antonym of 'benevolent':",
            "options": ["kind", "generous", "malevolent", "wise"],
            "correct_answer": "C",
            "chapter": "Vocabulary", "difficulty": "easy", "marks": 3,
        },
        {
            "question_text": "Choose the correctly spelled word:",
            "options": ["accommodate", "accomodate", "acommodate", "accommadate"],
            "correct_answer": "A",
            "chapter": "Spelling", "difficulty": "medium", "marks": 3,
        },
        {
            "question_text": "Identify the part of speech of 'quickly' in 'She ran quickly':",
            "options": ["noun", "verb", "adverb", "adjective"],
            "correct_answer": "C",
            "chapter": "Grammar", "difficulty": "easy", "marks": 3,
        },
        {
            "question_text": "Closest meaning to 'ubiquitous':",
            "options": ["rare", "ancient", "found everywhere", "expensive"],
            "correct_answer": "C",
            "chapter": "Vocabulary", "difficulty": "medium", "marks": 3,
        },
    ],
})

# ----- CBSE Class 10 (2024) — board exam -------------------------------------
BATCHES.append({
    "filename": "cbse_class10_2024_mathematics.json",
    "source": "cbse_class10_2024_mathematics",
    "default_board": "cbse", "default_grade": 10,
    "default_subject": "mathematics",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "HCF of 12 and 18 is:",
            "options": ["2", "6", "12", "18"], "correct_answer": "B",
            "chapter": "Real Numbers", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Solve: 2x + 3 = 11",
            "options": ["3", "4", "5", "8"], "correct_answer": "B",
            "chapter": "Linear Equations", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Area of a circle with radius 7 cm (π=22/7):",
            "options": ["154 cm²", "44 cm²", "22 cm²", "77 cm²"],
            "correct_answer": "A",
            "chapter": "Areas Related to Circles", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Discriminant of x² + 4x + 4 = 0 is:",
            "options": ["0", "4", "8", "16"], "correct_answer": "A",
            "chapter": "Quadratic Equations", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Pythagoras: legs 3, 4. Hypotenuse:",
            "options": ["5", "6", "7", "12"], "correct_answer": "A",
            "chapter": "Triangles", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Probability of getting a head when a fair coin is tossed:",
            "options": ["0", "1/4", "1/2", "1"], "correct_answer": "C",
            "chapter": "Probability", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Mean of 5, 10, 15, 20, 25:",
            "options": ["10", "15", "20", "25"], "correct_answer": "B",
            "chapter": "Statistics", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "sin(30°) equals:",
            "options": ["0", "1/2", "√3/2", "1"], "correct_answer": "B",
            "chapter": "Trigonometry", "difficulty": "easy", "marks": 1,
        },
    ],
})

BATCHES.append({
    "filename": "cbse_class10_2024_science.json",
    "source": "cbse_class10_2024_science",
    "default_board": "cbse", "default_grade": 10,
    "default_subject": "science",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "pH of pure water at 25°C:",
            "options": ["6", "7", "8", "10"], "correct_answer": "B",
            "chapter": "Acids Bases Salts", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Heart muscles are made of:",
            "options": ["smooth muscle", "skeletal muscle", "cardiac muscle", "all of these"],
            "correct_answer": "C",
            "chapter": "Life Processes", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Unit of electric resistance:",
            "options": ["volt", "ampere", "ohm", "watt"], "correct_answer": "C",
            "chapter": "Electricity", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Which gas evolved when zinc reacts with HCl?",
            "options": ["O2", "H2", "Cl2", "CO2"], "correct_answer": "B",
            "chapter": "Metals and Non-Metals", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Photosynthesis requires:",
            "options": ["light, water, CO2", "light, O2, water", "CO2, O2", "only light"],
            "correct_answer": "A",
            "chapter": "Life Processes", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Refractive index of water is approximately:",
            "options": ["1.0", "1.33", "1.5", "2.0"], "correct_answer": "B",
            "chapter": "Light Refraction", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "An object placed at infinity in front of concave mirror, image forms at:",
            "options": ["pole", "focus", "centre of curvature", "infinity"],
            "correct_answer": "B",
            "chapter": "Light Reflection", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Which biome contains the largest carbon stock on land?",
            "options": ["desert", "tropical forest", "tundra", "savanna"],
            "correct_answer": "B",
            "chapter": "Our Environment", "difficulty": "medium", "marks": 1,
        },
    ],
})

BATCHES.append({
    "filename": "cbse_class10_2024_social_science.json",
    "source": "cbse_class10_2024_social_science",
    "default_board": "cbse", "default_grade": 10,
    "default_subject": "social_science",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Who wrote the Indian national anthem?",
            "options": ["Bankim Chandra", "Rabindranath Tagore", "Subramania Bharati", "Sarojini Naidu"],
            "correct_answer": "B",
            "chapter": "Nationalism in India", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Globalization in India accelerated after which year?",
            "options": ["1947", "1971", "1991", "2000"],
            "correct_answer": "C",
            "chapter": "Globalization", "difficulty": "medium", "marks": 1,
        },
        {
            "question_text": "Major iron-ore producing state in India:",
            "options": ["Kerala", "Jharkhand", "Punjab", "Gujarat"],
            "correct_answer": "B",
            "chapter": "Resources", "difficulty": "medium", "marks": 1,
        },
        {
            "question_text": "First general election in India was held in:",
            "options": ["1947", "1950", "1951-52", "1957"],
            "correct_answer": "C",
            "chapter": "Political History", "difficulty": "medium", "marks": 1,
        },
        {
            "question_text": "Power sharing arrangement most common in India:",
            "options": ["caste-based", "federal (centre-state)", "executive-only", "individual"],
            "correct_answer": "B",
            "chapter": "Federalism", "difficulty": "medium", "marks": 2,
        },
    ],
})

# ----- CBSE Class 12 (2024) — board exam -------------------------------------
BATCHES.append({
    "filename": "cbse_class12_2024_physics.json",
    "source": "cbse_class12_2024_physics",
    "default_board": "cbse", "default_grade": 12,
    "default_subject": "physics",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Faraday's law of EM induction relates:",
            "options": ["voltage and current", "EMF and rate of change of magnetic flux", "force and field", "resistance and temperature"],
            "correct_answer": "B",
            "chapter": "Electromagnetic Induction", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Critical angle of a medium with refractive index 1.5 (rounded):",
            "options": ["42°", "48°", "30°", "60°"],
            "correct_answer": "A",
            "chapter": "Ray Optics", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Energy of photon E = hν. Unit of Planck's constant h:",
            "options": ["J", "J·s", "J/s", "Hz"], "correct_answer": "B",
            "chapter": "Dual Nature", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Logic gate that gives 1 only when both inputs are 0:",
            "options": ["AND", "OR", "NOR", "NAND"], "correct_answer": "C",
            "chapter": "Semiconductors", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Two equal point charges +q at distance r. Force on each is:",
            "options": ["zero", "kq²/r²", "kq²/r", "kq/r²"], "correct_answer": "B",
            "chapter": "Electric Charges", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Maxwell's first equation (Gauss's law for E) is:",
            "options": ["∮E·dA = q/ε₀", "∮B·dA = 0", "∮E·dl = -dΦB/dt", "∮B·dl = μ₀I"],
            "correct_answer": "A",
            "chapter": "Electromagnetic Waves", "difficulty": "hard", "marks": 2,
        },
    ],
})

BATCHES.append({
    "filename": "cbse_class12_2024_chemistry.json",
    "source": "cbse_class12_2024_chemistry",
    "default_board": "cbse", "default_grade": 12,
    "default_subject": "chemistry",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Coordination number in body-centred cubic lattice:",
            "options": ["6", "8", "12", "4"], "correct_answer": "B",
            "chapter": "Solid State", "difficulty": "medium", "marks": 1,
        },
        {
            "question_text": "Faraday constant equals approximately:",
            "options": ["96500 C/mol", "6.022 × 10²³", "9.81 N", "1.6 × 10⁻¹⁹ C"],
            "correct_answer": "A",
            "chapter": "Electrochemistry", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "First-order rate law:",
            "options": ["k[A]⁰", "k[A]", "k[A]²", "k[A][B]"], "correct_answer": "B",
            "chapter": "Chemical Kinetics", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Which amino acid has a sulfur atom?",
            "options": ["alanine", "glycine", "methionine", "valine"],
            "correct_answer": "C",
            "chapter": "Biomolecules", "difficulty": "medium", "marks": 1,
        },
        {
            "question_text": "Bond angle in NH3 (ammonia):",
            "options": ["90°", "107.5°", "109.5°", "120°"], "correct_answer": "B",
            "chapter": "p-Block Elements", "difficulty": "medium", "marks": 1,
        },
        {
            "question_text": "Polymer formed by ethene monomers:",
            "options": ["polythene", "PVC", "nylon", "bakelite"],
            "correct_answer": "A",
            "chapter": "Polymers", "difficulty": "easy", "marks": 1,
        },
    ],
})

# ----- ICSE Class 10 (2024) --------------------------------------------------
BATCHES.append({
    "filename": "icse_class10_2024_mathematics.json",
    "source": "icse_class10_2024_mathematics",
    "default_board": "icse", "default_grade": 10,
    "default_subject": "mathematics",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Compound interest on ₹1000 at 10% per annum for 2 years:",
            "options": ["₹200", "₹210", "₹220", "₹100"], "correct_answer": "B",
            "chapter": "Compound Interest", "difficulty": "medium", "marks": 3,
        },
        {
            "question_text": "If A = {1,2,3}, B = {2,3,4}, then A ∩ B is:",
            "options": ["{1,4}", "{2,3}", "{1,2,3,4}", "{}"],
            "correct_answer": "B",
            "chapter": "Sets", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Volume of a cylinder, radius 7 cm, height 10 cm (π=22/7):",
            "options": ["1540 cm³", "770 cm³", "440 cm³", "1080 cm³"],
            "correct_answer": "A",
            "chapter": "Mensuration", "difficulty": "medium", "marks": 3,
        },
        {
            "question_text": "GST at 18% on ₹500 is:",
            "options": ["₹90", "₹50", "₹100", "₹85"], "correct_answer": "A",
            "chapter": "GST", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Banker's discount on ₹2000 for 3 months at 4%:",
            "options": ["₹15", "₹20", "₹40", "₹80"], "correct_answer": "B",
            "chapter": "Banking", "difficulty": "medium", "marks": 3,
        },
        {
            "question_text": "Median of 2, 4, 6, 8, 10:",
            "options": ["4", "5", "6", "8"], "correct_answer": "C",
            "chapter": "Statistics", "difficulty": "easy", "marks": 1,
        },
    ],
})

# ----- State boards Class 10 (2024) ------------------------------------------
BATCHES.append({
    "filename": "maharashtra_class10_2024_science.json",
    "source": "maharashtra_ssc_2024_science",
    "default_board": "state_mh", "default_grade": 10,
    "default_subject": "science",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Symbol for sodium:",
            "options": ["S", "So", "Na", "N"], "correct_answer": "C",
            "chapter": "Periodic Table", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Insectivorous plant:",
            "options": ["mango", "rose", "Venus flytrap", "tulsi"],
            "correct_answer": "C",
            "chapter": "Life Processes", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Which is a renewable source of energy?",
            "options": ["coal", "petroleum", "solar", "nuclear (fission)"],
            "correct_answer": "C",
            "chapter": "Energy", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Newton's first law of motion is about:",
            "options": ["inertia", "action-reaction", "F=ma", "gravity"],
            "correct_answer": "A",
            "chapter": "Force and Motion", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Largest planet in solar system:",
            "options": ["Earth", "Saturn", "Jupiter", "Neptune"],
            "correct_answer": "C",
            "chapter": "Universe", "difficulty": "easy", "marks": 1,
        },
    ],
})

BATCHES.append({
    "filename": "tamilnadu_class10_2024_mathematics.json",
    "source": "tamilnadu_sslc_2024_mathematics",
    "default_board": "state_tn", "default_grade": 10,
    "default_subject": "mathematics",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "If 5 books cost ₹125, cost of 7 books:",
            "options": ["₹150", "₹175", "₹200", "₹140"], "correct_answer": "B",
            "chapter": "Proportions", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Simple interest on ₹1000 at 8% for 2 years:",
            "options": ["₹80", "₹160", "₹100", "₹200"], "correct_answer": "B",
            "chapter": "Interest", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Square of 13:",
            "options": ["169", "144", "121", "196"], "correct_answer": "A",
            "chapter": "Squares and Cubes", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "tan(45°) equals:",
            "options": ["0", "1", "√3", "undefined"], "correct_answer": "B",
            "chapter": "Trigonometry", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Area of triangle with base 10 cm, height 6 cm:",
            "options": ["30 cm²", "60 cm²", "16 cm²", "60 cm³"],
            "correct_answer": "A",
            "chapter": "Mensuration", "difficulty": "easy", "marks": 2,
        },
    ],
})

BATCHES.append({
    "filename": "karnataka_class10_2024_science.json",
    "source": "karnataka_sslc_2024_science",
    "default_board": "state_ka", "default_grade": 10,
    "default_subject": "science",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Power of a 60W bulb running for 2 hours consumes:",
            "options": ["60 Wh", "120 Wh", "30 Wh", "120 J"],
            "correct_answer": "B",
            "chapter": "Electricity", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Latin for sodium chloride is:",
            "options": ["NaCl", "K2SO4", "NaOH", "Na2CO3"], "correct_answer": "A",
            "chapter": "Chemical Formulae", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Which vitamin deficiency causes rickets?",
            "options": ["A", "B12", "C", "D"], "correct_answer": "D",
            "chapter": "Nutrition", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Magnetic field around a current carrying wire is:",
            "options": ["linear", "circular", "elliptical", "spherical"],
            "correct_answer": "B",
            "chapter": "Magnetic Effects", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Number of bones in adult human body:",
            "options": ["196", "206", "216", "300"], "correct_answer": "B",
            "chapter": "Human Body", "difficulty": "easy", "marks": 1,
        },
    ],
})

BATCHES.append({
    "filename": "ap_telangana_class10_2024_mathematics.json",
    "source": "ap_telangana_ssc_2024_mathematics",
    "default_board": "state_ap_tg", "default_grade": 10,
    "default_subject": "mathematics",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "If x² = 49, x equals:",
            "options": ["7", "-7", "±7", "0"], "correct_answer": "C",
            "chapter": "Real Numbers", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Volume of cube with side 5 cm:",
            "options": ["75 cm³", "125 cm³", "25 cm³", "100 cm³"],
            "correct_answer": "B",
            "chapter": "Mensuration", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Sum of angles in a quadrilateral:",
            "options": ["180°", "270°", "360°", "540°"], "correct_answer": "C",
            "chapter": "Geometry", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Linear equation in two variables: 2x + 3y = 6. y when x=0:",
            "options": ["1", "2", "3", "0"], "correct_answer": "B",
            "chapter": "Linear Equations", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "Discriminant b² - 4ac. For x² + 2x + 1 = 0, value is:",
            "options": ["0", "4", "-4", "8"], "correct_answer": "A",
            "chapter": "Quadratic Equations", "difficulty": "medium", "marks": 2,
        },
    ],
})

BATCHES.append({
    "filename": "gujarat_class10_2024_science.json",
    "source": "gujarat_ssc_2024_science",
    "default_board": "state_gj", "default_grade": 10,
    "default_subject": "science",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Atmospheric pressure at sea level is approximately:",
            "options": ["101 kPa", "100 Pa", "1 MPa", "10 kPa"],
            "correct_answer": "A",
            "chapter": "Fluids", "difficulty": "medium", "marks": 1,
        },
        {
            "question_text": "Lever class with effort and load on either side of fulcrum:",
            "options": ["Class 1", "Class 2", "Class 3", "Class 4"],
            "correct_answer": "A",
            "chapter": "Simple Machines", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Currency of India:",
            "options": ["dollar", "rupee", "euro", "pound"], "correct_answer": "B",
            "chapter": "General Knowledge", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Element with atomic number 1:",
            "options": ["helium", "hydrogen", "lithium", "carbon"],
            "correct_answer": "B",
            "chapter": "Periodic Table", "difficulty": "easy", "marks": 1,
        },
    ],
})

BATCHES.append({
    "filename": "westbengal_class10_2024_mathematics.json",
    "source": "westbengal_madhyamik_2024_mathematics",
    "default_board": "state_wb", "default_grade": 10,
    "default_subject": "mathematics",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "If a:b = 2:3, b:c = 4:5, then a:c =",
            "options": ["8:15", "2:5", "8:5", "15:8"], "correct_answer": "A",
            "chapter": "Ratio", "difficulty": "medium", "marks": 2,
        },
        {
            "question_text": "Square root of 144:",
            "options": ["10", "12", "14", "16"], "correct_answer": "B",
            "chapter": "Squares and Roots", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "LCM of 12 and 18:",
            "options": ["12", "36", "18", "24"], "correct_answer": "B",
            "chapter": "Real Numbers", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Slope of line passing through (1,2) and (3,6):",
            "options": ["1", "2", "3", "4"], "correct_answer": "B",
            "chapter": "Coordinate Geometry", "difficulty": "medium", "marks": 2,
        },
    ],
})

BATCHES.append({
    "filename": "up_class10_2024_science.json",
    "source": "up_high_school_2024_science",
    "default_board": "state_up", "default_grade": 10,
    "default_subject": "science",
    "default_year": 2024, "default_paper": "main",
    "questions": [
        {
            "question_text": "Acid present in lemon:",
            "options": ["acetic", "citric", "lactic", "oxalic"], "correct_answer": "B",
            "chapter": "Acids", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Speed of light is approximately:",
            "options": ["3 × 10⁸ m/s", "3 × 10⁶ m/s", "3 × 10⁵ m/s", "300 m/s"],
            "correct_answer": "A",
            "chapter": "Light", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Number of protons in oxygen atom:",
            "options": ["6", "8", "10", "16"], "correct_answer": "B",
            "chapter": "Atomic Structure", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "Largest gland in human body:",
            "options": ["thyroid", "pancreas", "liver", "pituitary"],
            "correct_answer": "C",
            "chapter": "Human Body", "difficulty": "easy", "marks": 1,
        },
    ],
})

# ----- Hindi-medium variants (proves the pipeline supports multi-medium) ------
# Same question content, but in Devanagari script — what a Hindi-medium
# CBSE student would actually see on the paper. The board/grade/subject
# metadata stays identical; only question_text + options change.
BATCHES.append({
    "filename": "cbse_class10_2024_mathematics_hindi.json",
    "source": "cbse_class10_2024_mathematics_hindi_medium",
    "default_board": "cbse", "default_grade": 10,
    "default_subject": "mathematics_hindi",
    "default_year": 2024, "default_paper": "main_hindi_medium",
    "questions": [
        {
            "question_text": "12 और 18 का म.स.प. (HCF) क्या है?",
            "options": ["2", "6", "12", "18"], "correct_answer": "B",
            "chapter": "वास्तविक संख्याएँ", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "हल कीजिए: 2x + 3 = 11",
            "options": ["3", "4", "5", "8"], "correct_answer": "B",
            "chapter": "रैखिक समीकरण", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "7 cm त्रिज्या वाले वृत्त का क्षेत्रफल (π=22/7):",
            "options": ["154 cm²", "44 cm²", "22 cm²", "77 cm²"],
            "correct_answer": "A",
            "chapter": "वृत्तों से संबंधित क्षेत्रफल", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "sin(30°) का मान क्या है?",
            "options": ["0", "1/2", "√3/2", "1"], "correct_answer": "B",
            "chapter": "त्रिकोणमिति", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "5, 10, 15, 20, 25 का माध्य:",
            "options": ["10", "15", "20", "25"], "correct_answer": "B",
            "chapter": "सांख्यिकी", "difficulty": "easy", "marks": 1,
        },
    ],
})

BATCHES.append({
    "filename": "cbse_class10_2024_science_hindi.json",
    "source": "cbse_class10_2024_science_hindi_medium",
    "default_board": "cbse", "default_grade": 10,
    "default_subject": "science_hindi",
    "default_year": 2024, "default_paper": "main_hindi_medium",
    "questions": [
        {
            "question_text": "25°C पर शुद्ध जल का pH है:",
            "options": ["6", "7", "8", "10"], "correct_answer": "B",
            "chapter": "अम्ल क्षार लवण", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "विद्युत प्रतिरोध का मात्रक:",
            "options": ["volt", "ampere", "ohm", "watt"], "correct_answer": "C",
            "chapter": "विद्युत", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "प्रकाश संश्लेषण के लिए आवश्यक:",
            "options": ["प्रकाश, पानी, CO2", "प्रकाश, O2, पानी", "CO2, O2", "केवल प्रकाश"],
            "correct_answer": "A",
            "chapter": "जैव प्रक्रम", "difficulty": "easy", "marks": 2,
        },
        {
            "question_text": "जिंक + तनु HCl से कौन-सी गैस निकलती है?",
            "options": ["O2", "H2", "Cl2", "CO2"], "correct_answer": "B",
            "chapter": "धातु और अधातु", "difficulty": "easy", "marks": 1,
        },
        {
            "question_text": "जल का अपवर्तनांक लगभग है:",
            "options": ["1.0", "1.33", "1.5", "2.0"], "correct_answer": "B",
            "chapter": "प्रकाश का अपवर्तन", "difficulty": "easy", "marks": 2,
        },
    ],
})

# =============================================================================


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="validate without writing")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    total_questions = 0
    files_written = 0
    for batch in BATCHES:
        fname = batch["filename"]
        path = OUT / fname
        payload = {k: v for k, v in batch.items() if k != "filename"}
        n = len(payload["questions"])
        total_questions += n
        if args.check:
            print(f"  [check] {fname}: {n} questions")
            continue
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        files_written += 1
        print(f"  wrote {fname}: {n} questions")

    verb = "would-write" if args.check else "wrote"
    print(
        f"\n--- total: {verb} {files_written if not args.check else len(BATCHES)} "
        f"files, {total_questions} questions ---",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
