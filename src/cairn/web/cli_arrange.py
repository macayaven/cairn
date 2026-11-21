"""The model that arranges pages, on the command line (T4b), and tidying with no model (T4f):
`serve [--model-env PREFIX] [--tidy | --no-tidy] [--model-price IN,OUT] [--model-deadline
SECONDS]` and `arrange --db PATH --account ID [--project NAME] [--plans FILE] (--model-env
PREFIX | --tidy) [--model-price IN,OUT]`.

The model comes from <PREFIX>_BASE_URL, <PREFIX>_API_KEY (optional) and <PREFIX>_MODEL in
this process's environment (cairn.llm.from_env), never from arguments, so its key lands in no
process list, shell history or output. <PREFIX>_REQUEST_EXTRA (optional) is a JSON object of
fields sent with every request, for what the server needs in its own words: a lane that
reasons aloud before it answers runs into the answer bound, so it is told not to there
({"chat_template_kwargs": {"enable_thinking": false}} on llama.cpp and vLLM). It cannot set
what cairn sets (the model, the messages, the token bounds, ...). <PREFIX>_PROVIDER says
which format the endpoint speaks: "openai" (the default, unset or blank), an
OpenAI-compatible endpoint as above, or "anthropic", Anthropic's Messages API
(cairn.llm.AnthropicMessages): <PREFIX>_BASE_URL is then optional (Anthropic's own API by
default, which needs <PREFIX>_API_KEY), thinking is switched off unless
<PREFIX>_REQUEST_EXTRA says otherwise, and a field given as null there is left out of the
request ({"thinking": null} for an endpoint that does not know it). A missing or malformed
setting exits 2 naming the variable, never its value.
<PREFIX>_SOURCE_NOTES (M2, frozen by tests/acceptance/test_m2_withheld_notes.py) says whether
pages that hold notes read from a withheld source (every kind the server reads, since review
finding Privacy 4: cairn.sources.withheld_via), or notes whose source_ref names a source
(M3: a meeting, a mail thread, a calendar, a document or a to-do list,
cairn.sources.withheld_by_ref), go to the model: "withhold", the default, also unset or
blank, keeps them from it and has the worker tidy them instead (cairn.arrange); "send" sends
every page, as before. White space around the word is dropped and the word is matched exactly,
in lower case ("SEND" is refused). Anything else makes `serve` and `arrange` exit 2 before
anything is opened, with one line that names the variable and both words and never the value
given (sends_source_notes); `serve` reads it at the top of its run, before its store, beside
`refused`. It is read with the model's settings and only then: without --model-env (`serve`,
`serve --tidy`, `serve --no-tidy`, `arrange --tidy`) nothing reads it. When `serve` starts
with a model it prints one line on stderr (serve_says): the model's name, the prefix, and
whether those pages are withheld or sent, with the setting's value, so an operator sees the
state without reading the environment.
<PREFIX>_SLACK_NOTES (sends_slack_notes), in the same two words, read by the same rule at the
same moments and refused the same way, says whether pages that hold a note that came through
Slack (`via` "slack", cairn.sources.HELD_VIA) go to the model: "withhold", the default,
keeps them from it and has the worker tidy them; "send" sends them, except in a project its
people keep from every model. It is the only choice that sends them: <PREFIX>_SOURCE_NOTES=send,
which was chosen before Slack existed, does not, and the start-up line is unchanged by it.
<PREFIX>_UNDECIDED_PROJECTS (opts_in; Privacy 4, tests/unit/test_arrange_undecided_projects_unit.py)
says whether projects opt in: "send", the default, also unset or blank, sends the pages of every
project not kept from every model, as before; "withhold" sends only the pages of projects whose
people set Models to allowed themselves (cairn.arrange `opt_in`), and a page of any other is
"undecided" and tidied without the model. Read by the same rule at the same moments, with the
model's settings only, and refused the same way (the variable and both words, never the value).
With "withhold" the start-up line says so too. It sends nothing the other two hold back.
`--model-price IN,OUT` is the model's
price in US dollars per million tokens in and out ("0.15,0.6"); without it the tokens are
recorded at no cost, so a plan's model budget is never spent.

`serve --model-env` runs the worker (cairn.arrange.Arranger) in the background, a pass every
ARRANGE_EVERY seconds, through a view of each workspace limited by its plan when `--plans`
is given (worker_views), in place of the worker without a model that `serve` runs by default
(below), with --no-tidy or without it; with --no-tidy and without --model-env there is no
worker and no arranging code is loaded.
`--model-price` without `--model-env` exits 2, with --no-tidy or without it. Arranging has
its own deadline, `--model-deadline SECONDS` (MODEL_DEADLINE, 300, by default), not the
compiler's 900: one call, every retry and backoff included, ends then ("failed"), is paid at
its reservation and saves nothing. The worker does not queue a failed page again: its next
note does, or a run of `arrange`. The default covers the largest page on slow hardware: when
a model still answered in claim ids, a local 27B model took 0.78 s a claim, 240 s for 300
claims (measured 2026-11-12), and a deadline of 60 s failed every page over some 78 claims there.
Answers in claim numbers are several times shorter, so the default now has room to spare. The
same measurement gives the context a model needs: the largest request (64,000 bytes, some
29,000 tokens) with its whole answer bound (3,632) wants about 33,000 tokens of context. The
worker arranges up to cairn.arrange.WORKSPACES_AT_ONCE workspaces at once.

`arrange` makes one pass over a workspace's pages (or one project's), through the same worker,
and prints one line per page: "<project>/<slug>: <status>[, version <a>[ -> <b>]][, tokens
<in>/<out>]", never claim text. "version <a>" is the version read, there only when the page
was read: a page that is "gone", every page of a workspace whose budget was spent before its
page was read ("no budget"), and a page whose arranging raised ("failed") print the status
alone. "-> <b>" is the version saved when it differs; the tokens are there when the model
was asked, "?" for a count it did not report and "tokens not reported" when it reported
neither (a call that failed). A page withheld from the model (<PREFIX>_SOURCE_NOTES, above)
prints "<project>/<slug>: withheld, version <a>[ -> <b>]": the version judged and the one its
tidy saved, never tokens. A page of a project its people keep from every model (M4), whatever
the setting, prints "<project>/<slug>: kept from models, version <a>[ -> <b>]" the same way,
and with <PREFIX>_UNDECIDED_PROJECTS=withhold a page of a project nobody set to allowed prints
"<project>/<slug>: undecided, version <a>[ -> <b>]".
With `--plans
FILE` it holds the workspace to its plan in force, as the worker of `serve --plans` does
(worker_views); `--plans` is required, with --tidy too, and the command says so, when any
workspace in the database has a plan the operator set, so a hosted workspace's limits (its
model budget, and how many versions of a page it keeps) are never passed by running the
command without them. The pass runs
into the worker's pause (cairn.arrange: five answers in a row that could not be used) like
any pass: it asks about no further page, prints the pages it asked about as before, then one
line on stderr, "arrange stopped: <n> answers in a row from '<model>' could not be used, so
<k> pages were not asked about" (k may be 0 when the pause began on the last page), and exits
1. `serve`'s worker gets the pause's defaults. Exit 1 for a missing database, an unknown
workspace or project, a page that failed, or the pause; 2 for neither or both of --model-env
and --tidy (refused by the parser before anything is read), --tidy with --model-price or
--model-deadline, a model that is not configured, a plans file it cannot use, or --plans
missing. Run beside `serve` on
one database, each call reserves what it may cost first (cairn.store reservations), so the
two never spend the same budget.

`--tidy` is the worker without a model (cairn.arrange.tidy_page, Arranger(None, ...)):
`serve` runs it in the background by default (TIDY_BY_DEFAULT, decision 76), a pass every
ARRANGE_EVERY seconds, as `serve --tidy` does; `serve --no-tidy` runs no worker (the parser
refuses --tidy with --no-tidy), and `serve --model-env` runs the model's in its place.
`serve --model-deadline` without --model-env is accepted and changes nothing, as before the
default. `arrange --tidy` makes one pass. It keeps each heading's claims together and folds
the claims that repeat, and needs no model, key or budget: nothing is sent, reserved or spent. Every
queued page is tidied, however few its claims, and a pass never pauses. A page saved this
way is recorded as arranged by cairn.arrange.TIDY ("cairn"). `arrange --tidy` spends
nothing and needs --plans where `arrange --model-env` does: each save is held to the
workspace's plan in force as the worker of `serve --plans` holds it. A tidied page prints
"<project>/<slug>: arranged, version <a> -> <b>" and a page already tidy "<project>/<slug>:
unchanged, version <a>", never with tokens. --tidy with --model-env, --model-price or
--model-deadline exits 2 with one line before anything is opened, "--tidy uses no model, so
it takes no <option>" (on `arrange`, --tidy with --model-env is the parser's refusal, as is
neither).

Nothing here imports cairn.arrange or cairn.llm until --model-env, --model-price, --tidy or
serve's default asks for them (cairn.arrange imports cairn.llm; a tidy builds no model):
`serve --no-tidy` without --model-env or --model-price imports neither.
"""

import argparse
import asyncio
import math
import os
import sys
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cairn.plans import Plans
from cairn.plans import load as load_plans
from cairn.store import Store
from cairn.web.cli_data import DB_EXISTS

if TYPE_CHECKING:
    from cairn.arrange import Arranger, Price

# Whether `serve` tidies pages when no option says (decision 76): True, so every install
# does, and --no-tidy turns it off. False puts serve back as it was: --tidy turns it on.
TIDY_BY_DEFAULT = True
ARRANGE_EVERY = 1.0  # seconds between the background worker's passes
# Seconds one arranging call may take, retries and backoff included. A call cut off here is
# paid at its reservation and saves nothing, so the default covers the largest page cairn
# sends on slow hardware: 300 claims took 240 s on a local 27B model when the answer was in
# claim ids (measured 2026-11-12); an answer in claim numbers is several times shorter.
MODEL_DEADLINE = 300.0
SOURCE_NOTES = "SOURCE_NOTES"  # <PREFIX>_SOURCE_NOTES: "withhold" (the default) or "send"
SLACK_NOTES = "SLACK_NOTES"  # <PREFIX>_SLACK_NOTES: "withhold" (the default) or "send"
# <PREFIX>_UNDECIDED_PROJECTS: "send" (the default) or "withhold" (projects opt in: Privacy 4)
UNDECIDED_PROJECTS = "UNDECIDED_PROJECTS"
_model_options: dict[str, Any] = {}  # extra options for the model (a test gives a transport)


def deadline(text: str) -> float:
    """Seconds, a finite number above 0."""
    try:
        seconds = float(text)
    except ValueError:
        seconds = math.nan
    if not (math.isfinite(seconds) and seconds > 0):
        raise argparse.ArgumentTypeError("give the deadline in seconds, a number above 0")
    return seconds


def price(text: str) -> "Price":
    """IN,OUT in US dollars per million tokens, as a cairn.arrange.Price in millionths."""
    from cairn.arrange import Price

    parts = text.split(",")
    try:
        dollars = [Decimal(part.strip()) for part in parts]
    except InvalidOperation:
        dollars = []
    if len(parts) != 2 or len(dollars) != 2 or not all(d.is_finite() and d >= 0 for d in dollars):
        raise argparse.ArgumentTypeError(
            "give the price as IN,OUT in US dollars per million tokens, such as 0.15,0.6"
        )
    micros = [math.ceil(d * 1_000_000) for d in dollars]
    return Price(input_per_million=micros[0], output_per_million=micros[1])


TIDY_HELP = (
    "cairn arranges pages itself, with no model, key or budget: it keeps each heading's claims"
    " together and folds claims that repeat; it takes none of the --model-* options"
)
NO_TIDY_HELP = (
    "cairn does not tidy pages: no worker runs, unless --model-env names a model, and no"
    " arranging code is loaded"
    + ("; serve tidies pages unless this is given" if TIDY_BY_DEFAULT else "; the default")
)


def add_options(serve: argparse.ArgumentParser, *, required: bool = False) -> None:
    """--model-env, --tidy, --model-price and --model-deadline, on `serve` (where neither
    --model-env nor --tidy is needed, `refused` turns down the two together, and --tidy and
    --no-tidy are a group the parser refuses together) and on `arrange` (where exactly one
    of --model-env and --tidy is `required`, a group the usage line shows; no --no-tidy)."""
    which = serve.add_mutually_exclusive_group(required=True) if required else serve
    tidy = which if required else serve.add_mutually_exclusive_group()
    which.add_argument(
        "--model-env",
        metavar="PREFIX",
        help="a model arranges pages: it reads PREFIX_BASE_URL, PREFIX_API_KEY, PREFIX_MODEL"
        " and the optional PREFIX_REQUEST_EXTRA from the environment, and PREFIX_PROVIDER:"
        " openai (the default, an OpenAI-compatible endpoint) or anthropic (Anthropic's"
        " Messages API, where PREFIX_BASE_URL is optional). PREFIX_SOURCE_NOTES is withhold"
        " (the default, also unset or blank: a page that holds a note any source the server"
        " reads brought, a connector's included, or a note whose source_ref says it came from"
        " a meeting, a"
        " mail thread, a calendar, a document or a to-do list, is not sent to the model, and"
        " is tidied without it) or send (such pages are sent too, except in a project its"
        " people keep from every model);"
        " the two words in lower case. PREFIX_SLACK_NOTES, in the same two words, does the"
        " same for a page that holds a note that came through Slack, which"
        " PREFIX_SOURCE_NOTES=send does not send. PREFIX_UNDECIDED_PROJECTS is send (the"
        " default, also unset or blank) or withhold: only the pages of projects whose people"
        " set Models to allowed themselves are sent to the model",
    )
    tidy.add_argument("--tidy", action="store_true", help=TIDY_HELP)
    if not required:
        tidy.add_argument("--no-tidy", action="store_true", help=NO_TIDY_HELP)
    serve.add_argument(
        "--model-price",
        type=price,
        metavar="IN,OUT",
        help="the model's price in US dollars per million tokens in and out, for example"
        " 0.15,0.6; without it nothing is charged to a budget (the tokens are still recorded)",
    )
    serve.add_argument(
        "--model-deadline",
        type=deadline,
        metavar="SECONDS",
        help=f"how long one call may take, retries included (default {MODEL_DEADLINE:g})",
    )


def add_commands(commands: Any) -> None:
    arrange = commands.add_parser(
        "arrange",
        help="one pass over a workspace of the model that arranges pages, or of --tidy",
        allow_abbrev=False,
    )
    arrange.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    arrange.add_argument("--account", required=True, help="the workspace's id")
    arrange.add_argument("--project", help="only this project's pages (a name or an id)")
    arrange.add_argument(
        "--plans",
        type=Path,
        metavar="FILE",
        help="hold the workspace to its plan in force, as `serve --plans` does (required when"
        " any workspace has a plan the operator set)",
    )
    add_options(arrange, required=True)


def worker_views(store: Store, plans: Plans | None) -> Callable[[str], Store]:
    """The view the worker acts through for a workspace: no person, limited by the
    workspace's plan in force now when there are plans (`Plans.in_force`: the operator's,
    else the default), as a request's view is.
    KeyError: the workspace is gone."""

    def view_for(account_id: str) -> Store:
        view = store.for_account(account_id)
        if plans is not None:
            plan = plans.in_force(view, account_id)[1]  # and each write reads it again
            view = plans.limit(view, plan)
        return view

    return view_for


def _tokens(prompt: int | None, completion: int | None) -> str:
    """A page line's token counts: "<in>/<out>", "?" for one the model did not report, and
    "not reported" when it reported neither."""
    if prompt is None and completion is None:
        return "not reported"
    return f"{'?' if prompt is None else prompt}/{'?' if completion is None else completion}"


def _model(prefix: str, seconds: float | None = None) -> Any:
    """The model from the environment, each call bounded by `seconds` (MODEL_DEADLINE when
    None: the option not given). LLMConfigError names the setting, never its value."""
    from cairn.llm import from_env

    seconds = MODEL_DEADLINE if seconds is None else seconds
    return from_env(prefix=prefix, **{"deadline": seconds, **_model_options})


def sends_source_notes(prefix: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether <PREFIX>_SOURCE_NOTES says "send": False for "withhold", unset or blank. White
    space around the word is dropped; the word is matched exactly, in lower case. ValueError
    for anything else, naming the variable and both words, never the value given."""
    return _sends(f"{prefix}_{SOURCE_NOTES}", environ)


def sends_slack_notes(prefix: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether <PREFIX>_SLACK_NOTES says "send", read by sends_source_notes' rule: the one
    choice that sends a page holding a note that came through Slack (cairn.sources.HELD_VIA).
    <PREFIX>_SOURCE_NOTES=send does not."""
    return _sends(f"{prefix}_{SLACK_NOTES}", environ)


def opts_in(prefix: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether <PREFIX>_UNDECIDED_PROJECTS says "withhold": only the pages of projects whose
    people chose "allowed" themselves go to the model (cairn.arrange: `opt_in`). False for
    "send", the default, unset or blank: today's behaviour. Read by sends_source_notes' rule,
    and refused the same way, naming the variable and both words, never the value given."""
    name = f"{prefix}_{UNDECIDED_PROJECTS}"
    env = os.environ if environ is None else environ
    value = env.get(name, "").strip()
    if value in ("", "send"):
        return False
    if value == "withhold":
        return True
    raise ValueError(f"{name} must be send (the default) or withhold, in lower case")


def _sends(name: str, environ: Mapping[str, str] | None) -> bool:
    env = os.environ if environ is None else environ
    value = env.get(name, "").strip()
    if value in ("", "withhold"):
        return False
    if value == "send":
        return True
    raise ValueError(f"{name} must be withhold (the default) or send, in lower case")


def serve_says(
    args: argparse.Namespace, arranger: "Arranger | None", send: bool, opt_in: bool = False
) -> str | None:
    """The line `serve` prints as it starts with a model: the model, and whether pages that
    hold notes any source the server reads brought, or notes whose source_ref says they
    came from a meeting, a mail thread, a calendar, a document or a to-do list, are sent to
    it (with send, except in a project its people keep from every model); with `opt_in`,
    that only projects whose people set Models to allowed are sent. None without a model."""
    if arranger is None or arranger.model is None:
        return None
    name = f"{args.model_env}_{SOURCE_NOTES}"
    if send:
        how = (
            "are sent to it too, except in a project its people keep from every model"
            f" ({name}=send)"
        )
    else:
        how = f"are withheld from it and tidied without it ({name}=withhold)"
    said = (
        f"arranging pages with model {arranger.model.name!r} ({args.model_env}_*): pages that"
        " hold notes any source the server reads brought, a connector's included, or notes"
        " whose source_ref says"
        f" they came from a meeting, a mail thread, a calendar, a document or a to-do list, {how}"
    )
    if opt_in:
        said += (
            "; only projects whose people set Models to allowed are sent"
            f" ({args.model_env}_{UNDECIDED_PROJECTS}=withhold)"
        )
    return said


def refused(args: argparse.Namespace) -> str | None:
    """The one line `serve` and `arrange` exit 2 with when --tidy comes with an option for a
    model; None otherwise. Read before anything is opened or built."""
    if not args.tidy:
        return None
    given = {
        "--model-env": args.model_env is not None,
        "--model-price": args.model_price is not None,
        "--model-deadline": args.model_deadline is not None,
    }
    for option, there in given.items():
        if there:
            return f"--tidy uses no model, so it takes no {option}"
    return None


def serve_arranger(
    args: argparse.Namespace,
    store: Store,
    plans: Plans | None,
    *,
    send_source_notes: bool = False,
    send_slack_notes: bool = False,
    opt_in: bool = False,
) -> "Arranger | None":
    """The background worker `serve` runs: with --model-env, the model's (`refused` has turned
    down --tidy with it, and --no-tidy changes nothing), withholding the withheld kinds'
    notes (every kind's) and the notes whose source_ref names a source unless
    `send_source_notes` (sends_source_notes, read by `serve` before anything is opened), and
    the notes that came through Slack unless `send_slack_notes` (sends_slack_notes), and with
    `opt_in` (opts_in) sending only projects whose people chose "allowed"; else, with --tidy or by
    default (TIDY_BY_DEFAULT, read here), the worker without a model; with --no-tidy, None,
    and nothing of cairn.arrange is imported. ValueError (a cairn.llm.LLMConfigError among
    them) says what is wrong, naming settings only."""
    if args.model_env is None:
        if args.model_price is not None:
            raise ValueError("--model-price needs --model-env, the model it prices")
        if not (args.tidy or (TIDY_BY_DEFAULT and not args.no_tidy)):
            return None
        from cairn.arrange import Arranger

        return Arranger(None, price=None, view_for=worker_views(store, plans), every=ARRANGE_EVERY)
    from cairn.arrange import Arranger

    return Arranger(
        _model(args.model_env, args.model_deadline),
        price=args.model_price,
        view_for=worker_views(store, plans),
        every=ARRANGE_EVERY,
        withhold=() if send_source_notes else None,
        held_via=() if send_slack_notes else None,
        opt_in=opt_in,
    )


NEEDS_PLANS = (
    "arrange needs --plans FILE here: a workspace in this database has a plan the operator"
    " set, and its plan in force limits what a pass may spend and keep"
)


def _any_plan(store: Store) -> bool:
    """Whether any workspace has a plan the operator set."""
    return any(account.plan is not None for account in store.list_accounts())


def run(args: argparse.Namespace) -> int:
    """`arrange`: one pass over the workspace's pages, one line each. The parser has made
    sure of exactly one of --model-env and --tidy."""
    if (said := refused(args)) is not None:
        print(said, file=sys.stderr)
        return 2
    from cairn.arrange import Arranger
    from cairn.llm import LLMConfigError

    model = None  # --tidy: the worker without a model
    send = slack = opt_in = False  # read with the model's settings, and only then
    if not args.tidy:
        try:
            send = sends_source_notes(args.model_env)
            slack = sends_slack_notes(args.model_env)
            opt_in = opts_in(args.model_env)
        except ValueError as error:  # names the setting, never its value
            print(f"cannot use that model: {error}", file=sys.stderr)
            return 2
        try:
            model = _model(args.model_env, args.model_deadline)
        except LLMConfigError as error:
            print(f"cannot use that model: {error}", file=sys.stderr)
            return 2
    plans = None
    if args.plans is not None:
        try:
            plans = load_plans(args.plans)
        except (OSError, ValueError) as error:
            said = error.strerror if isinstance(error, OSError) else error
            print(f"cannot use those plans: {said}", file=sys.stderr)
            return 2
    if not args.db.is_file():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1
    store = Store(args.db, upgrade=False)
    try:
        if plans is None and _any_plan(store):  # a tidy spends nothing, and still saves
            print(NEEDS_PLANS, file=sys.stderr)
            return 2
        try:
            view = store.for_account(args.account)
        except KeyError:
            print(f"no workspace {args.account!r}", file=sys.stderr)
            return 1
        projects = view.list_projects()
        if args.project is not None:
            found = view.get_project(args.project)
            if found is None:
                print(f"no project {args.project!r}", file=sys.stderr)
                return 1
            projects = [found]
        names = {project.id: project.name for project in projects}
        pages = [
            (project.id, page.slug) for project in projects for page in view.list_pages(project.id)
        ]
        arranger = Arranger(
            model,
            price=args.model_price,
            view_for=worker_views(store, plans),
            capacity=max(1, len(pages)),
            withhold=() if send else None,
            held_via=() if slack else None,
            opt_in=opt_in,
        )
        for project_id, slug in pages:
            arranger.queue(view.account_id, project_id, slug)
        done = asyncio.run(arranger.run_once(limit=len(pages))) if pages else []
        paused = bool(pages) and arranger.paused()
    finally:
        store.close()
    for page in done:
        said = f"{names.get(page.project_id, page.project_id)}/{page.slug}: {page.status}"
        if page.previous is not None:
            said += f", version {page.previous}"
            if page.version is not None and page.version != page.previous:
                said += f" -> {page.version}"
        if page.draft is not None:
            said += f", tokens {_tokens(page.draft.prompt_tokens, page.draft.completion_tokens)}"
        print(said)
    if paused:  # the pass ran into the worker's pause and stopped there
        print(
            f"arrange stopped: {arranger.unusable} answers in a row from {model.name!r} could"
            f" not be used, so {len(pages) - len(done)} pages were not asked about",
            file=sys.stderr,
        )
        return 1
    return 1 if any(page.status == "failed" for page in done) else 0
