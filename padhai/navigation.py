"""v3.17 — Navigation manifest.

Per gap-review §26 — the redesign should not delete features.
It should **reorganise** them under a goal-led navigation: Home /
Exam Hub → Study Studio → Mock Tests / PYQ → AI Tutor →
Community → School / Teacher / Parent → Marketplace →
Admin & Trust.

This module exposes the navigation **as a backend-driven
manifest** so the frontend can render the mockup's layout
dynamically. Adding a new feature elsewhere → list its endpoint
in the right section here → it shows up in the UI without a
frontend deploy.

Single endpoint: `GET /api/navigation/manifest` returns:
  • sections[]      — top-level groups (Exam Hub, Study Studio, etc.)
    • title, slug, icon hint, description
    • features[]    — endpoint refs that power this section
      • title, endpoint, http_method, description, badge ("keep"
        on existing modules per §26)
  • mobile_bottom_nav[]   — the 5 tabs in the mobile mockup
  • visible_to_role       — admin items hidden from students etc.

No database — this is a static manifest. Updated by editing the
module. v3.17 ships the goal-led structure from review §26 +
HTML mockup; new features added in later releases append to the
relevant section.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Feature:
    title: str
    endpoint: str
    http_method: str = "GET"
    description: str = ""
    badge: str | None = None        # 'keep' / 'new' / 'admin' / 'beta'
    visible_to_role: str | None = None    # None = everyone
    sample_payload: dict | None = None   # form fields sent by the Try-it button


@dataclass(frozen=True)
class Section:
    title: str
    slug: str
    icon: str
    description: str
    features: tuple[Feature, ...] = field(default_factory=tuple)


NAVIGATION: tuple[Section, ...] = (
    Section(
        title="Exam Hub",
        slug="exam-hub",
        icon="target",
        description=(
            "Goal-first home — readiness, weak topics, today's plan, "
            "next action."
        ),
        features=(
            Feature("Student home dashboard",
                    "/api/home/me/dashboard",
                    description=(
                        "Goal-led composite view — pack + readiness + "
                        "today's plan + community + recent fallbacks."
                    ),
                    badge="new"),
            Feature("My exam packs",
                    "/api/exam-packs/me/enrollments",
                    description="Active Exam Pack enrollments.",
                    badge="keep"),
            Feature("Browse Exam Packs",
                    "/api/exam-packs",
                    description="Catalog — CBSE 10, UPSC, SSC, etc.",
                    badge="keep"),
            Feature("Readiness score",
                    "/api/readiness/me",
                    description=(
                        "0-100 per-pack readiness blending mastery, "
                        "mocks, coverage, consistency, trust."
                    ),
                    badge="keep"),
            Feature("Personalised pack overlay",
                    "/api/adaptive-packs/me",
                    description=(
                        "Per-user topic-weightage adjustments based "
                        "on mastery + mocks + plans."
                    ),
                    badge="new"),
        ),
    ),
    Section(
        title="Study Studio",
        slug="study-studio",
        icon="book-open",
        description=(
            "All existing creation tools, grouped: upload, video, "
            "notes, flashcards, quiz, recap."
        ),
        features=(
            Feature("Upload library",
                    "/api/uploads",
                    http_method="POST",
                    description="Documents + manuscripts.",
                    badge="keep"),
            Feature("Generate lessons",
                    "/lessons",
                    http_method="POST",
                    description="AI video + notes from sources.",
                    badge="keep"),
            Feature("Doubt chat",
                    "/api/doubts",
                    description="Quick AI doubt resolution.",
                    badge="keep"),
            Feature("Flashcard decks (SRS)",
                    "/api/srs/decks/me",
                    description=(
                        "Spaced-repetition cards from any source."
                    ),
                    badge="keep"),
            Feature("Quiz maker",
                    "/api/practice-tests",
                    description="Generated practice quizzes.",
                    badge="keep"),
            Feature("Audio recap",
                    "/api/audio-recaps/me",
                    description=(
                        "On-the-go audio summaries — NotebookLM-style."
                    ),
                    badge="new"),
            Feature("Retrieval search",
                    "/api/retrieval/query",
                    http_method="POST",
                    description=(
                        "Find the right passage across your uploads."
                    ),
                    badge="new"),
            Feature("Step-by-step math",
                    "/api/step-math/problems",
                    http_method="POST",
                    description="Photomath-style solver.",
                    badge="new",
                    sample_payload={"problem_latex": "x^2 - 5x + 6 = 0", "language": "en"}),
        ),
    ),
    Section(
        title="Mock Tests / PYQ",
        slug="mocks",
        icon="clipboard-check",
        description=(
            "Exam-pattern-faithful mocks with section timing, "
            "negative marking, percentile."
        ),
        features=(
            Feature("Browse mocks",
                    "/api/mock/papers",
                    description=(
                        "Sectional + full + PYQ modes per Exam Pack."
                    ),
                    badge="keep"),
            Feature("My attempts",
                    "/api/mock/me/attempts",
                    description="History + analysis per submission.",
                    badge="keep"),
            Feature("Question bank",
                    "/api/question-bank/search",
                    description="Verified questions tagged + filterable.",
                    badge="keep"),
        ),
    ),
    Section(
        title="AI Tutor",
        slug="ai-tutor",
        icon="message-square",
        description=(
            "Citation-grounded chat with source/official/general modes "
            "+ Socratic step-by-step."
        ),
        features=(
            Feature("Start tutor session",
                    "/api/tutor/sessions",
                    http_method="POST",
                    description="One-on-one chat with the AI tutor.",
                    badge="keep"),
            Feature("Set session mode",
                    "/api/tutor/sessions/{sid}/mode",
                    http_method="POST",
                    description=(
                        "general / source_only / official — drives "
                        "the citation discipline."
                    ),
                    badge="new"),
            Feature("Recent fallbacks",
                    "/api/tutor/me/fallbacks",
                    description=(
                        "Times the tutor declined to answer — "
                        "'upload more material' nudge."
                    ),
                    badge="new"),
            Feature("Socratic exchanges",
                    "/api/socratic/me",
                    description=(
                        "Khanmigo-style ask-before-tell flows."
                    ),
                    badge="new"),
            Feature("My citations",
                    "/api/citations/me",
                    description=(
                        "Every AI answer's source provenance."
                    ),
                    badge="new"),
        ),
    ),
    Section(
        title="Community",
        slug="community",
        icon="users",
        description=(
            "Exam / state / language rooms with moderation + reactions."
        ),
        features=(
            Feature("Forums",
                    "/api/forums/threads",
                    description="Discussion threads.",
                    badge="keep"),
            Feature("Study buddies",
                    "/api/buddies/me",
                    description="Peer-match by exam + schedule.",
                    badge="keep"),
            Feature("Mentor program",
                    "/api/mentors",
                    description="Volunteer mentor matching.",
                    badge="keep"),
            Feature("Reactions",
                    "/api/reactions/{target_kind}/{target_id}",
                    description="Like / helpful / thank / report.",
                    badge="new"),
        ),
    ),
    Section(
        title="School / Teacher / Parent",
        slug="school",
        icon="building",
        description=(
            "Org-mode tools: roster, classes, attendance, fees, "
            "parent dashboard, teacher view."
        ),
        features=(
            Feature("Teacher dashboard",
                    "/api/teacher/orgs/{org_id}/dashboard",
                    description="Class-level student summaries.",
                    badge="new", visible_to_role="teacher"),
            Feature("Parent dashboard",
                    "/api/parent/me/dashboard",
                    description=(
                        "Verified-child summaries with academic + "
                        "trust signals."
                    ),
                    badge="new", visible_to_role="parent"),
            Feature("Org members",
                    "/api/orgs/{org_id}/members",
                    description="Roster + roles.",
                    badge="keep", visible_to_role="admin"),
            Feature("Attendance",
                    "/api/orgs/{org_id}/attendance",
                    description="Class attendance ledger.",
                    badge="keep", visible_to_role="teacher"),
            Feature("Fees",
                    "/api/orgs/{org_id}/fees",
                    description="School fee management.",
                    badge="keep", visible_to_role="admin"),
            Feature("Daily plan completion",
                    "/api/daily-plan/me/recent",
                    description="14-day study habit ledger.",
                    badge="new"),
        ),
    ),
    Section(
        title="Marketplace",
        slug="marketplace",
        icon="shopping-cart",
        description=(
            "Teachers, content publishers, question packs, tutors — "
            "rated + refundable + copyright-checked."
        ),
        features=(
            Feature("Teacher courses (O1)",
                    "/api/publishing/storefront",
                    description="Solo teacher-published courses.",
                    badge="keep"),
            Feature("Content packs (O2)",
                    "/api/content/packs",
                    description="Publisher-board syllabus packs.",
                    badge="keep"),
            Feature("Question packs (O3)",
                    "/api/qb-market/packs",
                    description="Curated PYQ + practice packs.",
                    badge="keep"),
            Feature("Tutor marketplace (M4)",
                    "/api/marketplace/tutors",
                    description="1:1 paid tutor booking.",
                    badge="keep"),
            Feature("Top-quality items",
                    "/api/market/quality/top",
                    description=(
                        "Best-rated items across all 4 marketplaces."
                    ),
                    badge="new"),
            Feature("Vouchers + bundles",
                    "/api/bundles",
                    description="Promo codes + cart bundle discounts.",
                    badge="keep"),
        ),
    ),
    Section(
        title="Admin & Trust",
        slug="admin",
        icon="shield",
        description=(
            "Moderation queue, expert review, accuracy benchmarks, "
            "compliance audits — all admin-only."
        ),
        features=(
            Feature("Grounding rate dashboard",
                    "/api/admin/citations/grounding-rate",
                    description=(
                        "% of AI answers carrying at least 1 citation."
                    ),
                    badge="new", visible_to_role="admin"),
            Feature("Accuracy benchmark dashboard",
                    "/api/admin/bench/trust-dashboard",
                    description=(
                        "Pass rate + mean score per target across runs."
                    ),
                    badge="new", visible_to_role="admin"),
            Feature("Moderation queue",
                    "/api/admin/moderation/queue",
                    description=(
                        "Auto-flagged content + reviewer queue with SLA."
                    ),
                    badge="new", visible_to_role="admin"),
            Feature("Expert review queue",
                    "/api/expert-reviews/queue",
                    description=(
                        "Verified experts review flagged AI answers."
                    ),
                    badge="new", visible_to_role="admin"),
            Feature("Refund queue",
                    "/api/admin/market/refunds",
                    description="Marketplace refund decisions.",
                    badge="new", visible_to_role="admin"),
            Feature("Copyright claims",
                    "/api/admin/market/copyright-claims",
                    description="DMCA-style takedowns.",
                    badge="new", visible_to_role="admin"),
            Feature("DPDP / SOC2 audit",
                    "/api/orgs/{org_id}/audit",
                    description=(
                        "Compliance event audit trail (org-scoped)."
                    ),
                    badge="keep", visible_to_role="admin"),
        ),
    ),
)


# Mobile bottom-nav per HTML mockup §26 (5 tabs).
MOBILE_BOTTOM_NAV: tuple[dict, ...] = (
    {"title": "Home", "section_slug": "exam-hub", "icon": "home"},
    {"title": "Studio", "section_slug": "study-studio",
     "icon": "book-open"},
    {"title": "Test", "section_slug": "mocks",
     "icon": "clipboard-check"},
    {"title": "Tutor", "section_slug": "ai-tutor",
     "icon": "message-square"},
    {"title": "Group", "section_slug": "community", "icon": "users"},
)


def migrate() -> None:
    """No-op — this is a pure manifest module."""
    return None


def _feature_to_dict(f: Feature) -> dict:
    return {
        "title": f.title,
        "endpoint": f.endpoint,
        "http_method": f.http_method,
        "description": f.description,
        "badge": f.badge,
        "visible_to_role": f.visible_to_role,
        "sample_payload": f.sample_payload,
    }


def _section_to_dict(s: Section) -> dict:
    return {
        "title": s.title,
        "slug": s.slug,
        "icon": s.icon,
        "description": s.description,
        "features": [_feature_to_dict(f) for f in s.features],
    }


def get_manifest(*, role: str | None = None) -> dict:
    """Return the navigation manifest, optionally filtered by role."""
    sections = []
    for s in NAVIGATION:
        if role is None:
            visible = list(s.features)
        else:
            visible = [
                f for f in s.features
                if f.visible_to_role is None
                or f.visible_to_role == role
            ]
        if not visible:
            continue
        sections.append({
            "title": s.title,
            "slug": s.slug,
            "icon": s.icon,
            "description": s.description,
            "features": [_feature_to_dict(f) for f in visible],
        })
    return {
        "sections": sections,
        "mobile_bottom_nav": list(MOBILE_BOTTOM_NAV),
    }


def get_section(slug: str) -> dict | None:
    for s in NAVIGATION:
        if s.slug == slug:
            return _section_to_dict(s)
    return None


def list_section_slugs() -> list[str]:
    return [s.slug for s in NAVIGATION]
