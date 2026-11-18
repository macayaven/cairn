"""The pages anyone can read: the guides (/docs, /docs/<name>) and the landing page (GET /
without a session, when people sign in through a provider and each gets a workspace of their
own).

Auth lets /docs and /docs/<name> through without a session and admits one when the request
carries it, so a signed-in reader keeps the app's navigation (`viewer`). The landing page is
Auth's to serve, through `Public.landing`, since no route runs without an account.

The guides are Markdown shipped inside the package (cairn/guides/<name>.md), one copy each,
rendered by web/markdown.guide. A guide's name is looked up in GUIDES, never joined into a
path.

Under a brand (B3; the contract is frozen by tests/acceptance/test_b3_brand_inside.py):
- The guides say the brand's name. A guide never spells the product's name: it writes a
  marker (web/markdown.py: `{{name}}`, `{{slug}}`, `{{SLUG}}`) that the renderer fills from
  the product of the request's brand (brand.product_of), and so do the summaries the index
  lists and the sentence above the operator's own notes. The software's identifiers in the
  guides' commands are written as they are. Each guide is rendered once per process and
  product (`read_guide`), so an app under a brand and one without share nothing they say.
- The operator's guide (/docs/run) documents the software by its own name for whoever runs
  it, who has the repository's copy. Under a brand it is not one of the guides
  (`shown_guides`): /docs/run answers as the address of a guide that does not exist, the
  index does not list it, and no guide shown links it (a unit test reads every guide's
  links).
- The operator's own notes (`serve --operator-notes`) are theirs: shown as written, never
  filled, whatever they say.
"""

import functools
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from cairn.memory import Memory, NotFound
from cairn.web import markdown
from cairn.web.auth import MEMORY
from cairn.web.brand import Brand, Product, product_of
from cairn.web.templating import _page, brand_of

GUIDES = ("connect", "patterns", "sources", "security", "privacy", "run")
OPERATORS_GUIDE = "run"  # the operator's guide: not one of the guides under a brand
BRANDED_GUIDES = tuple(name for name in GUIDES if name != OPERATORS_GUIDE)
# What the index says of each guide; its markers are filled as a guide's are.
SUMMARIES = {
    "connect": "Connect Claude Code, Codex, the Claude apps, ChatGPT, Cursor, VS Code, Gemini CLI"
    " or a script, by URL or with an API key.",
    "patterns": "Lay out projects and connections: many agents with one project, many projects, and"
    " sets of projects per use.",
    "sources": "Sources: meetings, mail, calendar, documents and to-dos",
    "security": "Who can reach what, how sign-in and agent connections work, what the checks"
    " catch and what they do not.",
    "privacy": "What {{name}} stores and where, what it never stores, and how export, deletion"
    " and backups behave.",
    "run": "Run cairn on your own machine in one container: a key, signing in, where the data"
    " lives, backups and upgrades.",
}


def shown_guides(brand: Brand | None) -> tuple[str, ...]:
    """The guides an app shows: GUIDES, or BRANDED_GUIDES under a brand."""
    return GUIDES if brand is None else BRANDED_GUIDES


def _branded(brand: Brand | None) -> Product | None:
    """What a guide is rendered for: the brand's product, or None without a brand."""
    return None if brand is None else product_of(brand)


@functools.cache
def read_guide(name: str, product: Product | None = None) -> markdown.Guide:
    """The guide `name` (one of GUIDES), rendered once per process and product. `product`:
    a brand's (brand.product_of), whose name the guide says and whose guides its links
    lead to (never the operator's); None without a brand."""
    text = (files("cairn") / "guides" / f"{name}.md").read_text(encoding="utf-8")
    return markdown.guide(text, GUIDES if product is None else BRANDED_GUIDES, product)


# serve --operator-notes FILE: the operator's own words, the last section of /docs/privacy.
OPERATOR_NOTES_MAX = 64 * 1024  # bytes of the file
_FILL_IN = "## For the operator to fill in\n"
_FROM_OPERATOR = (
    "## From the operator\n\n"
    "The operator of this install wrote this section. {{name}} shows it as written and does"
    " not check it.\n"
)


def read_operator_notes(path: Path) -> str:
    """The text of `serve --operator-notes FILE`. ValueError, so serve does not start: a file
    that cannot be read, is over OPERATOR_NOTES_MAX bytes, or is not UTF-8 text."""
    try:
        with Path(path).open("rb") as file:
            data = file.read(OPERATOR_NOTES_MAX + 1)
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error.strerror}") from None
    if len(data) > OPERATOR_NOTES_MAX:
        raise ValueError(f"{path} is over 64 KiB: the notes are one section of a page")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path} is not UTF-8 text") from None


def privacy_guide(notes: str, product: Product | None = None) -> markdown.Guide:
    """The privacy guide with the operator's `notes` as its last section, in place of the list
    for the operator to fill in, as read_guide renders it for `product`. The notes go
    through the claims' renderer (markdown.render): raw HTML is text, no heading is a
    heading, only web and mail links are links, and no marker is filled: they are the
    operator's words, as written."""
    text = (files("cairn") / "guides" / "privacy.md").read_text(encoding="utf-8")
    kept, found, _ = text.partition(_FILL_IN)
    assert found, "the privacy guide ends with the section for the operator"
    known = GUIDES if product is None else BRANDED_GUIDES
    shown = markdown.guide(kept + _FROM_OPERATOR, known, product)
    return markdown.Guide(shown.title, shown.sections, shown.html + markdown.render(notes))


def viewer(request: Request) -> Memory | None:
    """The signed-in reader of a public page, when Auth admitted one."""
    memory = request.scope.get(MEMORY)
    return memory if isinstance(memory, Memory) else None


def _either(labels: Sequence[str]) -> str:
    """ "GitHub", "GitHub or Google", "GitHub, Google or Acme SSO"."""
    if len(labels) < 2:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} or {labels[-1]}"


class Public:
    """The public pages of one app. `mcp_url`: where agents connect. `providers`: the sign-in
    providers' labels, for the landing page. `operator_notes`: the last section of
    /docs/privacy (privacy_guide). `test_instance` (`serve --test-instance`): the landing page
    says the install is a private test instance for approved testers and leads with the run
    guide, and the landing page's and the guides' header links to /login as "Tester
    sign-in"; a signed-in reader's pages do not change."""

    def __init__(
        self,
        mcp_url: str,
        *,
        providers: Sequence[str] = (),
        operator_notes: str | None = None,
        test_instance: bool = False,
    ) -> None:
        self.test_instance = test_instance
        self.mcp_url = mcp_url
        self.notes = operator_notes
        # The privacy guide with the notes, by product (None: no brand), each made once.
        self.privacy: dict[Product | None, markdown.Guide] = {}
        if operator_notes is not None:
            self.privacy[None] = privacy_guide(operator_notes)
        self.providers = _either(providers)

    def landing(self) -> Response:
        # Never under a brand: Auth has no landing page then (it names the software).
        return _page(
            "landing.html",
            brand=None,
            mcp_url=self.mcp_url,
            providers=self.providers,
            test_instance=self.test_instance,
        )

    def _reader(self, request: Request) -> dict[str, object]:
        memory = viewer(request)
        return {
            "brand": brand_of(request),
            "person": memory.person if memory else None,
            "signed_in": memory is not None,
            "test_instance": self.test_instance,
        }

    def routes(self) -> list[Route]:
        def index(request: Request) -> Response:
            brand = brand_of(request)
            branded, says = _branded(brand), product_of(brand)
            listed = [
                (name, read_guide(name, branded).title, markdown.fill(SUMMARIES[name], says))
                for name in shown_guides(brand)
            ]
            return _page("docs.html", guides=listed, **self._reader(request))

        def one(request: Request) -> Response:
            name = request.path_params["name"]
            brand = brand_of(request)
            if name not in shown_guides(brand):
                raise NotFound("There is no guide at this address.")
            branded = _branded(brand)
            if name == "privacy" and self.notes is not None:
                if branded not in self.privacy:
                    self.privacy[branded] = privacy_guide(self.notes, branded)
                shown = self.privacy[branded]
            else:
                shown = read_guide(name, branded)
            return _page("guide.html", guide=shown, **self._reader(request))

        return [
            Route("/docs", index, methods=["GET"]),
            Route("/docs/{name}", one, methods=["GET"]),
        ]
