# Haiku Auto-Classification + Bulk Approve for Review Queue — SCOPE (not built)

## Goal
1. Every crop landing in the review queue gets classified by Claude Haiku (cheapest vision model).
2. Haiku's predicted class becomes the pre-selected option in the review dropdown.
3. Add a bulk-approve tool: "select all" + per-item checkboxes (untick to exclude), one approve action.
4. PHASE 2 (later, after Billy trusts Haiku): let Haiku auto-approve without human review.

## Where this runs — the iMac (NOT Railway)
- Review crops are LOCAL-ONLY on the iMac (`~/bike-counter/data/review_crops/`, deleted on decision
  for privacy). They are never pushed to Railway. So classification must run where the images are.
- Billy reviews via the iMac dashboard at `http://100.69.48.21:5001/review` (Tailscale). That's the
  page we modify (same `dashboard.py` where we built duplicate-collapse + reject-sibling cleanup).
- The public Railway review mirror is metadata-only and unaffected.

## Prerequisites (one-time plumbing)
- Add `ANTHROPIC_API_KEY` to the iMac `~/bike-counter/.env` (reuse the key from this host's
  `~/.hermes/.env`, or mint a dedicated one — recommend dedicated so usage is isolated/observable).
- `pip install anthropic` into the iMac venv (`~/bike-counter/venv`).
- Confirm exact Haiku model id before building. Family is `claude-*-4-*`; the current Haiku
  identifier (e.g. `claude-haiku-4-5`) MUST be verified against the Anthropic API / docs — do not
  assume. Also verify that specific Haiku supports vision (some Haiku point-releases historically did
  not). This is the single open unknown; everything else is confirmed feasible.
- Verify iMac egress to api.anthropic.com (online box; trivial).

## Cost (Billy's "extremely cheap" intuition — confirmed)
- Crop is 313x199 px. Anthropic image tokens ≈ (w*h)/750 ≈ ~83 tokens.
- Per classification: ~83 image + ~150 prompt + ~30 output ≈ ~265 tokens.
- At Haiku pricing this is well under $0.001 per image — fractions of a cent.
- Even at 100 review items/day → a few cents per MONTH. Cost is a non-issue.

## Data model (iMac sqlite `review_queue`)
Add columns:
- `haiku_class INTEGER` — Haiku's predicted canonical class id
- `haiku_conf REAL` — Haiku's self-reported confidence 0-1
- `haiku_reason TEXT` — one-line rationale (shown on hover; useful for trust-building)
- `haiku_at TEXT` — ISO timestamp of classification (null = not yet classified)

## Classification worker (decoupled from the counter)
HARD RULE we've held all session: do NOT put network/API calls in counter.py (the critical loop).
Two viable placements:
- (A) Cron on the iMac every ~2-3 min: SELECT pending rows WHERE haiku_at IS NULL AND crop_path
  exists → classify each → UPDATE row. Survives dashboard restarts; no latency on /review load.
- (B) Background thread inside dashboard.py doing the same poll.
Recommend (A) — cleaner lifecycle, no coupling to the dashboard process, easy to pause/disable.

### Haiku call shape
- Input: the local JPEG (base64) + a tight prompt listing the 7 valid classes:
  0 person, 1 bicycle, 2 car, 3 moped/motorcycle, 5 bus, 7 truck, 100 scooter.
- Ask for strict JSON: {"class_id": <int from the allowed set>, "confidence": <0-1>, "reason": "<=8 words"}.
- Validate class_id is in the allowed set; on parse failure, leave haiku_at set but haiku_class null
  (falls back to YOLO guess in the UI) and log.

## UI changes (iMac /review page)
1. Dropdown pre-selection: default to `haiku_class` when present, else fall back to `class_id` (YOLO).
2. Show a small disagreement signal when Haiku != YOLO, e.g. badge "YOLO: bicycle · Haiku: scooter"
   plus Haiku's one-line reason on hover. (This is the trust-building surface Billy needs before
   Phase 2.)
3. Bulk approve:
   - A "Select all" master checkbox + a checkbox per card (default checked).
   - An "Approve selected (N)" button. Untick any card to exclude it.
   - Each approved item uses its currently-selected dropdown class (Haiku's guess unless manually
     overridden) — so bulk approve respects per-item corrections.
4. Keep existing single Approve/Reject buttons for one-offs.

## Backend (iMac dashboard.py)
- New endpoint `POST /api/review/approve_batch` taking `[{id, class_id}, ...]`; loops the existing
  `approve_review(id, new_class_id)` (already handles promotion to crossings + dup-sibling cleanup +
  crop deletion). Returns per-id results.
- `/api/review` list already returns rows; extend to include haiku_class/conf/reason.

## Phase 2 (future, explicitly deferred by Billy)
- Config flag `auto_approve.enabled` + `auto_approve.min_conf`. When on, the classification worker
  auto-promotes items where Haiku confidence >= threshold (and optionally only when Haiku agrees with
  YOLO, as a safety AND-gate), skipping the human queue. Items below threshold or on disagreement
  still go to manual review. Fully reversible flag.
- Recommend a "shadow period": log what auto-approve WOULD have done vs Billy's actual decisions for
  a week, measure agreement, THEN flip it on. (Mirrors the double-count watchdog pattern.)

## Verification plan (when built)
- Unit: feed known crops, confirm Haiku returns valid in-set class_ids.
- Confirm dropdown pre-selects Haiku class; disagreement badge shows.
- Bulk approve: select all → approve → confirm rows promoted to crossings, crops deleted, queue clears.
- Confirm a per-item untick excludes it; confirm a manual dropdown override wins over Haiku in bulk approve.
- Cost check: log token usage per call, confirm ~sub-cent.

## Open question for Billy
- Dedicated Anthropic key for the iMac, or reuse the existing one? (Recommend dedicated.)
- Phase 1 auto-classify-but-still-review only, correct? (Yes per Billy — Phase 2 auto-approve comes later.)
