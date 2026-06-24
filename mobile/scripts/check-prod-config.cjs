#!/usr/bin/env node
/**
 * prod-177 — Mobile shell production-config validator.
 *
 * Walks the three Capacitor config files (student / parent / teacher)
 * and confirms that NONE of them are pointing at the Android emulator
 * loopback (`10.0.2.2`) or any other obvious dev URL before a
 * production app-store build.
 *
 * Run as part of `npm run build:prod` to catch the very common
 * mistake of forgetting to set CAPACITOR_SERVER_URL before bundling.
 *
 * Exit codes:
 *   0 — all three configs point at a production URL (https://, not 10.0.2.2)
 *   1 — at least one config points at a dev loopback / placeholder
 */

const fs = require("fs");
const path = require("path");

const BASE_DIR = path.resolve(__dirname, "..");

const CONFIGS = [
  { role: "student", file: "capacitor.config.json" },
  { role: "parent",  file: path.join("parent", "capacitor.config.json") },
  { role: "teacher", file: path.join("teacher", "capacitor.config.json") },
];

const DEV_PATTERNS = [
  /^http:\/\/10\.0\.2\.2/,         // Android emulator loopback
  /^http:\/\/localhost/,            // dev only
  /^http:\/\/127\.0\.0\.1/,
  /^http:\/\/192\.168\./,          // LAN test
  /TODO_REPLACE/i,                 // generate_prod_secrets placeholder
  /example\.com/,
  /your[-_]?domain/i,
];

function check(role, file) {
  const full = path.join(BASE_DIR, file);
  if (!fs.existsSync(full)) {
    return { role, file, ok: false, reason: "config file missing" };
  }
  let cfg;
  try {
    cfg = JSON.parse(fs.readFileSync(full, "utf8"));
  } catch (e) {
    return { role, file, ok: false, reason: `parse error: ${e.message}` };
  }
  const url = (cfg.server && cfg.server.url) || "";
  if (!url) {
    return {
      role, file, ok: false,
      reason: "server.url is empty — Capacitor will fall back to the bundled web assets only",
    };
  }
  for (const pat of DEV_PATTERNS) {
    if (pat.test(url)) {
      return {
        role, file, url, ok: false,
        reason: `server.url ${JSON.stringify(url)} matches dev-pattern ${pat}`,
      };
    }
  }
  if (!url.startsWith("https://")) {
    return {
      role, file, url, ok: false,
      reason: `server.url is not https:// (mobile shells block mixed content): ${url}`,
    };
  }
  if (cfg.server.cleartext) {
    return {
      role, file, url, ok: false,
      reason: "server.cleartext=true — turn this off for production builds",
    };
  }
  return { role, file, url, ok: true };
}

function main() {
  console.log("[mobile-prod-check] verifying Capacitor configs:");
  let failures = 0;
  for (const { role, file } of CONFIGS) {
    const result = check(role, file);
    const tag = result.ok ? "[OK]" : "[FAIL]";
    const detail = result.url ? `url=${result.url}` : result.reason;
    console.log(`  ${tag} ${role.padEnd(7)} (${file}) — ${detail}`);
    if (!result.ok) {
      failures += 1;
      if (result.url) console.log(`         reason: ${result.reason}`);
    }
  }
  if (failures > 0) {
    console.log("");
    console.log(`[mobile-prod-check] FAIL — ${failures}/3 configs are not production-ready.`);
    console.log("Fix: re-run with the prod URL set, e.g.");
    console.log("  CAPACITOR_SERVER_URL=https://api.yourdomain.com \\");
    console.log("    node scripts/configure-server.cjs");
    console.log("");
    console.log("Then re-run this check, then `cap sync` + your app-store build.");
    process.exit(1);
  }
  console.log("");
  console.log("[mobile-prod-check] all configs production-ready.");
  console.log("Safe to run `cap sync && cap build android` (or ios).");
}

main();
