---
title: Quill and Lumen runbook
date: 2026-03-14
tags: [runbook, cli]
---

# Quill and Lumen runbook

How one script drives two fictional CLIs headless. Checked on 2026-03-14 against Quill 2.4.1 and Lumen 0.9.0-beta.2.

> Re-check versions after 30 days.

## Quill — `quill run`
- **Invoke:** `quill run "<task>" --json --workdir ./scratch`.
  - `--json` prints one event per line.
  - `--out last.txt` writes the final message to a file.
- **Timeout:** pass `--max-seconds 300`; the default is 5 min.
- **Cache off:** `--set cache.enabled=false --set cache.write=false`.
- **Config:** defaults live in ~/.config/quill/config.toml.
- **Cost:** a 40-file run cost $1.15 and peaked at 1.2 GB of RAM.

## Lumen — `lumen exec`
- **Invoke:** `lumen exec "<task>" --format jsonl`.
- **Safety:** `--read-only` and `--no-network` give a read-only check.
- **Quiet:** add --quiet to drop progress lines.
- **Cache:** Lumen has no cache store.
- **Settings:** /etc/lumen/settings.json, mode 0600.
- **Known gap:** on 2026-03-12 Lumen ignored the `<Kind>: <subject>` label format.

| Tool | Version | Headless |
|---|---|---|
| Quill | 2.4.1 | `quill run` |
| Lumen | 0.9.0-beta.2 | `lumen exec` |

## Copy-paste: read-only check
```bash
# quill
quill run "<q>" --json --set cache.enabled=false
# lumen
lumen exec "<q>" --format jsonl --read-only
```

Two checks per day keep usage under 2,500 tokens. Docs: https://example.com/quill-lumen.

## Notes
- **Duplicate:** keep this line.
- **Duplicate:** keep this line.
