"""Plans: what a hosted workspace may store, use and ask for, as the operator configures it.

Contract (frozen by tests/acceptance/test_h6_quotas.py):
- `Plan(label, notes_per_month=None, storage_bytes=None, projects=None,
  model_budget_micros=None, requests_per_minute=None)`: `label` is what people read ("Free");
  each cap is a whole number of at least 0, or None for no cap; `requests_per_minute` is at
  least 1, or None for no rate limit. ValueError otherwise. `plan.limits()` is the
  cairn.store.Limits a workspace's view is limited by (`Store.limited`). A plan may also
  have `versions_per_page` (keyword, at least 1; None: every version is kept): how many
  versions of each page a workspace on it keeps.
- `plans.limits_of(name) -> Limits` is resolve's plan's limits: the resolver a limited view
  asks inside each write. `plans.limit(view, plan)` is `view` held that way, every cap the
  plan in force at each write, with only `plan`'s model budget fixed: how requests, source
  syncs, the arranger and `import --plans` limit a workspace's view.
- `limit_sentence(limit, cap) -> str` is one limit in words, the one place they are written
  ("5,000 notes a month", "500 MB of notes", "40 projects", "the last 50 versions of each
  page", "$2.00 of model use a month", "120 requests a minute"; a cap of None has words
  too, "any number of projects"); `limit_sentences(plan) -> dict[str, str]` is every limit of
  a plan that way, in SAID's order. /usage and the 402 answers say a plan's limits with
  them. `quantity(limit, count) -> str` is an amount of one limit,
  as /usage says what was used and the cap ("12,345", "768 bytes", "500 MB", "$1.25").
- `Plans(table, default)`: `table` maps a plan's name to its Plan, and `default` names one of
  them (ValueError otherwise). A name is 1 to cairn.store.PLAN_NAME_MAX characters without
  surrounding blanks, as a workspace records it (`Store.set_plan`). `plans.resolve(name)`
  is (name, Plan) for the plan a workspace that records `name` is on: the default for None
  or for a name the table does not have.
- `load(path) -> Plans` reads the same from a YAML file with the keys `default` and
  `plans` (a mapping of names to plans, each with `label` and any of the caps). A missing
  default, an unknown key, a key given twice, a number that is negative or not whole, or a
  file that is not such a mapping is a ValueError that names what is wrong. So is a plan
  with `storage_bytes` and no `notes_per_month`: what a workspace takes is at most 30 times
  its storage cap plus a fixed cost for each note it holds (cairn.store's contract), and
  only the cap on notes a month bounds how fast the second grows. `Plan(...)` built in code
  is not held to it.

Plans are data: no plan's name or number is in the code. The table is the server's
configuration (`serve --plans FILE`), never the database's, which records only the name a
workspace is on. deploy/plans.example.yaml is an example whose caps are placeholders.
"""

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from cairn.store import Store
from cairn.store.usage import BYTES_PER_PASSAGE, CAPS, LIMITS, Limits, check_plan_name

# What a plan in a plans file may hold.
FIELDS = ("label", *LIMITS, "requests_per_minute", "versions_per_page")
# The order a plan's limits are said in (limit_sentences).
SAID = ("notes_per_month", "storage_bytes", "passages", "projects", "versions_per_page")
SAID += ("model_budget_micros", "requests_per_minute")
_UNITS = ((1_000_000_000, "GB"), (1_000_000, "MB"), (1_000, "kB"))


def _cap(value: object, name: str, least: int = 0) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < least
    ):
        raise ValueError(f"{name} is a whole number of at least {least}, or none: not {value!r}")


@dataclasses.dataclass(frozen=True)
class Plan:
    """One plan: its label, its caps (None: no cap) and its rate limit (None: none)."""

    label: str
    notes_per_month: int | None = None
    storage_bytes: int | None = None
    projects: int | None = None
    model_budget_micros: int | None = None
    requests_per_minute: int | None = None
    versions_per_page: int | None = None  # the versions of each page kept; None: every one

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError(f"a plan's label is some text people read: not {self.label!r}")
        for name in LIMITS:
            _cap(getattr(self, name), name)
        _cap(self.requests_per_minute, "requests_per_minute", least=1)
        _cap(self.versions_per_page, "versions_per_page", least=1)

    def limits(self) -> Limits:
        """The caps a workspace on this plan is held to, as a limited view takes them."""
        return Limits(**{name: getattr(self, name) for name in CAPS})


def limit_sentence(limit: str, cap: int | None) -> str:
    """One limit of a plan in words, as /usage and a 402 say it: `limit` one of
    SAID, `cap` its number or None (no cap). "5,000 notes a month", "500 MB of notes"
    (decimal units), "1,500 passages of notes", "40 projects", "the last 50 versions of each
    page", "$2.00 of model use a month", "120 requests a minute". ValueError: another
    limit."""
    if limit not in SAID:
        raise ValueError(f"no words for {limit!r}: a limit is one of {', '.join(SAID)}")
    if cap is None:
        return {
            "notes_per_month": "any number of notes a month",
            "storage_bytes": "notes of any size in all",
            "passages": "any number of passages",
            "projects": "any number of projects",
            "versions_per_page": "every version of each page",
            "model_budget_micros": "model use without a monthly cap",
            "requests_per_minute": "requests without a rate limit",
        }[limit]
    if limit == "notes_per_month":
        return f"{cap:,} {_plural(cap, 'note')} a month"
    if limit == "storage_bytes":
        return f"{_size(cap)} of notes"
    if limit == "passages":
        return f"{cap:,} {_plural(cap, 'passage')} of notes"
    if limit == "projects":
        return f"{cap:,} {_plural(cap, 'project')}"
    if limit == "versions_per_page":
        return (
            "the latest version of each page"
            if cap == 1
            else f"the last {cap:,} versions of each page"
        )
    if limit == "model_budget_micros":
        return "no model use" if cap == 0 else f"${cap / 1_000_000:,.2f} of model use a month"
    return f"{cap:,} {_plural(cap, 'request')} a minute"


def limit_sentences(plan: Plan) -> dict[str, str]:
    """Every limit of `plan` in words (limit_sentence), by limit, in SAID's order."""
    return {limit: limit_sentence(limit, plan_cap(plan, limit)) for limit in SAID}


def plan_cap(plan: Plan, limit: str) -> int | None:
    """The cap `plan` sets on `limit` (one of SAID), None for none: "passages" follows from
    the storage cap, one per cairn.store.BYTES_PER_PASSAGE bytes of it."""
    if limit == "passages":
        storage = plan.storage_bytes
        return None if storage is None else storage // BYTES_PER_PASSAGE
    return getattr(plan, limit)


def quantity(limit: str, count: int) -> str:
    """An amount of one of LIMITS (or "passages") as people say it, without saying what it
    counts: "12,345" notes, passages or projects, "768 bytes" or "500 MB" stored (decimal
    units), "$1.25" of model use. How /usage says what was used of a limit and its cap.
    ValueError: another limit."""
    if limit == "storage_bytes":
        return _size(count)
    if limit == "model_budget_micros":
        return f"${count / 1_000_000:,.2f}"
    if limit in ("notes_per_month", "projects", "passages"):
        return f"{count:,}"
    raise ValueError(f"no amount for {limit!r}: a limit is one of {', '.join(LIMITS)}")


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def _size(count: int) -> str:
    for unit, name in _UNITS:
        if count >= unit:
            return f"{f'{count / unit:,.2f}'.rstrip('0').rstrip('.')} {name}"
    return f"{count:,} {_plural(count, 'byte')}"


class Plans:
    """The table of plans by name, and the name of the default plan."""

    def __init__(self, table: Mapping[str, Plan], default: str) -> None:
        for name, plan in table.items():
            check_plan_name(name)  # the rule a workspace's recorded name keeps, in its words
            if not isinstance(plan, Plan):
                raise ValueError(f"plan {name!r} is not a Plan")
        if default not in table:
            raise ValueError(f"the default plan {default!r} is not one of {sorted(table)}")
        self.table: Mapping[str, Plan] = MappingProxyType(dict(table))
        self.default = default

    def resolve(self, name: str | None) -> tuple[str, Plan]:
        """The plan a workspace that records `name` is on, with its name: the default for
        None or for a name this table does not have."""
        if name is not None and name in self.table:
            return name, self.table[name]
        return self.default, self.table[self.default]

    def in_force(self, store: Store, account_id: str) -> tuple[str, Plan]:
        """The plan workspace `account_id` is on now, with its name: the one the operator
        recorded with plan-set (`Store.plan_in_force`, one read, no write), else the
        default, resolved against this table. The one place a workspace's plan is resolved:
        requests (web.auth.Accounts.memory), source syncs (sources.runner.sync_all and
        `source-sync`) and the arranging worker (web.cli_arrange.worker_views). KeyError: no
        such workspace."""
        return self.resolve(store.plan_in_force(account_id))

    def limits_of(self, name: str | None) -> Limits:
        """The caps of the plan a workspace that records `name` is on (resolve): the
        resolver a limited view asks inside each write (`Store.limited(resolve=...)`), with
        the name the store reads by Store.plan_in_force's rule in that write's transaction.
        So a request, a source pass or the arranger admitted on one plan writes under the
        plan in force when it writes."""
        return self.resolve(name)[1].limits()

    def limit(self, view: Store, plan: Plan) -> Store:
        """`view` held to its workspace's plans: every cap is the plan in force when each
        write is made (limits_of, asked inside the write), so a change of plan after `plan`
        was resolved, down or up, holds or frees the next write. Only the model budget is
        `plan`'s, the one the caller resolved (Store.model_budget_left reads it)."""
        budget = Limits(model_budget_micros=plan.model_budget_micros)
        return view.limited(budget, resolve=self.limits_of)


class _Loader(yaml.SafeLoader):
    """Safe YAML, where a key given twice in one mapping is an error, not the last one."""


def _mapping(loader: _Loader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ValueError(f"{key!r} is given twice")
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load(path: str | Path) -> Plans:
    """The plans in a YAML file (see the module's contract). ValueError names what is wrong;
    OSError when the file cannot be read."""
    try:
        data = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_Loader)  # a safe loader
    except yaml.YAMLError as error:
        raise ValueError(f"the plans file is not YAML: {error}") from None
    if not isinstance(data, dict):
        raise ValueError("the plans file is a mapping with the keys default and plans")
    if unknown := sorted(set(data) - {"default", "plans"}, key=str):
        raise ValueError(f"unknown key {unknown[0]!r}: a plans file has default and plans")
    if "default" not in data:
        raise ValueError("the plans file has no default: name the plan a workspace is on")
    table = data.get("plans")
    if not isinstance(table, dict) or not table:
        raise ValueError("plans is a mapping of each plan's name to its label and caps")
    plans = {}
    for name, fields in table.items():
        if not isinstance(fields, dict):
            raise ValueError(f"plans.{name}: a plan is a mapping with a label and its caps")
        if unknown := sorted(set(fields) - set(FIELDS), key=str):
            raise ValueError(
                f"plans.{name}: unknown key {unknown[0]!r}; a plan may have {', '.join(FIELDS)}"
            )
        if "label" not in fields:
            raise ValueError(f"plans.{name}: no label")
        try:
            plans[name] = Plan(**fields)
        except ValueError as error:
            raise ValueError(f"plans.{name}: {error}") from None
        if plans[name].storage_bytes is not None and plans[name].notes_per_month is None:
            raise ValueError(
                f"plans.{name}: storage_bytes needs notes_per_month too: each note a "
                "workspace holds takes some space whatever its size, and only a cap on notes "
                "a month bounds how fast that grows"
            )
    return Plans(plans, str(data["default"]))
