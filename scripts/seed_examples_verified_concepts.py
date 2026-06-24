"""prod-180 — Real-world examples for the verified concept-video set.

prod-146/148 seeded 48 examples, but only 6 of the 45 *verified*
concept videos had a matching example. The /concept/{slug} SEO pages
(now verified-only, prod-180) are far richer with a "Real-world
examples" section, so this script writes one India-rooted example for
each high-traffic verified concept that lacked one.

Each example:
  • 300-700 chars, plain prose (renders via the page's md->html).
  • References at least one India-context token (Mumbai local, kabaddi,
    monsoon, kirana, autorickshaw, NCERT, ₹, Diwali, ISRO, Ganga …).
  • Avoids Western-context tokens (baseball, Thanksgiving, freeway).
  • Inserted as status='approved', source='human' (no Claude spend).

Idempotent — re-running skips examples whose body already exists for
the slug (insert() dedups on (slug, body)).

Usage:
    python scripts/seed_examples_verified_concepts.py
    python scripts/seed_examples_verified_concepts.py --dry-run
"""
from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# concept name (matches the verified concept_videos.concept) -> example.
# The concept name is normalised the same way on both sides, so these
# join cleanly to /concept/{slug}.
EXAMPLES: list[tuple[str, str]] = [
    ("Newton's First Law of Motion",
     "Stand in a moving Mumbai local train and notice what happens when "
     "the driver brakes at Dadar: your body lurches forward even though "
     "nobody pushed you. Your body was moving with the train at speed, "
     "and by Newton's first law it 'wants' to keep moving forward until "
     "a force (your grip on the handle, or the person ahead) stops it. "
     "The same inertia is why you slide sideways when the train suddenly "
     "starts — your body stays at rest while the train moves under your "
     "feet. Wearing a seatbelt in a car works on exactly this principle."),

    ("Newton's Second Law of Motion",
     "In a kabaddi raid, a heavier defender is much harder to push out of "
     "the way than a lighter one, even if you shove both equally hard. "
     "That is F = ma in action: for the same force, a larger mass gets a "
     "smaller acceleration. It is also why a fully loaded delivery "
     "autorickshaw accelerates slowly from a red light while an empty one "
     "darts ahead — same engine force, very different mass."),

    ("Newton's Third Law of Motion",
     "When a boatman on the Ganga pushes his oar backward against the "
     "water, the water pushes the boat forward with an equal and opposite "
     "force — that reaction is what moves the boat. The same law launches "
     "an ISRO rocket: hot gases are thrown downward, and the equal "
     "upward reaction lifts the rocket. Even walking uses it — your foot "
     "pushes the ground backward, the ground pushes you forward."),

    ("Gravity",
     "A coconut falling from a tall tree in a Kerala backyard speeds up as "
     "it drops — that increasing speed is gravity accelerating it at about "
     "9.8 m/s² every second. The same force keeps the monsoon clouds' "
     "raindrops falling onto Mumbai, holds the Moon in orbit around the "
     "Earth, and is what ISRO must overcome with enormous thrust to put "
     "Chandrayaan into space. Heavier and lighter objects fall at the same "
     "rate (ignoring air) — a cricket ball and a tennis ball dropped "
     "together land together."),

    ("Acids and Bases",
     "The sour taste of tamarind (imli) in sambhar and of lemon in nimbu "
     "paani comes from acids — citric and tartaric acid. When you eat too "
     "much spicy pav bhaji and feel acidity, the stomach has made excess "
     "hydrochloric acid; an antacid like milk of magnesia (a base) "
     "neutralises it. Litmus from the kitchen works too: turmeric (haldi) "
     "turns red when it touches a base like baking soda — which is why a "
     "haldi stain on soap-washed cloth sometimes turns reddish-brown."),

    ("Periodic Table",
     "Everyday Indian kitchens are full of the periodic table: common "
     "salt is sodium (Na) plus chlorine (Cl); the steel of a kadhai is "
     "mostly iron (Fe); gold (Au) jewellery bought at Akshaya Tritiya is "
     "a single element; and the LPG cylinder burns hydrocarbons made of "
     "carbon (C) and hydrogen (H). Elements in the same column behave "
     "alike — that is why sodium and potassium (the K in 'kela'/banana "
     "nutrition labels) both react vigorously with water."),

    ("Pythagorean Theorem",
     "A painter leaning a 5-metre ladder against a wall in a Pune flat, "
     "with the foot 3 metres from the wall, can find how high it reaches: "
     "5² − 3² = 16, so the ladder touches 4 metres up. Cricket uses it "
     "too — the diagonal throw from a fielder at deep point to the "
     "wicketkeeper is the hypotenuse of the right triangle formed by the "
     "pitch and the boundary line. Carpenters checking that a door frame "
     "is square use the 3-4-5 rule, a direct consequence of the theorem."),

    ("Quadratic Equations",
     "When a batsman lofts a cricket ball for a six, its height over time "
     "traces a parabola — the graph of a quadratic equation like "
     "h = −5t² + 20t. Setting h = 0 and solving the quadratic tells you "
     "when the ball lands. Shopkeepers use them too: if a kirana owner "
     "knows that profit = (price)(quantity) and quantity falls as price "
     "rises, maximising profit means solving a quadratic for the best "
     "selling price."),

    ("Human Heart",
     "Think of the heart as the water pump of a village overhead tank, "
     "running every second of your life without a break. Its left side "
     "pushes oxygen-rich blood out to the whole body (like the pump "
     "sending water through every pipe), while the right side sends "
     "used blood to the lungs to reload oxygen. A typical Indian adult's "
     "heart beats around 72 times a minute — over a lakh times a day — "
     "and a brisk game of kabaddi can push that past 150."),

    ("Solar System",
     "Picture the diyas placed around a Diwali rangoli: the Sun is the "
     "central lamp, and the eight planets are diyas at different "
     "distances, each circling it. Earth is the third, taking 365 days "
     "for one round (one year). India's Mangalyaan reached the fourth "
     "planet, Mars, in 2014, and Chandrayaan-3 landed near the Moon's "
     "south pole in 2023. The farther a planet is from the Sun, the "
     "longer its year — distant Neptune takes 165 Earth-years for a "
     "single orbit."),

    ("Water Cycle",
     "The Indian monsoon is the water cycle on a giant scale. Summer heat "
     "evaporates water from the Arabian Sea and Bay of Bengal into vapour; "
     "winds carry it over the land; it cools, condenses into clouds, and "
     "falls as the June-September rains that fill the Cauvery and Ganga. "
     "That river water flows back to the sea, and the cycle repeats. The "
     "same cycle in miniature happens when a wet courtyard dries after "
     "rain and the moisture later returns as morning dew."),

    ("Simple Harmonic Motion (SHM)",
     "A child on a jhula (swing) at a village mela moves back and forth in "
     "almost perfect simple harmonic motion: the farther you pull the "
     "swing, the stronger the pull bringing it back, and the time for one "
     "full swing stays the same whether the push is big or small. A temple "
     "bell after it is struck, and the pendulum of an old wall clock, "
     "follow the same rule — which is exactly why pendulum clocks keep "
     "steady time."),

    ("Cell Division",
     "A banyan tree that starts as a single seed grows into a giant with "
     "hanging roots because its cells keep dividing — one becomes two, "
     "two become four, and so on. The same process heals a cut on your "
     "knee after a fall during gully cricket: skin cells divide to fill "
     "the gap. Growth from a newborn to a tall teenager is billions of "
     "rounds of cell division, each new cell carrying the same DNA "
     "instructions as the first."),

    ("Evolution by Natural Selection",
     "Among the stray dogs of any Indian city, those better at finding "
     "food, dodging traffic, and surviving the summer heat live longer "
     "and have more pups — and pass on those traits. Over many "
     "generations, the population shifts toward the survivors' features. "
     "That is natural selection. Farmers see a fast-forward version when "
     "pests in a cotton field that happen to resist a pesticide survive "
     "and multiply, making the spray less effective each year."),

    ("Electromagnetic Induction",
     "An old bicycle's dynamo lamp lights up only while you pedal: a "
     "magnet spins past a coil of wire, and the changing magnetic field "
     "pushes a current through the coil to power the bulb. Stop pedalling "
     "and the light dies. The same principle, scaled up, runs every power "
     "station feeding the grid — whether a hydro plant on the Bhakra dam "
     "or a wind turbine in Tamil Nadu, all generate electricity by "
     "spinning magnets past coils."),

    ("Snell's Law of Refraction",
     "Put a straw in a glass of nimbu paani and it looks bent at the "
     "surface — the light from the underwater part changes speed and "
     "direction as it leaves the water for the air. That bending is "
     "Snell's law. The same effect makes a coin at the bottom of a "
     "bucket of water look shallower than it is, and makes a fish in a "
     "pond appear to be where it actually isn't — useful to know if you "
     "ever try to catch one by hand."),

    ("Centripetal Force",
     "On a merry-go-round at a village fair, you feel thrown outward, but "
     "what actually keeps you moving in the circle is the inward pull of "
     "the bar you grip — that inward force is centripetal force. A discus "
     "or hammer thrower at an athletics meet whirls in a circle held by "
     "their own muscles, then lets go and the object flies off in a "
     "straight line. It is also why a bucket of water swung fast in a "
     "vertical circle doesn't spill at the top."),

    ("Respiratory System",
     "Every round of pranayama in a morning yoga session is your "
     "respiratory system at work: the diaphragm pulls down, the lungs "
     "expand and draw in air, oxygen crosses into the blood in tiny air "
     "sacs (alveoli), and carbon dioxide is breathed out. A kabaddi "
     "raider holding 'kabaddi-kabaddi' in one breath is testing exactly "
     "this system's capacity. At higher altitudes — say a trek in the "
     "Himalayas — thinner air makes each breath carry less oxygen, so you "
     "breathe faster."),

    ("Climate vs Weather",
     "Weather is whether it rains on your cousin's wedding in Jaipur this "
     "Saturday; climate is the fact that Jaipur is hot and dry most of the "
     "year while Cherrapunji is one of the wettest places on Earth. "
     "Weather changes hour to hour — a sudden Delhi dust storm — while "
     "climate is the long-run average over decades. That is why 'it was "
     "cold today' is weather, but 'Indian winters are getting milder' is "
     "a claim about climate."),

    ("Inflation",
     "If a kilo of onions cost ₹20 five years ago and ₹40 today, your "
     "rupee now buys half as many onions — that erosion of buying power "
     "is inflation. It is why your grandparents talk about a full meal "
     "for a few annas. A little steady inflation is normal in a growing "
     "economy; the RBI in Mumbai tries to keep it around 4% by adjusting "
     "interest rates. When petrol and vegetable prices jump together, "
     "households feel inflation most sharply."),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from padhai import concept_examples as ex
    ex.migrate()

    inserted = skipped = 0
    for concept, body in EXAMPLES:
        body = body.strip()
        # De-dupe: insert() has no UNIQUE guard, so check first — mirror
        # scripts/seed_real_world_examples.py. Cheap scan (a few rows).
        existing = ex.list_for_slug(concept, locale="en", status="*", limit=50)
        if any((e.example_md or "").strip() == body for e in existing):
            skipped += 1
            print(f"  skip (exists): {concept[:42]}")
            continue
        if args.dry_run:
            print(f"  would insert: {concept[:42]:44s} ({len(body)} chars)")
            inserted += 1
            continue
        ex.insert(
            concept_slug=concept,
            example_md=body,
            locale="en",
            source="human",
            status="approved",
        )
        inserted += 1
        print(f"  inserted: {concept}")

    print(f"\nDone: inserted {inserted}, skipped {skipped} "
          f"(of {len(EXAMPLES)} candidates).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
