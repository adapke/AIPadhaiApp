"""prod-217 — Resolve concept-video placeholder URLs into real videos via the
YouTube Data API v3.

The legacy prod-14 seed left `channel_seed` rows whose source_url is a
`youtube.com/@Channel/search?query=…` placeholder (a curator TODO marker that
can't embed). This script does what a human curator would: for each such row it
runs a CHANNEL-RESTRICTED, embeddable-only search on the intended channel, takes
the top relevant hit, confirms `status.embeddable` via the API, writes the real
watch URL + title + duration back onto the row, and promotes it to `verified`
(prod-216's guard now permits it because the URL is playable). Rows that are
already playable are just re-verified.

The API key is read from the YT_API_KEY environment variable and is NEVER
printed, logged, or written to any file.

Usage:
    YT_API_KEY=... PYTHONPATH=. python scripts/youtube_curate.py --dry-run
    YT_API_KEY=... PYTHONPATH=. python scripts/youtube_curate.py
    YT_API_KEY=... PYTHONPATH=. python scripts/youtube_curate.py --tier channel_seed --limit 5
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

_API = "https://www.googleapis.com/youtube/v3"
_PLACEHOLDER = re.compile(r"youtube\.com/@([^/]+)/search\?query=(.+)$", re.I)
_ISO_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _get(path: str, params: dict, key: str) -> dict:
    params = {**params, "key": key}
    url = f"{_API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "AIPadhaiApp/curator (prod-217)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _iso_to_sec(iso: str) -> int | None:
    m = _ISO_DUR.fullmatch(iso or "")
    if not m:
        return None
    h, mn, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mn * 60 + s


def _resolve_channel_id(handle: str, key: str, cache: dict) -> str | None:
    if handle in cache:
        return cache[handle]
    cid = None
    with contextlib.suppress(Exception):
        data = _get("channels", {"part": "id", "forHandle": handle}, key)
        items = data.get("items") or []
        if items:
            cid = items[0]["id"]
    cache[handle] = cid
    return cid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report proposed changes; write nothing.")
    ap.add_argument("--tier", default="channel_seed", help="Which tier to curate (default channel_seed).")
    ap.add_argument("--limit", type=int, default=0, help="Cap rows processed (0 = all).")
    ap.add_argument("--sleep-ms", type=int, default=150, help="Pause between API calls.")
    args = ap.parse_args()

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    key = os.environ.get("YT_API_KEY", "").strip()
    if not key:
        print("ERROR: set YT_API_KEY in the environment (never commit it).", file=sys.stderr)
        return 2

    from padhai import concept_videos as cv

    rows = cv.list_curator_queue(quality_tier=args.tier, limit=args.limit or 10000)
    ch_cache: dict = {}
    report = {"processed": 0, "resolved": 0, "verified_playable": 0, "skipped": 0, "errors": 0}

    for row in rows:
        report["processed"] += 1
        try:
            if cv.is_playable_video_url(row.source_url):
                # Already a real video — just confirm + verify.
                if not args.dry_run:
                    cv.set_quality_tier(row.id, "verified",
                                        curator_note="prod-217: already playable, verified")
                report["verified_playable"] += 1
                print(f"  VERIFY  {row.concept[:40]:40} (already playable)")
                continue

            m = _PLACEHOLDER.search(row.source_url or "")
            if not m:
                report["skipped"] += 1
                print(f"  SKIP    {row.concept[:40]:40} (unrecognised non-playable URL)")
                continue
            handle = "@" + m.group(1)
            query = urllib.parse.unquote_plus(m.group(2))
            chan_id = _resolve_channel_id(handle, key, ch_cache)
            time.sleep(args.sleep_ms / 1000.0)

            search_params = {
                "part": "snippet", "type": "video", "videoEmbeddable": "true",
                "maxResults": "3", "q": query, "order": "relevance",
            }
            if chan_id:
                search_params["channelId"] = chan_id
            if (row.language or "en") != "en":
                search_params["relevanceLanguage"] = row.language
            sres = _get("search", search_params, key)
            time.sleep(args.sleep_ms / 1000.0)
            items = sres.get("items") or []
            if not items:
                report["skipped"] += 1
                print(f"  NORESULT {row.concept[:38]:38} (channel={handle} q={query!r})")
                continue

            vid = items[0]["id"]["videoId"]
            vtitle = items[0]["snippet"]["title"]
            vchan = items[0]["snippet"]["channelTitle"]
            # Authoritative embeddable check + duration.
            vres = _get("videos", {"part": "contentDetails,status", "id": vid}, key)
            time.sleep(args.sleep_ms / 1000.0)
            vitems = vres.get("items") or []
            if not vitems or not vitems[0]["status"].get("embeddable", False):
                report["skipped"] += 1
                print(f"  NOEMBED {row.concept[:38]:38} (top hit not embeddable)")
                continue
            dur = _iso_to_sec(vitems[0]["contentDetails"].get("duration", ""))
            new_url = f"https://www.youtube.com/watch?v={vid}"

            print(f"  RESOLVE {row.concept[:34]:34} -> {vid} | {vtitle[:44]}")
            if not args.dry_run:
                cv.update_video(
                    row.id, source_url=new_url, title=vtitle, channel=vchan,
                    duration_sec=dur,
                    curator_note=f"prod-217: auto-curated via YouTube Data API (channel {handle}, q={query!r})",
                )
                cv.set_quality_tier(row.id, "verified",
                                    curator_note="prod-217: verified — API status.embeddable=true")
            report["resolved"] += 1
        except Exception as e:
            report["errors"] += 1
            print(f"  ERROR   {row.concept[:40]:40} — {type(e).__name__}: {str(e)[:120]}")

    print("\nsummary:", json.dumps(report, sort_keys=True))
    print("(dry-run — no writes)" if args.dry_run else "(writes committed to DB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
