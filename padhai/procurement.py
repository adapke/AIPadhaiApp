"""H7 — GeM procurement: SKU catalog + spec-sheet endpoints.

GeM (Government e-Marketplace) requires:
1. Public, non-discriminatory price list per SKU
2. Per-SKU spec sheet (features, deployment options, support level)

This module exposes both as public endpoints so the GeM team can
crawl them during seller-onboarding review + tender-response
verification.

The actual paperwork (Udyam, GST, GeM seller account, OEM cert) is
in H7_GEM_PROCUREMENT.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SKU:
    code: str               # 'padhai-school-basic'
    name: str
    tier: str               # 'School' | 'Coaching' | 'Enterprise'
    description: str
    price_inr_per_user_year: int
    min_users: int
    max_users: int | None       # None = unlimited
    features: tuple[str, ...]
    support_level: str
    gem_category: str = "Educational Technology Services (8543.7099.99)"


CATALOG: list[SKU] = [
    SKU(
        code="padhai-school-basic",
        name="AI Pathshala School (Basic)",
        tier="School",
        description=(
            "Full K-12 school ERP — roster, classes, attendance, "
            "assignments, exam mode, parent portal, multilingual "
            "video lessons (cartoon avatar)."
        ),
        price_inr_per_user_year=600,
        min_users=50,
        max_users=2000,
        features=(
            "Multilingual lessons (10 Indian languages)",
            "Attendance + exam mode + auto-grading",
            "Parent portal with progress tracking",
            "DPDP-compliant under-13 consent",
            "Single admin tenant",
        ),
        support_level="Email support (24h response)",
    ),
    SKU(
        code="padhai-school-premium",
        name="AI Pathshala School (Premium)",
        tier="School",
        description=(
            "Adds photoreal teacher avatar (Wav2Lip GPU rendering), "
            "advanced analytics, white-label branding, audit log "
            "export, custom domain support."
        ),
        price_inr_per_user_year=1200,
        min_users=50,
        max_users=5000,
        features=(
            "All Basic features",
            "Photoreal teacher avatar (M3 tier)",
            "Per-student analytics + weak-topic reports",
            "White-label theme + custom subdomain",
            "Audit log CSV export (SOC 2 evidence)",
            "Custom domain (school.edu.in/learn)",
        ),
        support_level="Phone + email (4h response)",
    ),
    SKU(
        code="padhai-coaching-jee",
        name="AI Pathshala JEE Coaching",
        tier="Coaching",
        description=(
            "JEE Main + Advanced preparation: subject-wise practice, "
            "mock tests, weak-topic identification, AI-generated "
            "worked solutions, video lectures."
        ),
        price_inr_per_user_year=3600,
        min_users=10,
        max_users=None,
        features=(
            "JEE 2025-2026 syllabus aligned",
            "Physics + Chemistry + Mathematics practice bank",
            "Subject-wise + topic-wise analytics",
            "Adaptive difficulty engine",
            "Hindi voice lectures (Sarvam bulbul)",
        ),
        support_level="Phone + email (4h response)",
    ),
    SKU(
        code="padhai-coaching-neet",
        name="AI Pathshala NEET Coaching",
        tier="Coaching",
        description=(
            "NEET preparation with biology focus, mock NEET papers, "
            "AIIMS / JIPMER-style question bank."
        ),
        price_inr_per_user_year=3600,
        min_users=10,
        max_users=None,
        features=(
            "NEET 2025-2026 syllabus aligned",
            "Biology + Chemistry + Physics practice bank",
            "Previous-year-question bank (2015-2024)",
            "Mock NEET papers + analytics",
        ),
        support_level="Phone + email (4h response)",
    ),
    SKU(
        code="padhai-coaching-upsc",
        name="AI Pathshala UPSC Coaching",
        tier="Coaching",
        description=(
            "UPSC Civil Services preparation: prelims MCQ practice, "
            "mains essay critique, daily current affairs digest, "
            "subject-wise GS analytics."
        ),
        price_inr_per_user_year=4800,
        min_users=10,
        max_users=None,
        features=(
            "UPSC CSE 2025-2026 syllabus aligned",
            "Daily current affairs digest (PIB, The Hindu)",
            "Prelims practice bank (50k+ questions)",
            "AI essay feedback for Mains practice",
            "Subject-wise rank prediction",
        ),
        support_level="Phone + email (2h response)",
    ),
    SKU(
        code="padhai-govt-state",
        name="AI Pathshala — State Government Deployment",
        tier="Enterprise",
        description=(
            "State-wide deployment: white-label branding, India-only "
            "data residency, dedicated CSM, source-code escrow, "
            "SOC 2 Type 2 audit reports, SAML/SCIM for govt IdPs, "
            "GeM-listed via custom RFP response."
        ),
        price_inr_per_user_year=0,   # custom
        min_users=10000,
        max_users=None,
        features=(
            "All Premium features",
            "India-only data residency (DPDP §16 compliant)",
            "Dedicated single-tenant deployment",
            "Source-code escrow with NSDL",
            "SOC 2 Type 2 audit reports (annual)",
            "Dedicated Customer Success Manager",
            "Custom training + on-site rollout",
        ),
        support_level="24x7 hotline + dedicated CSM",
    ),
]


def list_skus() -> list[SKU]:
    return list(CATALOG)


def get_sku(code: str) -> SKU | None:
    for s in CATALOG:
        if s.code == code:
            return s
    return None


def to_dict(s: SKU) -> dict:
    return {
        "code": s.code, "name": s.name, "tier": s.tier,
        "description": s.description,
        "price_inr_per_user_year": s.price_inr_per_user_year,
        "price_display": (
            f"₹{s.price_inr_per_user_year}/user/year"
            if s.price_inr_per_user_year > 0
            else "Custom (RFP)"
        ),
        "min_users": s.min_users,
        "max_users": s.max_users,
        "features": list(s.features),
        "support_level": s.support_level,
        "gem_category": s.gem_category,
    }
