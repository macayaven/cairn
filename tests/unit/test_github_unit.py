"""Unit tests for the GitHub source: what tests/acceptance/test_c11_github.py does not pin.
HTTP goes through httpx.MockTransport; time is injected. Every token, name and address here
is made up."""

import pytest

from cairn.sources import KINDS
from cairn.sources import github as g

VAR = "GITHUB_UNIT_TOKEN"
REPO = "quillworks/quill"
TOKEN = "github" + "_pat_" + "11UNITMADEUP0" + "_" + "a1b2c3" * 6  # never a real token


def settings(**given) -> dict:
    return {"token_env": VAR, "repo": REPO, "projects": ["quill"], **given}


def refusal(config) -> str:
    with pytest.raises(ValueError) as refused:
        g.check_config(config)
    return str(refused.value)


# The config, setting by setting


def test_the_kind_is_registered_starts_off_and_proposes_the_project_github():
    kind = KINDS["github"]
    assert kind is g.KIND and kind.starts_off and kind.via == "github"
    assert kind.check is g.check_config and kind.open is g.open_source
    assert kind.default_project == "github" and kind.withheld is True


def test_the_defaults_are_filled_in_and_what_is_stored_checks_the_same():
    checked = g.check_config({"token_env": VAR, "repo": REPO})
    assert checked == {
        "token_env": VAR,
        "repo": REPO,
        "projects": [],
        "items": ["pulls", "issues", "releases"],
        "comments": True,
        "settle_minutes": 30,
        "lookback_days": 30,
        "backfill_days": 0,
        "base_url": "https://api.github.com",
    }
    assert g.check_config(checked) == checked


def test_items_are_kept_in_one_order_once_each():
    assert g.check_config(settings(items=["releases", "pulls", "releases"]))["items"] == [
        "pulls",
        "releases",
    ]


def test_base_url_loses_a_trailing_slash_and_keeps_its_path():
    given = "https://ghe.example.test/api/v3/"
    assert g.check_config(settings(base_url=given))["base_url"] == given.rstrip("/")
    assert g.check_config(settings(base_url="http://[::1]:8123"))["base_url"] == (
        "http://[::1]:8123"
    )


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"token_env": "github_token"}, "token_env names an environment variable"),
        ({"token_env": "A" * 129}, "token_env names an environment variable"),
        ({"repo": "quillworks/quill.GIT"}, "repo is owner/name"),
        ({"repo": "quillworks-/quill"}, "repo is owner/name"),
        ({"repo": "quill--works/quill"}, "repo is owner/name"),
        ({"repo": "quillworks/.."}, "repo is owner/name"),
        ({"repo": "quillworks/qu ill"}, "repo is owner/name"),
        ({"repo": "quillworks/quill?x=1"}, "repo is owner/name"),
        ({"projects": [""]}, "projects holds a project's name"),
        ({"projects": [7]}, "projects holds a project's name"),
        ({"projects": ["br_" + "x" * 8]}, "projects holds a project's name"),
        ({"items": [7]}, "items is a list"),
        ({"items": None}, "items is a list"),
        ({"comments": 1}, "comments is true or false"),
        ({"comments": None}, "comments is true or false"),
        ({"settle_minutes": 30.0}, "settle_minutes is a whole number from 0 to 1440"),
        ({"settle_minutes": None}, "settle_minutes is a whole number from 0 to 1440"),
        ({"lookback_days": False}, "lookback_days is a whole number from 1 to 365"),
        ({"lookback_days": "30"}, "lookback_days is a whole number from 1 to 365"),
        ({"backfill_days": -1}, "backfill_days is a whole number from 0 to 365"),
        ({"backfill_days": 366}, "backfill_days is a whole number from 0 to 365"),
        ({"backfill_days": 2.5}, "backfill_days is a whole number from 0 to 365"),
        ({"backfill_days": "7"}, "backfill_days is a whole number from 0 to 365"),
        ({"base_url": "https://ghe.example.test/api/v3?x=1"}, "base_url has no query"),
        ({"base_url": "https://ghe.example.test/api/v3#top"}, "base_url has no query"),
        ({"base_url": "//ghe.example.test/api/v3"}, "base_url is https"),
        ({"base_url": "https://"}, "base_url is https"),
        ({"base_url": 7}, "base_url is the address of GitHub's API"),
        ({"base_url": "https://ghe.example.test/" + "a" * 2048}, "base_url is the address"),
        ({"base_url": "https://[::1/api"}, "base_url is not a URL"),
        ({"base_url": "https://ada@ghe.example.test/api/v3"}, "base_url must not hold a user"),
    ],
)
def test_each_bad_value_is_refused_by_its_setting(given, said):
    assert said in refusal(settings(**given))


def test_a_setting_that_is_not_one_is_named_only_when_it_is_a_plain_word():
    assert "no setting 'colour'" in refusal(settings(colour="blue"))
    for odd in ("Colour", "a b", "x" * 41, TOKEN.lower()[:40]):
        said = refusal(settings(**{odd: 1}))
        assert odd not in said and "does not take one of the settings given" in said


def test_a_missing_token_env_or_repo_says_what_it_is():
    assert "needs token_env" in refusal({"repo": REPO})
    assert "needs repo: owner/name" in refusal({"token_env": VAR})


@pytest.mark.parametrize(
    "prefix", ["gh" + "p_", "gh" + "o_", "gh" + "u_", "gh" + "s_", "gh" + "r_", "github" + "_pat_"]
)
def test_a_value_shaped_like_a_github_token_is_refused_wherever_it_is_and_never_repeated(prefix):
    token = prefix + "Unit" + "Ab12" * 6
    for setting, config in (
        ("token_env", settings(token_env=token)),
        ("token_env", settings(token_env=token.upper())),
        ("repo", settings(repo=f"quillworks/{token}")),
        ("projects", settings(projects=[token[:40]])),
        ("base_url", settings(base_url=f"https://ghe.example.test/{token}")),
        ("base_url", settings(base_url=f"https://{token.lower()[:60]}.example.test")),
    ):
        said = refusal(config)
        assert setting in said and token not in said and token.upper() not in said
        assert token.lower()[:60] not in said


def test_a_short_word_that_starts_like_a_token_is_still_a_name():
    """Only a prefix followed by twenty or more letters, digits or "_" looks like a token: a
    repository may be called ghp_tools."""
    assert g.check_config(settings(repo="quillworks/ghp_tools"))["repo"] == "quillworks/ghp_tools"
    assert g.check_config(settings(token_env="GHP_TOKEN"))["token_env"] == "GHP_TOKEN"


def test_describe_says_what_is_read_from_which_repository_and_when():
    said = g.describe(g.check_config(settings()))
    assert said == (
        "Pull requests merged, issues closed and releases published in quillworks/quill, at"
        " https://api.github.com, with their conversations; each is read once, 30 minutes"
        " after it is finished"
    )
    one = g.describe(g.check_config(settings(items=["releases"], settle_minutes=1)))
    assert one == (
        "Releases published in quillworks/quill, at https://api.github.com; each is read once,"
        " 1 minute after it is finished"
    )
    quiet = g.describe(g.check_config(settings(items=["issues"], comments=False, settle_minutes=0)))
    assert quiet == (
        "Issues closed in quillworks/quill, at https://api.github.com, without their"
        " conversations; each is read once, as soon as it is finished"
    )
    assert VAR not in said


def test_a_backfill_is_said_by_describe_and_by_the_switch_on_sentence():
    """K2: with backfill_days, both say what finished in those days before the switch-on."""
    config = g.check_config(settings(items=["pulls"], backfill_days=30))
    assert g.describe(config) == (
        "Pull requests merged in quillworks/quill, at https://api.github.com, with their"
        " conversations; each is read once, 30 minutes after it is finished, and so is what"
        " finished in the 30 days before it is switched on"
    )
    assert KINDS["github"].switching_on("Field Notes", config) == (
        "Switching it on reads what is finished from then on and what finished in the 30 days"
        " before it is switched on, each item once it has settled: a pull request when it is"
        " merged, an issue when it is closed, a release when it is published. Nothing finished"
        " earlier is read."
    )
    one = g.check_config(settings(backfill_days=1))
    assert "in the day before it is switched on" in g.describe(one)
    plain = g.check_config(settings())
    assert KINDS["github"].switching_on(config=plain) == g.SWITCH_ON


# Signed addresses


SIGNED_IMAGE = "https://private-user-images.githubusercontent.com/90001/300001-unit.png"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "unitPayload" + "." + "unitSig"
HOSTS = g.github_hosts("https://api.github.com")


def test_a_signed_image_in_markdown_loses_its_query():
    text = f"Before:\n\n![before]({SIGNED_IMAGE}?jwt={JWT})\n"
    assert g.unsigned(text, HOSTS) == f"Before:\n\n![before]({SIGNED_IMAGE})\n"


def test_a_signed_image_in_an_html_img_loses_its_query():
    text = f'<img width="300" alt="after" src="{SIGNED_IMAGE}?jwt={JWT}" />'
    assert g.unsigned(text, HOSTS) == f'<img width="300" alt="after" src="{SIGNED_IMAGE}" />'


def test_a_bare_signed_address_loses_its_query_and_keeps_what_follows_it():
    asset = "https://objects.githubusercontent.com/github-production-release-asset/1/2"
    query = "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=" + "unit" + "beef" * 4 + "&x=1"
    assert g.unsigned(f"Get {asset}?{query}.", HOSTS) == f"Get {asset}."
    upload = "https://github.com/user-attachments/files/4401/report.pdf"
    said = g.unsigned(f"[the report]({upload}?token=unitAttach77#page=2)", HOSTS)
    assert said == f"[the report]({upload}#page=2)"


def test_two_signed_addresses_on_one_line_each_lose_their_query():
    other = "https://user-images.githubusercontent.com/1/2.png"
    text = f"![a]({SIGNED_IMAGE}?jwt={JWT}) and ![b]({other}?access_token=unitAbc)"
    assert g.unsigned(text, HOSTS) == f"![a]({SIGNED_IMAGE}) and ![b]({other})"


def test_an_ordinary_address_is_left_as_it_is():
    for text in (
        "See https://github.com/quillworks/quill/issues?q=is%3Aopen+label%3Abug and more.",
        "Read https://docs.example.org/retries?token=unit1&section=2 there.",  # not GitHub's
        "https://githubusercontent.com.example.org/x.png?jwt=unit",  # a host that only starts so
        f"{SIGNED_IMAGE}?size=2",
        "No address at all.",
    ):
        assert g.unsigned(text, HOSTS) == text


def test_an_enterprise_servers_own_host_counts_as_githubs():
    hosts = g.github_hosts("https://ghe.example.test/api/v3")
    image = "https://media.ghe.example.test/user/9/files/1.png"
    assert g.unsigned(f"![x]({image}?token=unit9)", hosts) == f"![x]({image})"
    assert g.unsigned(f"![x]({image}?token=unit9)", HOSTS) == f"![x]({image}?token=unit9)"


# Cuts


def spans_of(text: str):
    from cairn.spans import chunk

    return chunk("cap_000000000000", text)


def test_a_body_within_its_bound_is_kept_whole():
    body = "One.\n\n- two\n- three\n\n```python\nfour = 4\n```\n"
    assert g.cut(body, 4, 10_000) == (body.rstrip("\n"), 4, False)


def test_a_body_past_its_bound_is_cut_between_two_passages():
    body = "\n\n".join(f"Paragraph {i}." for i in range(1, 11))
    kept, count, was_cut = g.cut(body, 3, 10_000)
    assert (kept, count, was_cut) == ("Paragraph 1.\n\nParagraph 2.\n\nParagraph 3.", 3, True)


def test_a_cut_never_falls_inside_a_code_fence():
    fence = "```\n" + "\n\n".join(f"line {i}" for i in range(40)) + "\n```"
    body = f"Before.\n\n{fence}\n\nAfter."
    kept, count, was_cut = g.cut(body, 2, 10_000)
    assert kept == f"Before.\n\n{fence}" and count == 2 and was_cut
    kept, count, was_cut = g.cut(body, 1, 10_000)
    assert kept == "Before." and count == 1 and was_cut


def test_an_unclosed_fence_is_closed_so_that_what_follows_stays_out_of_it():
    body = "Before.\n\n````js\nlet a = 1\n```\nstill code\n"
    closed = g.fenced(body)
    assert closed.startswith(body.rstrip("\n")) and closed.endswith("\n````")
    after = spans_of(closed + "\n\nA line after it.")
    assert after[-1].kind == "paragraph" and after[-1].text == "A line after it."
    listed = "- an item\n\n  ~~~\n  code in the item\n"
    after = spans_of(g.fenced(listed) + "\n\nA line after it.")
    assert after[-1].kind == "paragraph"
    plain = "Nothing open.\n\n```\nclosed\n```"
    assert g.fenced(plain) == plain


def test_the_passages_of_a_body_are_counted_as_the_store_counts_them():
    from cairn.store.usage import passages_of

    body = "# A heading\n\nOne.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> quoted\n\n- x\n- y\n"
    kept, count, _ = g.cut(body, 100, 10_000)
    assert count == passages_of(kept) == 5


# Titles


def test_a_title_is_the_item_and_its_name_when_it_fits():
    assert g.title_of("quillworks/quill#12", "Back off  between\nretries") == (
        "quillworks/quill#12: Back off between retries"
    )
    assert g.title_of("quillworks/quill v1.2.0", None) == "quillworks/quill v1.2.0"
    assert g.title_of("quillworks/quill v1.2.0", "  ") == "quillworks/quill v1.2.0"


def test_a_long_title_is_cut_at_a_word_with_an_ellipsis_and_keeps_the_item_whole():
    from cairn.store.usage import path_bytes

    head = "quillworks/quill#4"
    said = g.title_of(head, "Long title " + "word " * 60)
    assert said.startswith(head + ": Long title word") and said.endswith("word…")
    assert len(said) <= 200 and path_bytes([said]) <= 200
    wide = g.title_of(head, "大きな変更 " * 60)  # three bytes a character
    assert wide.startswith(head + ": 大きな変更") and wide.endswith("…")
    assert path_bytes([wide]) <= 200
    one_word = g.title_of(head, "x" * 400)
    assert one_word.startswith(head + ": xxx") and len(one_word) <= 200
    long_repo = "q" * 39 + "/" + "n" * 100 + "#1234567"
    assert g.title_of(long_repo, "A name " * 20).startswith(long_repo)


def test_a_release_with_a_tag_too_long_for_a_title_keeps_its_repository():
    head = "quillworks/quill v" + "9" * 300
    said = g.title_of(head, "Name")
    assert said.startswith("quillworks/quill v99") and len(said) <= 200 and said.endswith("…")


def test_a_body_past_its_characters_is_cut_between_passages_and_a_long_fence_is_closed():
    body = "\n\n".join(f"Paragraph {i:02d}." for i in range(1, 21))  # 13 characters each
    kept, count, was_cut = g.cut(body, 100, 45)
    assert (kept, count, was_cut) == ("Paragraph 01.\n\nParagraph 02.\n\nParagraph 03.", 3, True)
    fence = "```\n" + "\n".join(f"line {i:03d}" for i in range(500)) + "\n```"
    kept, count, was_cut = g.cut(f"{fence}\n\nAfter.", 100, 200)
    assert was_cut and count == 1 and len(kept) <= 200 + 4 and kept.endswith("\n```")
    assert spans_of(kept + "\n\nA line after it.")[-1].kind == "paragraph"
    kept, count, was_cut = g.cut("x" * 500, 100, 200)
    assert (kept, count, was_cut) == ("x" * 200, 1, True)
