---
title: Weekly sync, 2026-05-06
---

# Weekly sync, 2026-05-06

Attendees: Mara, Teo, Iris. Notes by Teo.

## Decisions
- Freeze the 2.0.0 release branch on 2026-05-13; only fixes land after that.
- Move the search index rebuild from hourly to every 6 h; it costs 2.4 GB of RAM per run.
- Keep the €1,200 monthly budget for test devices.

## Action items
1. Mara: rerun the import benchmark with `--workers 8` and post the p50 and p99 by 2026-05-08.
2. Teo: rotate the staging deploy key and update ~/deploy/keys/README.md.
3. Iris: draft the 2.0.0 release notes at https://example.net/notes/2.0.0.

## Open questions
- Do we need the legacy `/v1/export` endpoint after 2026-06-30?
- Is 3 days enough for the beta window?
