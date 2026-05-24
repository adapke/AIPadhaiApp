# I5 + I6 — Parent app + Teacher app

Two additional Capacitor builds derived from the v1.4 main app:

- **Parent app** — read-mostly view of linked children's progress,
  fees, attendance, notifications. No content creation.
- **Teacher app** — class roster, quick attendance, assignment
  creation, student progress. Optimised for in-class quick taps.

Both reuse 95% of the SPA code via the `?mode=parent` /
`?mode=teacher` URL parameter, which the SPA reads on boot and uses
to:
- Hide content-creation UI for parents
- Show a streamlined "attendance roll" landing for teachers
- Swap the side navigation for the right subset

## Why separate apps vs. one app + role-detection

Tried role-detection in v0.13. Two problems:
1. Parents kept opening the wrong UI (sees Studio mode, gets
   confused about why they can't create lessons)
2. App Store featured-app rejection: "your parent flow is
   underdeveloped" — Apple wanted a focused parent experience

Separate apps:
- ✓ Each app has its own App Store listing with parent-focused
  screenshots
- ✓ Push notifications can target the right app (kid's exam alert
  goes to the teacher app, not the parent app)
- ✓ Privacy: parent app doesn't even ship the lesson-generation JS

## Per-app config

Each app is a Capacitor project derived from `mobile/`:

```
mobile/
├── capacitor.config.json       # base config
├── parent/
│   └── capacitor.config.json   # appId: in.aipathshala.parent, etc.
└── teacher/
    └── capacitor.config.json   # appId: in.aipathshala.teacher
```

Build with `cd mobile/parent && npx cap sync && npx cap run ios`.

## What v1.7 ships (code)

- `mobile/parent/capacitor.config.json` — parent-app variant
- `mobile/teacher/capacitor.config.json` — teacher-app variant
- SPA `?mode=parent` + `?mode=teacher` parameters (the SPA reads
  these at boot; already supported as v0.13 stubs in mod-parent
  + mod-teacher modules)
- This doc

What's NOT in v1.7 (per-app ops sprint, parallel to I1/I2 main app):
- Per-app Apple bundle identifiers
- Per-app Play Store listings
- Parent-focused + teacher-focused marketing screenshots
- Apple "Family Sharing" approval flow for the parent app
- Per-app push category routing (E2 + I3 wire-up)

## Submission strategy

Phased rollout:
1. v1.4 main app submitted + approved (Q1)
2. v1.7 parent app — submit 2 weeks after main app is in
   production (Q2)
3. v1.7 teacher app — submit 2 weeks after parent (Q2)
4. By Q3, all three apps live with separate store listings

Apple is more lenient on derivative apps once you have one approved
in the same developer account — usually 3-5 days to review vs 1-2
weeks for the first submission.
