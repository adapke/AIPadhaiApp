# I1 + I2 — Mobile build runbook (Capacitor wrapper)

The PWA (D3, shipped v1.0) already runs on every modern phone, but:
- iOS Safari aggressively downgrades PWA install prompts
- App Store presence drives school-procurement credibility
- Native push (FCM/APNs) works only inside a native shell

v1.4 ships the **Capacitor scaffolding** that wraps the existing PWA
as a thin native shell — ~99% code reuse, no SPA rewrite. This doc
covers what's needed to build + publish both stores.

## What v1.4 ships (code)

- `mobile/capacitor.config.json` — single source of truth for both
  platforms (app ID, push config, deep links, status bar, splash)
- `mobile/package.json` — Capacitor 6 deps + npm scripts for the
  standard ios/android workflows
- `mobile/src/native-bridge.js` — JS bridge loaded by the Capacitor
  shell before the SPA. Wires I3 push registration (calls
  `/api/users/me/push-tokens` after FCM/APNs grants permission +
  returns a token), native share sheet, deep-link routing.

## What v1.4 doesn't ship (ops + paperwork)

- iOS native project bootstrap (`npx cap add ios`) — requires Xcode
  installed on a Mac. Run on a dev laptop, commit `mobile/ios/` to
  the repo afterwards.
- Android native project bootstrap (`npx cap add android`) — requires
  Android Studio. Same flow as iOS; commit `mobile/android/`.
- App Store / Play Store accounts (₹8000 + $25/yr respectively)
- App Store privacy nutrition labels + Play Store data-safety form
- Screenshots, descriptions, marketing material
- Apple App Review submission (1-2 weeks; expect 1 rejection cycle)

## Local dev quickstart

```bash
cd mobile
npm install
npx cap add ios       # one-time, requires Mac + Xcode
npx cap add android   # one-time, requires Android Studio

# Iterate on JS (the SPA at /ui)
npm run build         # → cap sync (copies web assets into native projects)
npm run ios:run       # launches simulator
npm run android:run   # launches emulator
```

## Verified build — Windows (prod-198)

Both apps were bootstrapped and the **Android debug APK built green** on a
Windows 11 box (2026-06-30). Recorded so it's reproducible.

Toolchain that worked: Node 24, JDK **17** (Gradle 8.2.1 in the Capacitor 6
template does **not** support JDK 21 — point `JAVA_HOME` at a 17), Android
SDK with `platform-tools` + `platforms;android-34` + `build-tools;34.0.0`.

```powershell
cd mobile
npm install
npx cap add android            # one-time: scaffolds mobile/android/
npm run build                  # configure server URL + cap sync

$env:JAVA_HOME    = "C:\Program Files\Eclipse Adoptium\jdk-17.x.x-hotspot"
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
cd android
.\gradlew.bat assembleDebug    # -> app/build/outputs/apk/debug/app-debug.apk
```

Result: package `in.aipathshala.app` v1.0, ~4.4 MB, label "AI Pathshala".
Sideload with `adb install app-debug.apk`.

**Windows gotcha — AF_UNIX self-pipe.** If `gradlew` dies with
`java.io.IOException: Unable to establish loopback connection`
(`sun.nio.ch.UnixDomainSockets.connect0 ... Invalid argument`), your
`%TEMP%` is resolving to an 8.3 short path (e.g.
`C:\Users\ANKUS~1\AppData\Local\Temp`) and this JDK's WEPoll NIO selector
can't bind its AF_UNIX self-pipe there. Give every JVM a clean ASCII temp
dir for the build session — **machine-local, never commit this**:

```powershell
mkdir C:\gtmp
$env:TEMP = "C:\gtmp"; $env:TMP = "C:\gtmp"
$env:_JAVA_OPTIONS = "-Djava.io.tmpdir=C:\gtmp"
.\gradlew.bat assembleDebug
```

**iOS is built in cloud CI** (`.github/workflows/mobile-ios.yml`, `macos-14`
runners) — Xcode is macOS-only so it can't compile on this Windows box. The
generated `mobile/ios/` project is rebuilt by CI each run and is git-ignored.

## Production builds

**Android (.aab for Play Store):**
```bash
cd mobile/android
./gradlew bundleRelease
# Upload android/app/build/outputs/bundle/release/app-release.aab
# to Play Console → Production → Create new release
```

**iOS (.ipa for App Store):**
```bash
npx cap open ios
# Xcode → Product → Archive → Distribute App → App Store Connect
```

## Push notification setup

Push works only after both platforms are configured:

### FCM (Android)
1. Create a Firebase project at console.firebase.google.com
2. Add an Android app with `applicationId = in.aipathshala.app`
3. Download `google-services.json` → place in `mobile/android/app/`
4. Set `FCM_SERVER_KEY` (legacy) or service-account JSON env var on
   the backend so `padhai/push.py:_send_fcm` can call the v1 API

### APNs (iOS)
1. App Store Connect → Keys → New Auth Key (`.p8`)
2. Note the Key ID + Team ID
3. Set backend env vars: `APNS_KEY_ID`, `APNS_TEAM_ID`,
   `APNS_AUTH_KEY` (the .p8 contents, base64-encoded)
4. Backend's `padhai/push.py:_send_apns` activates automatically
   when these are present

### Web Push (PWA fallback)
For users on Chrome/Edge/Firefox who don't install the native app:
1. Generate a VAPID keypair: `npx web-push generate-vapid-keys`
2. Set `VAPID_PUBLIC_KEY` + `VAPID_PRIVATE_KEY` + `VAPID_SUBJECT`
   (`mailto:admin@aipathshala.in`) on the backend
3. SPA registers via `PushManager.subscribe()` with the public key

## Sign-off checklist before publishing

- [ ] iOS app builds + runs on iPhone (test on 14 Pro Max + SE for
      screen-size range)
- [ ] Android app builds + runs on Pixel 7 + a 4GB-RAM Realme (test
      low-end pattern)
- [ ] Push permission prompt shows correct app name + reason text
- [ ] FCM-sent test push delivers on Android within 30s
- [ ] APNs-sent test push delivers on iOS within 30s
- [ ] Deep link `aipathshala://lesson/<id>` routes to the right SPA
      screen
- [ ] Share sheet works from a generated video (Web Share fallback
      verified on PWA)
- [ ] Account deletion flow (Apple-required since 2022) reachable
      from Settings → Account
- [ ] Sign in with Google works (iOS-specific Apple-sign-in pending
      v1.4.x — App Store requires it for new apps with social login)

## App Store / Play Store metadata

| Field | Value |
|---|---|
| App name | AI Pathshala |
| Subtitle (iOS) / Short desc (Android) | Multilingual AI teacher for every student |
| Category | Education |
| Age rating | 4+ (no objectionable content; under-13 with parental consent flow per S2) |
| Privacy URL | https://aipathshala.in/privacy |
| Support URL | https://aipathshala.in/support |
| Languages | en-IN, hi-IN, ta-IN, te-IN, mr-IN, kn-IN, bn-IN, gu-IN, ml-IN, pa-IN |
| Pricing | Free with in-app subscriptions (M2/M3 tiers) |

## In-App Purchase note

Apple takes 30% on iOS IAP (15% if revenue <$1M/yr). To avoid:
- Subscriptions sell only on web (`/billing`)
- iOS app reads subscription state from server; doesn't sell directly
- Same pattern as Spotify, Netflix on iOS

Apple has occasionally rejected apps for "lacking" IAP. Workaround if
rejected: add a token IAP for one premium feature; keep the bulk of
upgrades on web. Defer this fight until App Review pushes back.

## Rollback plan

Native apps don't have a "rollback" the way server deploys do —
once an update is approved + downloaded, users have it. Mitigations:
- Feature-flag every native-only path so we can server-side disable
- Phased Play Store rollout (start 5% → 25% → 100% over a week)
- TestFlight beta with the founder + 5 design partners for 48h
  before each store push
- Keep the PWA fully featured so a critical native bug isn't
  user-stranding — they can still use the web version
