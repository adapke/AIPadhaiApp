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
 */
const fs = require("fs");
const path = require("path");

const BASE_DIR = path.resolve(__dirname, "..");

const TARGETS = [
  {
    file: "capacitor.config.json",
    role: "student",
    defaultPath: "",
  },
  {
    file: "parent/capacitor.config.json",
    role: "parent",
    defaultPath: "/ui?mode=parent",
  },
  {
    file: "teacher/capacitor.config.json",
    role: "teacher",
    defaultPath: "/ui?mode=teacher",
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

function configureOne(target, baseUrl) {
  const full = path.join(BASE_DIR, target.file);
  if (!fs.existsSync(full)) {
    console.warn(`[configure] skip ${target.file} (not found)`);
    return;
  }
  const raw = fs.readFileSync(full, "utf-8");
  const cfg = JSON.parse(raw);
  cfg.server = cfg.server || {};
  const newUrl = baseUrl.replace(/\/$/, "") + target.defaultPath;
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
