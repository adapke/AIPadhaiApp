"""Curriculum index — a static catalogue of Indian-school topics that
uploaded material can be auto-tagged against.

For each (board, class, subject, chapter) we record:
  - The canonical topic name (in English; translations are derived later)
  - A few sentence-level descriptions of what the chapter covers
  - Pedagogical level (kg / primary / middle / secondary / neet_jee)
  - Subject tags (Physics, Chemistry, Maths, Biology, Civics, etc.)

This file ships the FIRST PASS seed data for the highest-leverage
slice: CBSE Class 6-10 Maths + Science (NEP 2020 mandates Maths + Science
in mother tongue first; everything else follows). State-board variants
and Class 11-12 + competitive-exam syllabi follow in Phase 2.

What this file is NOT:
  - It does NOT host or reproduce NCERT/state-board textbook content.
  - It only carries metadata: chapter titles + a few descriptive
    sentences. This is non-copyrightable factual material. See
    AI_PATHSHALA_BLUEPRINT.md §9 for the legal strategy.

Schema:
    TOPIC = {
        "board": "CBSE" | "ICSE" | "Maharashtra" | "Karnataka" | ...,
        "class": int,           # 6..12
        "subject": str,         # "Science", "Maths", "Social", ...
        "chapter_no": int,
        "chapter_title": str,   # canonical English name
        "level": str,           # padhai/pedagogy.py LEVEL_GUIDANCE key
        "summary": str,         # 1-2 sentences for embedding
        "topics": list[str],    # short concept tags
    }
"""

from __future__ import annotations


CURRICULUM: list[dict] = [
    # ===================== Class 6 =====================
    # Science (NCERT)
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":1,
     "chapter_title":"Components of Food","level":"middle",
     "summary":"Major nutrients in food (carbohydrates, fats, proteins, vitamins, minerals, water) and their roles.",
     "topics":["nutrients","balanced diet","deficiency diseases"]},
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":2,
     "chapter_title":"Sorting Materials Into Groups","level":"middle",
     "summary":"Properties of materials (transparency, hardness, solubility) used to classify them.",
     "topics":["materials","properties","grouping"]},
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":3,
     "chapter_title":"Separation of Substances","level":"middle",
     "summary":"Hand-picking, threshing, winnowing, sieving, sedimentation, decantation, filtration, evaporation.",
     "topics":["mixtures","separation","filtration","evaporation"]},
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":4,
     "chapter_title":"Getting to Know Plants","level":"middle",
     "summary":"Plant parts (roots, stem, leaves, flowers), kinds of plants (herbs, shrubs, trees, climbers, creepers), photosynthesis.",
     "topics":["plants","leaves","photosynthesis","roots"]},
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":5,
     "chapter_title":"Body Movements","level":"middle",
     "summary":"Bones, muscles, joints; movement in different animals (earthworm, snail, cockroach, fish).",
     "topics":["skeleton","joints","muscles","locomotion"]},
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":6,
     "chapter_title":"The Living Organisms and Their Surroundings","level":"middle",
     "summary":"Habitats (aquatic, terrestrial), biotic and abiotic factors, adaptation.",
     "topics":["habitat","adaptation","biotic","abiotic"]},
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":7,
     "chapter_title":"Motion and Measurement of Distances","level":"middle",
     "summary":"SI units, measurement of length, types of motion (rectilinear, circular, periodic).",
     "topics":["measurement","SI units","motion"]},
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":8,
     "chapter_title":"Light, Shadows and Reflections","level":"middle",
     "summary":"Luminous and non-luminous objects, transparent/opaque/translucent, formation of shadows, reflection from mirrors.",
     "topics":["light","shadows","mirrors","reflection"]},
    {"board":"CBSE","class":6,"subject":"Science","chapter_no":9,
     "chapter_title":"Electricity and Circuits","level":"middle",
     "summary":"Simple circuit (battery, switch, bulb), conductors vs insulators.",
     "topics":["circuit","battery","conductor","insulator"]},
    # Maths (Class 6 NCERT)
    {"board":"CBSE","class":6,"subject":"Maths","chapter_no":1,
     "chapter_title":"Knowing Our Numbers","level":"middle",
     "summary":"Place value, Indian and international number systems, estimation, rounding.",
     "topics":["place value","large numbers","estimation"]},
    {"board":"CBSE","class":6,"subject":"Maths","chapter_no":2,
     "chapter_title":"Whole Numbers","level":"middle",
     "summary":"Whole numbers, number line, properties of addition and multiplication, distributivity.",
     "topics":["whole numbers","number line","properties"]},
    {"board":"CBSE","class":6,"subject":"Maths","chapter_no":3,
     "chapter_title":"Playing With Numbers","level":"middle",
     "summary":"Factors, multiples, prime and composite numbers, divisibility rules, HCF, LCM.",
     "topics":["factors","multiples","prime","HCF","LCM"]},
    {"board":"CBSE","class":6,"subject":"Maths","chapter_no":4,
     "chapter_title":"Basic Geometrical Ideas","level":"middle",
     "summary":"Points, lines, angles, triangles, quadrilaterals, polygons, circles.",
     "topics":["geometry","angle","polygon","circle"]},
    {"board":"CBSE","class":6,"subject":"Maths","chapter_no":7,
     "chapter_title":"Fractions","level":"middle",
     "summary":"Proper, improper and mixed fractions; equivalent fractions; addition and subtraction of fractions.",
     "topics":["fractions","equivalent fractions","mixed numbers"]},
    {"board":"CBSE","class":6,"subject":"Maths","chapter_no":11,
     "chapter_title":"Algebra","level":"middle",
     "summary":"Use of variables, simple equations, algebraic expressions.",
     "topics":["algebra","variables","equations"]},

    # ===================== Class 7 =====================
    {"board":"CBSE","class":7,"subject":"Science","chapter_no":1,
     "chapter_title":"Nutrition in Plants","level":"middle",
     "summary":"Photosynthesis, autotrophs and heterotrophs, saprotrophs, symbiosis.",
     "topics":["photosynthesis","autotroph","heterotroph"]},
    {"board":"CBSE","class":7,"subject":"Science","chapter_no":4,
     "chapter_title":"Heat","level":"middle",
     "summary":"Temperature vs heat, thermometer types, transfer of heat (conduction, convection, radiation).",
     "topics":["heat","temperature","conduction","convection","radiation"]},
    {"board":"CBSE","class":7,"subject":"Science","chapter_no":6,
     "chapter_title":"Physical and Chemical Changes","level":"middle",
     "summary":"Reversible and irreversible changes, examples of chemical changes (rusting, burning), crystallisation.",
     "topics":["physical change","chemical change","rusting"]},
    {"board":"CBSE","class":7,"subject":"Maths","chapter_no":1,
     "chapter_title":"Integers","level":"middle",
     "summary":"Operations on integers, properties, multiplication and division of integers.",
     "topics":["integers","operations"]},
    {"board":"CBSE","class":7,"subject":"Maths","chapter_no":4,
     "chapter_title":"Simple Equations","level":"middle",
     "summary":"Forming and solving simple linear equations in one variable.",
     "topics":["linear equation","one variable"]},
    {"board":"CBSE","class":7,"subject":"Maths","chapter_no":11,
     "chapter_title":"Perimeter and Area","level":"middle",
     "summary":"Squares, rectangles, parallelograms, triangles, circumference and area of circle.",
     "topics":["area","perimeter","circle"]},

    # ===================== Class 8 =====================
    {"board":"CBSE","class":8,"subject":"Science","chapter_no":1,
     "chapter_title":"Crop Production and Management","level":"middle",
     "summary":"Agricultural practices, soil preparation, sowing, irrigation, fertilisers and manures.",
     "topics":["agriculture","crops","irrigation"]},
    {"board":"CBSE","class":8,"subject":"Science","chapter_no":4,
     "chapter_title":"Materials: Metals and Non-Metals","level":"middle",
     "summary":"Physical and chemical properties of metals and non-metals, reactivity series, displacement reactions.",
     "topics":["metals","non-metals","reactivity"]},
    {"board":"CBSE","class":8,"subject":"Science","chapter_no":6,
     "chapter_title":"Combustion and Flame","level":"middle",
     "summary":"Types of fuels, conditions necessary for combustion, ignition temperature, types of flames.",
     "topics":["combustion","fuel","ignition","flame"]},
    {"board":"CBSE","class":8,"subject":"Science","chapter_no":11,
     "chapter_title":"Force and Pressure","level":"middle",
     "summary":"Contact and non-contact forces, pressure, atmospheric pressure, pressure in liquids.",
     "topics":["force","pressure","atmospheric pressure"]},
    {"board":"CBSE","class":8,"subject":"Science","chapter_no":15,
     "chapter_title":"Stars and the Solar System","level":"middle",
     "summary":"Celestial objects, planets, satellites, constellations, the solar system.",
     "topics":["solar system","planets","stars","constellations"]},
    {"board":"CBSE","class":8,"subject":"Maths","chapter_no":2,
     "chapter_title":"Linear Equations in One Variable","level":"middle",
     "summary":"Solving linear equations with the variable on both sides, reducing equations to simpler form.",
     "topics":["linear equation","one variable"]},
    {"board":"CBSE","class":8,"subject":"Maths","chapter_no":8,
     "chapter_title":"Comparing Quantities","level":"middle",
     "summary":"Ratios, percentages, profit/loss, discount, simple and compound interest.",
     "topics":["ratio","percentage","interest","discount"]},
    {"board":"CBSE","class":8,"subject":"Maths","chapter_no":11,
     "chapter_title":"Mensuration","level":"middle",
     "summary":"Area of trapezium, general quadrilateral, polygons; surface area and volume of cube, cuboid, cylinder.",
     "topics":["mensuration","area","volume"]},

    # ===================== Class 9 =====================
    {"board":"CBSE","class":9,"subject":"Science","chapter_no":1,
     "chapter_title":"Matter in Our Surroundings","level":"secondary",
     "summary":"States of matter, change of state, evaporation, latent heat.",
     "topics":["matter","states","evaporation","latent heat"]},
    {"board":"CBSE","class":9,"subject":"Science","chapter_no":4,
     "chapter_title":"Structure of the Atom","level":"secondary",
     "summary":"Thomson, Rutherford and Bohr atomic models; atomic number, mass number, isotopes, isobars.",
     "topics":["atom","Bohr model","isotopes","isobars"]},
    {"board":"CBSE","class":9,"subject":"Science","chapter_no":8,
     "chapter_title":"Motion","level":"secondary",
     "summary":"Uniform and non-uniform motion, velocity, acceleration, distance-time and velocity-time graphs, equations of motion.",
     "topics":["motion","velocity","acceleration","equations of motion"]},
    {"board":"CBSE","class":9,"subject":"Science","chapter_no":9,
     "chapter_title":"Force and Laws of Motion","level":"secondary",
     "summary":"Newton's three laws of motion, inertia, momentum, conservation of momentum.",
     "topics":["Newton's laws","force","momentum","inertia"]},
    {"board":"CBSE","class":9,"subject":"Science","chapter_no":10,
     "chapter_title":"Gravitation","level":"secondary",
     "summary":"Universal law of gravitation, free fall, mass vs weight, thrust and pressure, Archimedes principle.",
     "topics":["gravity","gravitation","Archimedes","buoyancy"]},
    {"board":"CBSE","class":9,"subject":"Maths","chapter_no":2,
     "chapter_title":"Polynomials","level":"secondary",
     "summary":"Polynomial in one variable, zeros of a polynomial, remainder theorem, factor theorem, factorisation.",
     "topics":["polynomial","factor theorem","remainder theorem"]},
    {"board":"CBSE","class":9,"subject":"Maths","chapter_no":7,
     "chapter_title":"Triangles","level":"secondary",
     "summary":"Congruence criteria (SSS, SAS, ASA, RHS), properties of isosceles triangle, inequalities in a triangle.",
     "topics":["triangle","congruence","isosceles"]},
    {"board":"CBSE","class":9,"subject":"Maths","chapter_no":10,
     "chapter_title":"Heron's Formula","level":"secondary",
     "summary":"Heron's formula for area of triangle given three sides.",
     "topics":["Heron's formula","area of triangle"]},

    # ===================== Class 10 =====================
    {"board":"CBSE","class":10,"subject":"Science","chapter_no":1,
     "chapter_title":"Chemical Reactions and Equations","level":"secondary",
     "summary":"Types of chemical reactions (combination, decomposition, displacement, double displacement, redox), balancing equations.",
     "topics":["chemical reaction","balanced equation","redox"]},
    {"board":"CBSE","class":10,"subject":"Science","chapter_no":3,
     "chapter_title":"Metals and Non-Metals","level":"secondary",
     "summary":"Physical and chemical properties, ionic and covalent bonding, extraction of metals, corrosion.",
     "topics":["metals","extraction","corrosion","alloy"]},
    {"board":"CBSE","class":10,"subject":"Science","chapter_no":6,
     "chapter_title":"Life Processes","level":"secondary",
     "summary":"Nutrition, respiration, transport, excretion in plants and animals.",
     "topics":["nutrition","respiration","transport","excretion"]},
    {"board":"CBSE","class":10,"subject":"Science","chapter_no":10,
     "chapter_title":"Light - Reflection and Refraction","level":"secondary",
     "summary":"Laws of reflection, spherical mirrors, mirror formula, refraction through glass slab, lens formula.",
     "topics":["reflection","refraction","mirrors","lenses"]},
    {"board":"CBSE","class":10,"subject":"Science","chapter_no":12,
     "chapter_title":"Electricity","level":"secondary",
     "summary":"Electric current, potential difference, Ohm's law, resistance, resistors in series and parallel, heating effect.",
     "topics":["electricity","Ohm's law","resistance","circuits"]},
    {"board":"CBSE","class":10,"subject":"Maths","chapter_no":1,
     "chapter_title":"Real Numbers","level":"secondary",
     "summary":"Euclid's division algorithm, fundamental theorem of arithmetic, irrational numbers, decimal expansions.",
     "topics":["Euclid","irrational","prime factorisation"]},
    {"board":"CBSE","class":10,"subject":"Maths","chapter_no":3,
     "chapter_title":"Pair of Linear Equations in Two Variables","level":"secondary",
     "summary":"Graphical and algebraic methods (substitution, elimination, cross-multiplication) of solving linear equations.",
     "topics":["linear equation","two variables","substitution","elimination"]},
    {"board":"CBSE","class":10,"subject":"Maths","chapter_no":4,
     "chapter_title":"Quadratic Equations","level":"secondary",
     "summary":"Standard form, solution by factorisation and quadratic formula, nature of roots, discriminant.",
     "topics":["quadratic","discriminant","factorisation"]},
    {"board":"CBSE","class":10,"subject":"Maths","chapter_no":8,
     "chapter_title":"Introduction to Trigonometry","level":"secondary",
     "summary":"Trigonometric ratios, identities, ratios of specific angles (0, 30, 45, 60, 90).",
     "topics":["trigonometry","ratios","identities"]},
]


SUBJECTS = sorted({t["subject"] for t in CURRICULUM})
BOARDS = sorted({t["board"] for t in CURRICULUM})
CLASSES = sorted({t["class"] for t in CURRICULUM})
