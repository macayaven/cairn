"""Unit tests for B3: under a brand, the signed-in product says the brand's name. They pin
what tests/acceptance/test_b3_brand_inside.py leaves open: the slug's rule, the one place
the name is said (the product a page, a guide and a sentence ask for), and the spelling
check that keeps a template or a guide from naming the software by accident."""

import re
from pathlib import Path

import pytest

from cairn.web.brand import SOFTWARE, Brand, Product, product_of, slug_of

TEMPLATES = Path(__file__).parents[2] / "src" / "cairn" / "templates"
# The software's identifiers: what a person types exactly, and the static files' addresses.
# Anywhere else, a template or a guide asks for the product's name or slug.
IDENTIFIERS = re.compile(
    r"python3? -m cairn(?:\.[a-z_]+)*\b"  # the module
    r"|(?<![A-Za-z0-9_])CAIRN_[A-Z0-9_]+"  # an environment variable
    r"|(?<![A-Za-z0-9_-])cairn\.(?:db|env)\b"  # a file name the software chooses
    r"|\bghcr\.io/[\w.-]+/cairn(?::[\w.-]+)?"  # the container image
    r"|(?<![A-Za-z0-9_-])cairn\.(?:at|rt)\."  # a token's prefix
    r"|/static/cairn\.(?:svg|css)\b"  # a static file's address (no brand: the frame's own)
)
# Never rendered under a brand: the landing page is Auth's only without one (B1).
NOT_UNDER_A_BRAND = {"landing.html"}


def spelled(text: str) -> list[str]:
    """Each place `text` names the software outside one of IDENTIFIERS, with what is
    around it."""
    kept = [m.span() for m in IDENTIFIERS.finditer(text)]
    return [
        text[max(0, m.start() - 40) : m.end() + 40]
        for m in re.finditer(SOFTWARE, text, re.IGNORECASE)
        if not any(s <= m.start() and m.end() <= e for s, e in kept)
    ]


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Knowledge Base", "knowledge-base"),
        ("  Knowledge   Base  ", "knowledge-base"),
        ("R&D *Field* _Notes_", "r-d-field-notes"),
        ("Juniper-Field 2", "juniper-field-2"),
        ("--Field--", "field"),
        ("ÉLAN Field", "lan-field"),  # only ASCII letters and digits stay
        ("***", "memory"),  # nothing left: the word the scope says under a brand
        ("Ünïcödé", "n-c-d"),
        ("日本", "memory"),
    ],
)
def test_the_slug_is_the_name_in_lower_case_with_dashes(name, slug):
    assert slug_of(name) == slug


def test_the_product_is_the_brands_name_and_slug_or_the_softwares():
    assert product_of(None) == Product(SOFTWARE, SOFTWARE)
    assert product_of(Brand("Knowledge Base")) == Product("Knowledge Base", "knowledge-base")
    assert product_of(Brand("R&D *Field* _Notes_")).name == "R&D *Field* _Notes_"  # as written


# The name is said in one place: no template spells it


@pytest.mark.parametrize(
    "template",
    sorted(p.name for p in TEMPLATES.glob("*.html") if p.name not in NOT_UNDER_A_BRAND),
)
def test_no_template_spells_the_softwares_name(template):
    """A template asks for `product.name` (or `product.slug` in text to type) wherever it
    names the product; the software's name appears only inside an identifier. Jinja's own
    comments are not sent and are not read."""
    source = (TEMPLATES / template).read_text(encoding="utf-8")
    source = re.sub(r"\{#.*?#\}", "", source, flags=re.DOTALL)
    assert not spelled(source), spelled(source)


# What the software records under its own name is shown as the product's


def test_a_tidy_by_the_software_is_shown_as_the_products():
    from cairn.arrange import TIDY
    from cairn.web.components import actor_kind, arranger

    assert TIDY == SOFTWARE  # recorded under the software's name: what arranger reads
    assert arranger(TIDY, "Knowledge Base") == "Knowledge Base"
    assert arranger("qwen3-8b", "Knowledge Base") == "qwen3-8b"  # a model, by its own name
    assert actor_kind("system", None, "Knowledge Base") == "Knowledge Base"
    assert actor_kind("system") == SOFTWARE  # without a brand, as before


# Sentences in Python are given the product's name


def test_the_guards_words_name_the_product_they_are_given():
    from cairn.guard import POLICIES, SAID, policy_words, rule_words

    for rule in SAID:
        assert not spelled(rule_words(rule, "Knowledge Base")), rule
    assert rule_words("cairn_credential", "Knowledge Base") == (
        "a Knowledge Base access or refresh token"
    )
    assert rule_words("house_rule", "Knowledge Base") == "house_rule"  # another guard's own
    for policy in POLICIES:
        assert not spelled(policy_words(policy, "Knowledge Base")), policy
    assert "a Knowledge Base access or refresh token" in policy_words("secrets", "Knowledge Base")
    assert rule_words("cairn_credential") == f"a {SOFTWARE} access or refresh token"


def test_what_switching_a_source_on_reads_names_the_product_it_is_given():
    from cairn.sources import KINDS

    for kind in KINDS.values():
        said = kind.switching_on("Knowledge Base")
        assert said is None or not spelled(said)
    assert "traces marked for Knowledge Base" in KINDS["langfuse"].switching_on("Knowledge Base")
    assert f"traces marked for {SOFTWARE}" in KINDS["langfuse"].switching_on()


def test_the_start_pages_refusal_names_the_product_it_is_given():
    from cairn.web.start import _Refused, _wanted

    with pytest.raises(_Refused) as refused:
        _wanted([], {}, "Knowledge Base")
    assert str(refused.value) == (
        "Tick at least one use, so Knowledge Base knows which projects to make."
    )


# The guides: markers the renderer fills in

ODD = Product("R&D *Field* _Notes_", "r-d-field-notes")
MARKED = """\
# Connect an agent to {{name}}

{{name}} speaks MCP at `https://YOUR-{{SLUG}}/mcp`; **{{name}}'s** own tools.

| Who | {{name}} |
| --- | --- |
| an agent | `{{slug}}` |

```
claude mcp add --transport http {{slug}} https://YOUR-{{SLUG}}/mcp
write one note to the {{name}} project
```
"""


def test_the_renderer_fills_each_marker_in_prose_code_headings_and_tables():
    from cairn.web.markdown import guide

    shown = guide(MARKED, (), ODD)
    assert shown.title == "Connect an agent to R&D *Field* _Notes_"  # as written, not Markdown
    html = str(shown.html)
    assert (
        "R&amp;D *Field* _Notes_ speaks MCP at <code>https://YOUR-R-D-FIELD-NOTES/mcp</code>"
        in html
    )
    assert "<strong>R&amp;D *Field* _Notes_'s</strong> own tools" in html
    assert "<th>R&amp;D *Field* _Notes_</th>" in html
    assert 'data-label="R&amp;D *Field* _Notes_"><code>r-d-field-notes</code>' in html
    assert "http r-d-field-notes https://YOUR-R-D-FIELD-NOTES/mcp\n" in html
    assert "to the R&amp;D *Field* _Notes_ project" in html
    assert "{{" not in html and "<em>" not in html


def test_without_a_brand_the_markers_are_the_softwares_name():
    from cairn.web.markdown import fill, guide

    plain = MARKED.replace("{{name}}", SOFTWARE).replace("{{slug}}", SOFTWARE)
    plain = plain.replace("{{SLUG}}", SOFTWARE.upper())
    assert fill(MARKED, product_of(None)) == plain
    assert guide(MARKED, ()) == guide(plain, ())  # every byte as with the name spelled out


GUIDES_DIR = TEMPLATES.parent / "guides"
OPERATORS = "run"  # the operator's guide: never shown under a brand, it names the software


@pytest.mark.parametrize(
    "name", sorted(p.stem for p in GUIDES_DIR.glob("*.md") if p.stem != OPERATORS)
)
def test_no_guide_shown_under_a_brand_spells_the_softwares_name(name):
    """A guide writes a marker wherever it names the product; the software's name appears
    only inside an identifier."""
    text = (GUIDES_DIR / f"{name}.md").read_text(encoding="utf-8")
    assert not spelled(text), spelled(text)
    assert not re.search(rf"\]\({OPERATORS}\.md", text)  # no way to the operator's guide


def test_what_the_public_pages_say_of_the_guides_spells_no_name():
    from cairn.web.public import _FROM_OPERATOR, SUMMARIES

    for name, summary in SUMMARIES.items():
        if name != OPERATORS:
            assert not spelled(summary), name
    assert not spelled(_FROM_OPERATOR)


def test_under_a_brand_the_operators_guide_is_not_one_of_the_guides():
    from cairn.web.public import GUIDES, shown_guides

    assert shown_guides(None) == GUIDES
    assert OPERATORS in GUIDES
    assert shown_guides(Brand("Knowledge Base")) == tuple(g for g in GUIDES if g != OPERATORS)


def test_a_marker_the_renderer_does_not_know_is_left_as_it_is():
    from cairn.web.markdown import guide

    assert "{{Name}} and {{ name }}" in str(guide("{{Name}} and {{ name }}\n", (), ODD).html)


# The MCP server is built with the product's name


def _said_to_agents(mcp) -> str:
    """Everything the server tells an agent before a call: its name, its instructions, and
    each tool's description and each parameter's."""
    import json

    tools = [(t.name, t.description, t.parameters) for t in mcp._tool_manager.list_tools()]
    return json.dumps([mcp.name, mcp.instructions, tools])


def test_under_a_brand_the_mcp_server_says_the_brands_name():
    from cairn.web.mcp import INSTRUCTIONS, build_mcp

    branded = build_mcp(Brand("Knowledge Base"))
    assert branded.name == "Knowledge Base"
    assert branded.instructions.startswith("Knowledge Base is shared memory for agents.")
    assert not spelled(_said_to_agents(branded))
    plain = build_mcp()
    assert plain.name == SOFTWARE and plain.instructions == INSTRUCTIONS
    assert INSTRUCTIONS.startswith(f"{SOFTWARE} is shared memory for agents.")
    # One server per app: building a branded one changes nothing of a plain one.
    assert _said_to_agents(build_mcp()) == _said_to_agents(plain)
    assert [t.name for t in branded._tool_manager.list_tools()] == [
        t.name for t in plain._tool_manager.list_tools()
    ]
