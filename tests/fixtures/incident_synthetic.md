---
title: Ledgerly sync outage
date: 2026-04-02
tags: [incident, postmortem]
---

# Ledgerly sync outage

On 2026-04-02 the nightly `ledgerly sync` job failed for 47 min and left 1,830 invoices unsynced. No data was lost.

## Timeline
- **2026-04-02T02:10Z:** the job starts with `--pool-size 20` and stalls on the third batch.
- **2026-04-02T02:31Z:** the on-call engineer sees p95 latency at 840 ms and an error rate of 12%.
- **2026-04-02T02:57Z:** a rerun with `--pool-size 64 --retry 3` completes.

## Root cause
The pool size of 20 was set in v3.1.0, when a batch held 200 rows. Batches now hold 1,000 rows, so every connection stayed busy and new requests timed out after 30 s.

## Fix
```bash
# raise the pool and retry transient errors
ledgerly sync --pool-size 64 --retry 3
ledgerly verify --since 2026-04-01
```

- The default now lives in /etc/ledgerly/sync.yaml as `pool_size: 64`.
- Release v3.2.0 ships the new default; see https://example.com/ledgerly/3.2.0.

## Follow-ups
- Alert when p95 latency stays above 500 ms for 5 min.
- Add a load test with 1,000-row batches before each release.
