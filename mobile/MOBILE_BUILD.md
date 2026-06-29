# Mobile build & go-live runbook (iOS + Android)

The Capacitor config files reference this doc ("See MOBILE_BUILD.md").
It is the **exact, ordered** path from the current state to apps on the
App Store and Google Play.

> **Honest current state (read first).** `mobile/` today is *config
> scaffolding only*: three Capacitor configs (student / parent /
> teacher), the `configure-server` + `check-prod-config` scripts, and
> Cypress smoke for the entry URLs. There are **no `android/` or `ios/`
> native projects yet** (`cap add` has never been run) and the configs
> point at the dev server (`http://10.0.2.2:8000`, cleartext). So:
> nothing is buildable into an APK/IPA right now. The steps below create
> the native projects, switch to production config, build, and submit.
>
> **iOS requires a Mac with Xcode.** There is no way around this — an IPA
> cannot be produced on Windows/Linux. Android can be built on any OS
> with the Android SDK. If you don't have a Mac, options are: a borrowed/
> rented Mac, a Mac mini, or a CI service with macOS runners (e.g. GitHub
> Actions `macos-latest`, Codemagic, EAS-style).

The three apps share one webview wrapper but ship as separate listings:

| Role    | appId                   | App name              | Entry URL (prod)                         |
|---------|-------------------------|-----------------------|------------------------------------------|
| Student | `in.aipathshala.app`    | AI Pathshala          | `https://app.aipathshala.in/?home=math`  |
| Parent  | `in.aipathshala.parent` | AI Pathshala — Parent | `https://app.aipathshala.in/ui?mode=parent` |
| Teacher | `in.aipathshala.teacher`| AI Pathshala — Teacher| `https://app.aipathshala.in/ui?mode=teacher`|

Capacitor 6.x. Plugins already declared: SplashScreen, PushNotifications,
App (deep links `aipathshala://`, `https://app.aipathshala.in`),
StatusBar, Share, Filesystem, Browser, Device.

---

## Dependency order (do NOT skip ahead)

```
1. Deploy the backend → get a real HTTPS URL
2. Install toolchains (Node, Android SDK; + Xcode on a Mac for iOS)
3. cap add android / cap add ios      (one-time — creates native projects)
4. Switch configs to production + validate
5. Build (Android .aab; iOS archive on Mac)
6. Device/emulator test — incl. native plugins (camera/push/filesystem)
7. Store assets + accounts + signing
8. Submit
```

Step 1 gates everything: the app is a webview that loads the live site,
so it needs the production HTTPS URL to exist first.

---

## 1. Deploy the backend, get the prod HTTPS URL

Mobile cannot ship before the web app is live on HTTPS. Deploy per
`docs/DEPLOY.md` (Render). Note the resulting origin, e.g.
`https://app.aipathshala.in`.

`scripts/configure-server.cjs` already defaults its production URL to
`https://app.aipathshala.in`. **If your deployed origin differs**, pass
it explicitly in every command below via `CAPACITOR_SERVER_URL=...`.

App Store / Play **reject plain-HTTP apps** — the URL must be HTTPS.

## 2. Toolchains

- **Both:** Node 18+, `cd mobile && npm install`.
- **Android:** Android Studio + SDK (API 34), JDK 17. `gradlew` needs
  `JAVA_HOME` set.
- **iOS (Mac only):** Xcode + Command Line Tools, CocoaPods
  (`sudo gem install cocoapods`).

## 3. Create the native projects (one-time)

```bash
cd mobile
npm install
npx cap add android      # creates mobile/android/
npx cap add ios          # creates mobile/ios/   (Mac only)
```

This is the missing piece today. Commit the generated `android/` (and
`ios/` from the Mac) so builds are reproducible. Re-run `cap sync` after
any config or plugin change.

> The three apps share `capacitor.config.json` but have distinct
> `appId`s. The simplest path is **three separate Capacitor projects**
> (one per role) OR scripting the appId swap before each `cap add`.
> Decide this before step 3 — see `PARENT_TEACHER_APPS.md`.

## 4. Production config + validation

```bash
# Uses https://app.aipathshala.in by default:
NODE_ENV=production npm run configure:prod

# OR pin your real origin explicitly:
CAPACITOR_SERVER_URL=https://YOUR-REAL-HOST npm run configure:prod

npm run check:prod    # FAILS (exit 1) if any config still points at
                      # localhost/10.0.2.2/192.168 or has cleartext=true
```

`check:prod` must print **"all configs production-ready"** before you
build. It guards against shipping a dev/cleartext config to a store.

## 5. Build

```bash
# Android App Bundle (.aab) for Play:
npm run publish:android
#   → mobile/android/app/build/outputs/bundle/release/app-release.aab
#   (runs configure:prod + check:prod first; needs a signing keystore — see §7)

# iOS (Mac only):
npm run ios:archive          # or: npx cap open ios → Xcode → Product → Archive
```

## 6. Device / emulator test (the real gap)

The Cypress specs (`cypress/e2e/15-mobile-shell.cy.js`,
`16-mobile-interactions.cy.js`) only cover the SPA entry URLs — **not**
native behaviour. Before submitting, manually verify on a real device /
emulator:

- App launches and loads the live site over HTTPS (no cleartext warning).
- **Camera** — the math "take a picture" flow (PushNotifications/camera
  plugin) actually opens the camera and uploads.
- **Push notifications** — token registers; a test push arrives (needs
  FCM for Android / APNs for iOS configured server-side).
- **Filesystem / Share / Browser** plugins behave.
- Deep links `aipathshala://…` and `https://app.aipathshala.in/…` open
  the app.
- Back button (Android), splash screen, status bar color.
- Offline behaviour (webview wrapper = blank when the server is
  unreachable; decide if that's acceptable or needs a cached shell).

## 7. Store assets, accounts, signing (all require your accounts)

**Accounts:** Apple Developer Program (US $99/yr) + Google Play Console
(one-time $25).

**Signing:**
- Android — generate an upload keystore (`keytool`), wire it into
  `android/app/build.gradle` signingConfigs (keep the keystore + password
  out of git).
- iOS — signing certificate + provisioning profile via Xcode "Automatically
  manage signing".

**Assets per app (×3 listings):**
- App icon (1024×1024) + adaptive icon (Android).
- Splash screen (already themed `#5E60CE`).
- Screenshots: phone + tablet, per store spec.
- Short + full description, category (Education).
- **Privacy — load-bearing for this app:** Apple Privacy Nutrition Labels
  and Google **Data Safety** form. You collect minors' data (DPDP, under-18)
  + email + usage → declare it accurately, link the Privacy Policy, and be
  ready for extra review on a kids-adjacent education app.

## 8. Submit

- **Android:** upload the `.aab` to Play Console → internal testing →
  closed → production. `npm run publish:android` prints the path.
- **iOS:** Xcode → Product → Archive → Distribute App → App Store Connect
  → TestFlight → submit for review. (`npm run publish:ios` prints these
  steps.)

---

## Known risks / honest gaps

- **Apple guideline 4.2 (minimum functionality).** A pure website-wrapper
  can be rejected. Mitigations: lean on the native plugins (camera, push,
  share, offline cache) so it isn't "just a browser," and make the mobile
  entry (`/?home=math` scan-and-solve) feel app-native.
- **No native-plugin test harness.** Covered by manual step 6 today; a
  Detox/emulator-bridge suite is the follow-up (`CYPRESS_CAPACITOR.md`).
- **iOS build needs a Mac** — unavoidable; plan for it.
- **The three-app appId split** (§3) must be resolved before `cap add`.

## Quick reference (npm scripts already wired)

| Command                     | What it does |
|-----------------------------|--------------|
| `npm run configure`         | dev config (10.0.2.2 emulator bridge) |
| `npm run configure:prod`    | production config (HTTPS, cleartext off) |
| `npm run check:prod`        | fail-closed validator (run before every build) |
| `npm run android:run`       | configure + `cap run android` (emulator/device) |
| `npm run ios:run`           | configure + `cap run ios` (Mac) |
| `npm run android:build`     | configure:prod + check + `gradlew assembleRelease` |
| `npm run publish:android`   | …+ `bundleRelease` → uploadable `.aab` |
| `npm run ios:archive`       | configure:prod + check + `xcodebuild archive` (Mac) |
