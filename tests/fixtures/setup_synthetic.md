# Fernway API dev setup

Set up a laptop for the Fernway API in about 15 min. Tested on 2026-05-20 with Python 3.12.4 and Postgres 16.2.

## Prerequisites
- Install `uv` 0.9.1 or later and Docker.
- Copy ./config/dev.example.toml to ~/.config/fernway/dev.toml and keep it out of git.

## Start
```bash
docker compose up -d db
uv sync --frozen
uv run fernway migrate --env dev
uv run fernway serve --port 8080 --reload
```

## Environment
| Variable | Purpose | Default |
|---|---|---|
| `FERNWAY_DB_URL` | Postgres connection | `postgresql://localhost:5432/fernway` |
| `FERNWAY_LOG_LEVEL` | log verbosity | `info` |

## Gotchas
- `--reload` watches ./src only; restart after editing ./config.
- Seed data: `./scripts/seed.sh --rows 500` loads 500 rows in about 10 s.

> Never point FERNWAY_DB_URL at production from a laptop.
