#!/usr/bin/env node
/**
 * Rewrites the `server.url` field in each Capacitor config to point at
 * the running backend. Run before `cap sync` whenever you switch
 * between local-dev and production builds:
 *
 *   CAPACITOR_SERVER_URL=http://10.0.2.2:8000 node scripts/configure-server.cjs
 *
 * Defaults match the most common dev setup: Android emulator uses
 * 10.0.2.2 to reach the host loopback; iOS simulator uses localhost.
 * Override per-target via CAPACITOR_SERVER_URL_ANDROID /
 * CAPACITOR_SERVER_URL_IOS if your network needs differ.
 *
 * The script edits the JSON in place, preserving all other fields so
 * production builds remain reproducible by re-running with the prod URL.
 *
 * prod-133 — Home-screen path per role:
 *   The `defaultPath` per target sets where the shell lands on launch.
 *   Student shell now defaults to `/?home=math` — a CK-12-inspired
 *   "scan and solve" mobile entry. Override per-role via env:
 *     CAPACITOR_HOME_PATH_STUDENT=/
 *     CAPACITOR_HOME_PATH_PARENT=/ui?mode=parent
 *     CAPACITOR_HOME_PATH_TEACHER=/ui?mode=teacher
 *   See mobile/MOBILE_HOME.md for the design rationale.
 */
const fs = require("fs");
const path = require("path");

const BASE_DIR = path.resolve(__dirname, "..");

const TARGETS = [
  {
    file: "capacitor.config.json",
    role: "student",
    // prod-133: CK-12-inspired mobile entry. Math photo-OCR is the
    // highest-conversion flow for mobile users (in-class photo of a
    // textbook problem). Override with CAPACITOR_HOME_PATH_STUDENT=/
    // to restore the dashboard-first landing.
    defaultPath: "/?home=math",
    envVar: "CAPACITOR_HOME_PATH_STUDENT",
  },
  {
    file: "parent/capacitor.config.json",
    role: "parent",
    defaultPath: "/ui?mode=parent",
    envVar: "CAPACITOR_HOME_PATH_PARENT",
  },
  {
    file: "teacher/capacitor.config.json",
    role: "teacher",
    defaultPath: "/ui?mode=teacher",
    envVar: "CAPACITOR_HOME_PATH_TEACHER",
  },
];

const PROD_URL = "https://app.aipathshala.in";

function pickBaseUrl() {
  const explicit = process.env.CAPACITOR_SERVER_URL;
  if (explicit) return explicit;
  if (process.env.NODE_ENV === "production") return PROD_URL;
  // Android emulator can't reach host's localhost — 10.0.2.2 is the
  // emulator-host bridge. iOS simulator can use localhost directly.
  return "http://10.0.2.2:8000";
}

function pickHomePath(target) {
  if (target.envVar && process.env[target.envVar]) {
    return process.env[target.envVar];
  }
  return target.defaultPath;
}

function configureOne(target, baseUrl) {
  const full = path.join(BASE_DIR, target.file);
  if (!fs.existsSync(full)) {
    console.warn(`[configure] skip ${target.file} (not found)`);
    return;
  }
  const raw = fs.readFileSync(full, "utf-8");
  const cfg = JSON.parse(raw);
  cfg.server = cfg.server || {};
  const homePath = pickHomePath(target);
  const newUrl = baseUrl.replace(/\/$/, "") + homePath;
  cfg.server.url = newUrl;
  // Mixed-content + cleartext only makes sense on http:// (local dev).
  const isHttp = newUrl.startsWith("http://");
  cfg.server.cleartext = isHttp;
  cfg.server.androidScheme = isHttp ? "http" : "https";
  fs.writeFileSync(full, JSON.stringify(cfg, null, 2) + "\n");
  console.log(`[configure] ${target.role.padEnd(8)} → ${newUrl}`);
}

function main() {
  const baseUrl = pickBaseUrl();
  console.log(`[configure] base URL: ${baseUrl}`);
  TARGETS.forEach((t) => configureOne(t, baseUrl));
}

main();
