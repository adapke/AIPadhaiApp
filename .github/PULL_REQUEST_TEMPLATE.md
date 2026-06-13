<!--
prod-110 — Default PR template.

Tone: be honest about what's done and what isn't. Reviewers can tell
the difference between "pipeline ready" and "content seeded";
trying to hide gaps wastes their time and yours.
-->

## Summary

<!--
One paragraph: what does this PR do and why? If it closes an issue,
link it (e.g. "Closes #123" or "prod-N").
-->

## Test plan

<!--
Markdown checklist of how reviewers verify this works.
Include the relevant `make` target and any manual steps.

Example:
- [ ] `make verify` is green
- [ ] Hit `POST /api/new-endpoint` with curl; returns expected JSON
- [ ] Cypress spec `cypress/e2e/22-new-feature.cy.js` passes
-->

- [ ] `make verify` green (lint + invariant guards + pytest + bench)
- [ ] New tests added if you changed behaviour (`tests/` or `cypress/`)
- [ ] Updated CHANGELOG.md if user-visible
- [ ] Updated ONBOARDING.md if you added a new tool / admin page / Make target
- [ ] Manual verification steps documented below

## Honest gaps

<!--
What is NOT in this PR that arguably should be? List anything you
considered and deferred. Examples:
  - Native-speaker review of new translations
  - Test for the edge case I noticed but didn't reproduce
  - Performance benchmark on a real-world payload
  - Documentation in ONBOARDING.md

If there's nothing — say "nothing identified". Don't leave this
blank.
-->

## Screenshots / output (optional)

<!--
Paste curl output, Cypress run summary, screenshots of new UI, etc.
Anything that helps the reviewer say "yes this works" without
running the code.
-->

---

## Checklist for reviewers

- [ ] Diff is bounded to one concern (no drive-by formatting)
- [ ] CHANGELOG mentions the change (or it's explicitly internal)
- [ ] No secrets, API keys, or sample passwords in the diff
- [ ] If schema migration: additive only, idempotent, tested on fresh DB

---

<sub>This template is auto-applied to new PRs.
Edit `.github/PULL_REQUEST_TEMPLATE.md` to change it.</sub>
