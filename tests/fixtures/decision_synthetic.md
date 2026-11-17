# ADR-012: Queue for the ingest worker

Status: accepted on 2026-02-18. Owner: the platform team.

## Context
The ingest worker handles 2,000 messages/s at peak and must not drop one. Today it polls a SQL table every 5 s, which adds up to 5 s of delay and 40% of the database's CPU.

## Options
| Option | p95 delay | Cost per month | Ops burden |
|---|---|---|---|
| Keep polling | 5 s | $0 | none |
| Corvid 3.2.0 | 80 ms | $45 | one more service |
| Hosted queue | 120 ms | $310 | vendor lock-in |

## Decision
Use Corvid 3.2.0 with `--max-inflight 32` and `--ack-timeout 30s`. It meets the 200 ms target at the lowest cost.

## Rejected
- **Keep polling:** the 5 s delay breaks the 200 ms target.
- **Hosted queue:** $310 per month is 7 times the Corvid cost, and it ties us to one vendor.

## Consequences
- Corvid runs as a sidecar: `corvid serve --port 7400 --data ~/corvid-data`.
- Runbook: https://example.org/runbooks/corvid.
