# G4 — Multi-region deploy (Mumbai + Singapore failover)

The v1.5 stack runs in Render's Mumbai region. Mumbai goes down ~4h/yr
per Render's SLA. For schools in the middle of a class period, that's
an outage during peak. G4 adds a warm-standby in Singapore with
Cloudflare Load Balancing handling automatic failover.

## Architecture

```
                          Cloudflare LB
                          (health-checked)
                       /                    \
                      ↓                      ↓
               Mumbai (primary)       Singapore (standby)
               ┌──────────────┐       ┌──────────────┐
               │ web replicas │       │ web replicas │  (cold→warm
               │ 3-5x         │       │ 1x           │   on failover)
               ├──────────────┤       ├──────────────┤
               │ workers      │       │ workers      │
               │ 3-30x        │       │ 1-3x         │
               └──────┬───────┘       └──────┬───────┘
                      │                      │
                      └────────┬─────────────┘
                               ↓
                    ┌──────────────────────┐
                    │  Neon Postgres       │
                    │  Mumbai primary +    │
                    │  Singapore read      │
                    │  replica             │
                    └──────────────────────┘
```

Two failover modes:
- **Healthcheck miss** (Render replica down): Cloudflare LB routes new
  requests to Singapore; existing connections drain. RTO ~30s.
- **Region down** (Mumbai entirely): Singapore promoted to primary by
  flipping the Neon endpoint env var; cold-start delay ~2 min.

## What v1.6 ships (configuration + runbook)

- `padhai/region.py` — small module that reads `PADHAI_REGION`
  ("mumbai" | "singapore") and exposes it for headers + observability
- `render.yaml` updated with a Singapore service block (commented
  until ops provisions it)
- This runbook with promotion + rollback procedures

What's NOT in v1.6 (ops sprint):
- Cloudflare Load Balancing configured
- Singapore Render account region selected
- Neon read replica provisioned
- DNS pointed at Cloudflare LB hostname

## Cutover plan (1-week ops sprint)

### Day 1 — Provision Singapore

```bash
# In Render dashboard:
#   New Web Service from same repo
#   Region: Singapore
#   Environment Group: padhai-singapore (mirrors padhai-mumbai)
#   Min replicas: 1 (warm)
#   Auto-deploy: false (manual gate, matches Mumbai)
```

Set `PADHAI_REGION=singapore` in the env group.

### Day 2 — Cloudflare Load Balancing

1. Cloudflare dashboard → Traffic → Load Balancing → Create LB
2. Origin pool 1: `padhai-mumbai.onrender.com` (weight 100,
   health probe `/health` every 30s)
3. Origin pool 2: `padhai-singapore.onrender.com` (weight 0 until
   failover, same health probe)
4. Steering policy: failover (preserves order; promotes pool 2 only
   when pool 1 is unhealthy)
5. Point `aipathshala.in` at the LB hostname

### Day 3 — Neon read replica

1. Neon dashboard → Project → Add read replica → Singapore
2. Export `NEON_READ_REPLICA_URL` to both environments
3. Update `padhai/db.py` so when `PADHAI_REGION=singapore`, reads
   route to the replica URL (writes still go to Mumbai primary —
   write-fan-out from Singapore is async over the WAN, ~80ms p99
   added latency; acceptable for our workload)

### Day 4 — DNS verify + smoke

```bash
# From a Singapore VPN:
curl -I https://aipathshala.in/health
# Should hit Mumbai by default. Add the LB-routing header to force
# Singapore:
curl -I -H "CF-Backend-Pool: singapore" https://aipathshala.in/health
# Both should return 200; the Server header should reflect region.
```

### Day 5 — Drill

Manually fail Mumbai (Render dashboard → scale to 0):
- Cloudflare LB should detect within 90s + promote Singapore
- New requests route to Singapore
- Existing browser sessions complete + reconnect transparently
- After 5 min, scale Mumbai back up → traffic flips back

Document timings in the SOC 2 evidence dashboard (auditors love DR
drills with timestamps).

## Rollback decision tree

| Symptom | Action |
|---|---|
| Failover triggered by false-positive healthcheck | Tune the probe timeout / threshold |
| Replica lag >5s during normal ops | Increase Neon compute, or accept eventual consistency for reads |
| Cross-region writes timeout | Singapore writes still go to Mumbai primary; if Mumbai is genuinely down, promote the Singapore read-replica to primary via Neon dashboard |
| Brand-new write from Singapore not visible from Mumbai | Wait 1-2s for replication; this is async by design |

## Cost model

- Singapore Render: ~$15/mo for 1 warm replica + 1 worker
- Cloudflare LB: $5/mo per origin pool + $0.50/M health checks
- Neon read replica: ~$15/mo
- Total: ~$50/mo extra for the redundancy

At 10k schools active, this pays for itself the first time Mumbai
goes down (no SLA breach, no support fire).

## When NOT to enable G4

- Pilot phase (<100 schools): the complexity isn't worth the
  ₹4-5k/mo cost
- Pre-revenue: 99.95% uptime isn't a sales blocker yet
- During a major Postgres migration (G1 cutover) — DR + migration
  at once is asking for it

Recommended trigger: when revenue passes ₹1L/mo OR when first
government deal asks about RTO/RPO.
