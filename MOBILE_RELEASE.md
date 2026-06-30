# Mobile Release Preparation — AI Pathshala

The one playbook that takes the apps from **"builds locally"** to **"live on
the Play Store + App Store."** Follow the sections top to bottom; each is a
gate for the next.

- Build mechanics (toolchain, the Windows gotchas): [`MOBILE_BUILD.md`](MOBILE_BUILD.md)
- Deploy-then-mobile ordering: [`mobile/MOBILE_BUILD.md`](mobile/MOBILE_BUILD.md)
- The 3-app strategy: [`mobile/PARENT_TEACHER_APPS.md`](mobile/PARENT_TEACHER_APPS.md)
- Backend deploy: [`docs/DEPLOY.md`](docs/DEPLOY.md)

---

## Status right now (prod-198)

| Item | State |
|---|---|
| Android debug APK | ✅ Builds green (`in.aipathshala.app`, ~4.4 MB) — **points at `10.0.2.2:8000` (emulator + local backend)**, not a real device |
| iOS compile (CI) | ✅ `.github/workflows/mobile-ios.yml` compiles unsigned on `macos-14` |
| Production server URL | ❌ Not set in the build yet (`npm run build:prod`) |
| Release signing (keystore / Apple certs) | ❌ Not created |
| Store accounts + listings | ❌ Not created |
| Backend on public HTTPS | ❌ / depends on your deploy |

**Bottom line:** the apps *compile*. Everything below is what turns a compile
into a *shippable, signed, store-approved* app.

---

## 0. What ships — three apps

| App | appId | Audience | Submit |
|---|---|---|---|
| Student | `in.aipathshala.app` | Students (default) | First |
| Parent | `in.aipathshala.parent` | Linked parents (read-mostly) | +2 weeks after Student is live |
| Teacher | `in.aipathshala.teacher` | Teachers (roster/attendance) | +2 weeks after Parent |

Ship **Student first**; Apple/Google review derivative apps in the same account
faster (3–5 days vs 1–2 weeks). Each app reuses ~95% of the SPA via
`?mode=parent` / `?mode=teacher`.

---

## 1. One-time accounts & money

- [ ] **Google Play Console** — USD 25 one-time → https://play.google.com/console
- [ ] **Apple Developer Program** — USD 99 / year → https://developer.apple.com/programs
- [ ] **Backend on public HTTPS** — deploy per [`docs/DEPLOY.md`](docs/DEPLOY.md); note the origin (e.g. `https://app.aipathshala.in`). Stores **reject plain-HTTP** apps.
- [ ] GitHub repo (for the iOS macOS CI) — already have it.

---

## 2. Pre-flight gates (all must be true before any *release* build)

- [ ] Backend reachable over HTTPS at a stable origin
- [ ] Privacy Policy live at `/privacy` (already in-app) and reachable publicly
- [ ] In-app **account deletion** reachable (Settings → Account) — Apple-required since 2022; DPDP §12 already implements it
- [ ] Under-18 **parental-consent** flow works (DPDP §9 — already in-app)
- [ ] (Recommended) Push keys ready — see §6

---

## 3. Point the build at production (critical)

The current APK loads `http://10.0.2.2:8000` (emulator bridge). For real
devices and the stores you must rebuild against the deployed HTTPS origin.

```bash
cd mobile
# Default prod origin is https://app.aipathshala.in; override if different:
CAPACITOR_SERVER_URL=https://YOUR-ORIGIN npm run build:prod
npm run check:prod        # asserts no localhost/cleartext leaked into configs
```

`scripts/configure-server.cjs` rewrites `server.url` + `cleartext:false` +
`androidScheme:https` across the **student / parent / teacher** configs.
`check:prod` fails the build if a dev URL slipped through.

---

## 4. Android — signed release (AAB for Play Store)

### 4a. Create an upload keystore (one-time, keep it FOREVER + backed up)

```bash
keytool -genkeypair -v -keystore aipathshala-upload.jks \
  -keyalg RSA -keysize 2048 -validity 10000 -alias aipathshala
# Store the .jks OUTSIDE the repo. If you lose it you cannot update the app.
```

### 4b. Wire signing (do NOT commit the keystore or passwords)

Create `mobile/android/keystore.properties` (git-ignore it) and reference it
from `app/build.gradle`'s `signingConfigs`, **or** pass via env on the build
machine. Keep secrets out of git — see `.gitignore`.

### 4c. Build the bundle

```bash
cd mobile && npm run publish:android
# -> mobile/android/app/build/outputs/bundle/release/app-release.aab
```
(On this Windows box, apply the `_JAVA_OPTIONS` / clean-`%TEMP%` workaround
from [`MOBILE_BUILD.md`](MOBILE_BUILD.md) and use **JDK 17**.)

### 4d. Play Console

- [ ] Create app → upload `.aab` to **Internal testing** first, then promote
- [ ] **Data safety** form (declare: account email, usage; DPDP minors handling)
- [ ] Content rating questionnaire → Everyone
- [ ] Target audience + "Designed for Families" (under-18 with consent)
- [ ] Phased rollout: 5% → 25% → 100%

---

## 5. iOS — signed release (IPA for App Store)

Two paths — pick one:

**A) Cloud CI (no Mac needed).** Add these GitHub repo secrets, then the
`mobile-ios.yml` workflow can archive a signed `.ipa` (swap the simulator
step for the documented archive+export):
- `APPLE_CERTIFICATE_P12_BASE64`, `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_PROVISIONING_PROFILE_B64`, `APPLE_TEAM_ID`, `KEYCHAIN_PASSWORD`

**B) On a Mac.** `cd mobile && npm run build:prod && npx cap open ios` →
Xcode → Product → Archive → Distribute App → App Store Connect.

App Store Connect steps:
- [ ] Register App ID `in.aipathshala.app` + distribution cert + provisioning profile
- [ ] **Privacy nutrition labels** (email, usage data, no tracking)
- [ ] **Sign in with Apple** — required if you offer Google sign-in (the app does)
- [ ] Upload build → TestFlight → submit for review

---

## 6. Push notifications (optional for v1, recommended)

Full steps in [`MOBILE_BUILD.md`](MOBILE_BUILD.md) "Push notification setup":
- **Android/FCM:** Firebase project → `google-services.json` into `mobile/android/app/`; set the backend FCM key
- **iOS/APNs:** App Store Connect auth key (`.p8`) → backend `APNS_*` env
- **Web Push (PWA):** `npx web-push generate-vapid-keys` → backend `VAPID_*`

If you skip push for v1, the app still works fully (PWA + native shell).

---

## 7. Store listing assets (per app, ×3)

| Field | Value / source |
|---|---|
| App name | AI Pathshala (Student / Parent / Teacher) |
| Subtitle / short desc | "Multilingual AI teacher for every student" |
| Category | Education |
| Age rating | 4+ / Everyone (under-18 supported with parental consent) |
| App icon | already in `mobile/android/app/src/main/res/mipmap-*` |
| Screenshots | needed: phone + 7"/10" tablet; **parent/teacher get their own** |
| Feature graphic (Android) | 1024×500 — TODO |
| Descriptions | EN + 9 Indic languages (catalog in `padhai/locales/`) |
| Privacy URL | `https://YOUR-ORIGIN/privacy` |
| Support URL | `https://YOUR-ORIGIN/support` |

Metadata reference table also in [`MOBILE_BUILD.md`](MOBILE_BUILD.md).

---

## 8. Money / IAP rule (avoid the 15–30% cut)

Subscriptions (M2/M3 tiers) are sold **on the web only** (`/pricing`); the apps
read subscription state from the server and never sell directly — the
"reader app" model (same as Spotify/Netflix). Don't add native IAP unless App
Review forces it. Detail in [`MOBILE_BUILD.md`](MOBILE_BUILD.md) "In-App Purchase note".

---

## 9. Final pre-submission checklist

- [ ] Built against the **production HTTPS** URL (not `10.0.2.2`)
- [ ] Signed release (Android `.aab`, iOS `.ipa`) — debug build is test-only
- [ ] Installs + loads on a real Android phone **and** a real iPhone
- [ ] Sign-in, a lesson/practice flow, and a video embed all work over the prod backend
- [ ] Account deletion reachable; privacy policy linked
- [ ] Push permission copy shows the right app name (if push enabled)
- [ ] Deep link `aipathshala://...` opens the right screen
- [ ] Screenshots + descriptions uploaded (per app)
- [ ] Phased rollout configured

---

## 10. Realistic timeline

| Week | Milestone |
|---|---|
| 0 | Backend live on HTTPS; `npm run build:prod`; keystore + Apple certs created |
| 1 | Student app to Play **Internal testing** + iOS **TestFlight** |
| 2 | Fix review feedback (expect 1 rejection cycle on iOS); Student → Production |
| 3–4 | Parent app submission |
| 5–6 | Teacher app submission |
| ~6 | All three live with separate listings |

---

*Prepared prod-199. The engineering is done — what remains (accounts, signing
keys, store listings, the production deploy) is ops + paperwork, captured as
checkboxes above.*
