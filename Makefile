.PHONY: help lint typecheck guides-check test-fast test-ci-meta test-perf test-fuzz test-full test-cov test-alone security

help:
	@echo "Targets:"
	@echo "  lint          Run ruff lint and format checks over repo and scanners"
	@echo "  typecheck     Run mypy over src/"
	@echo "  guides-check  Verify rendered guides match docs/guides/"
	@echo "  test-fast     Run unit suite in parallel (excluding serial perf tests)"
	@echo "  test-ci-meta  Run CI workflow, scanner, and repository invariant checks"
	@echo "  test-perf     Run CPU/wall-clock performance bound tests serially"
	@echo "  test-fuzz     Run full fuzz sweep (CAIRN_FUZZ_PROFILE=full RT2_FULL=1)"
	@echo "  test-full     Run full offline suite (full fuzz + acceptance + perf)"
	@echo "  test-cov      Run non-perf suite with line and branch coverage report"
	@echo "  test-alone    Run every test file in a process of its own (slow)"
	@echo "  security      Run local gitleaks secret history scan"

lint:
	uv run ruff check . .github/scanners
	uv run ruff format --check . .github/scanners

typecheck:
	uv run mypy src/

guides-check:
	uv run python scripts/render_guides.py --check

test-fast:
	uv run pytest tests/unit -n auto --dist loadscope -m "not perf"

test-ci-meta:
	uv run pytest -m ci_meta -n auto --dist loadscope

test-perf:
	uv run pytest -m perf

test-fuzz:
	CAIRN_FUZZ_PROFILE=full RT2_FULL=1 uv run pytest -m fuzz -n auto --dist loadscope

test-full:
	CAIRN_FUZZ_PROFILE=full RT2_FULL=1 uv run pytest -n auto --dist loadscope -m "not perf"
	uv run pytest -m perf

test-cov:
	uv run pytest -n auto --dist loadscope -m "not perf" --cov=cairn --cov-branch --cov-report=term-missing:skip-covered

test-alone:
	uv run python scripts/ci_alone.py

security:
	gitleaks git --redact --ignore-gitleaks-allow --config .gitleaks.toml --exit-code 1
