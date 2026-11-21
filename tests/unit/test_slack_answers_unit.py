"""What the bot says in Slack (review: UI and UX 5): a search answer that shows fewer hits
than there are says so and gives the search page's address, a hit of outside text is marked
as on the search page, a note waiting for its page links its project, and the product's name
comes from the caller."""

import json

from cairn.slack import blocks

BASE = "https://cairn.example.test"


def hit(n: int, **changes) -> dict:
    return {
        "project": "library", "page": f"jar-{n}", "title": f"Jar {n}", "claim_id": f"c-{n}",
        "text": f"Jar {n} holds quince.", "version": 1, "outside": False, **changes,
    }  # fmt: skip


def said(payload: dict) -> str:
    return "\n".join(block["text"]["text"] for block in payload["blocks"])


def test_a_search_showing_fewer_hits_than_it_found_says_how_many_and_links_the_rest():
    payload = blocks.found([hit(n) for n in range(12)], query="quince jars", base_url=BASE)
    for text in (said(payload), payload["text"]):
        assert "5 of 12 claims shown." in text
    assert f"<{BASE}/search?q=quince%20jars|See them all on the search page>" in said(payload)
    assert f"{BASE}/search?q=quince%20jars" in payload["text"]
    assert "Jar 5" not in json.dumps(payload)  # the rest only counted


def test_a_search_that_found_as_many_as_it_asked_for_says_or_more():
    payload = blocks.found([hit(n) for n in range(30)], query="quince", base_url=BASE, asked=30)
    assert "5 of 30 or more claims shown." in said(payload)


def test_a_search_showing_every_hit_says_no_count_and_no_link():
    payload = blocks.found([hit(n) for n in range(5)], query="quince", base_url=BASE)
    assert " of " not in said(payload) and "/search?" not in json.dumps(payload)


def test_a_hit_of_outside_text_carries_the_search_pages_mark():
    payload = blocks.found([hit(0, outside=True), hit(1)], query="quince", base_url=BASE)
    first, second = payload["blocks"][1:3]
    assert "outside text" in first["text"]["text"]
    assert "outside text" not in second["text"]["text"]
    assert "outside text" in payload["text"]


def test_a_note_waiting_for_its_page_links_its_project_not_the_page():
    payload = blocks.filed(
        title="Row 4", page_url=f"{BASE}/b/library/p/row-4", project="library", waiting=True,
        project_url=f"{BASE}/b/library",
    )  # fmt: skip
    assert f"<{BASE}/b/library|library>" in said(payload)
    assert "/p/row-4" not in json.dumps(payload)
    assert "stored" in said(payload).lower() and "appear shortly" in said(payload).lower()


def test_the_products_name_comes_from_the_caller_and_stays_cairn_by_default():
    for made in (
        lambda **name: blocks.link_prompt("https://fable.example.test/link?t=x", "/memory", **name),
        lambda **name: blocks.linked("", **name),
        lambda **name: blocks.help_text("/memory", **name),
    ):
        default = json.dumps(made())
        assert "cairn" in default
        named = json.dumps(made(product="Fable Memory"))
        assert "Fable Memory" in named and "cairn" not in named.lower()
