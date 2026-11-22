"""Shared test helpers extracted from test_search_unit."""

import random
from pathlib import Path

import pytest

from cairn.store import Claim, Store


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def page(view, project_id: str, slug: str, *texts: str, title: str | None = None):
    claims = [Claim(id=f"cl_{slug}_{n}", text=t, span_ids=()) for n, t in enumerate(texts)]
    return view.save_page(project_id, slug, title or slug.title(), "", claims)


VOCABULARY = [
    "quill",
    "Quill",
    "QUILL",
    "naïve",
    "naive",
    "café",
    "cafe",
    "lumen",
    "queue",
    "relay",
    "retry_count",
    "retry",
    "count",
    "the",
    "a",
    "b",
    "ß",
    "zed",
    "ünïcode",
    "Ünïcode",
    "x9",
    "42",
    "the",
    "the",
]


def random_corpus(store, rng: random.Random) -> dict[str, str]:
    ids = {name: store.create_project(name).id for name in ("api", "web", "journal", "ops")}
    for name, project_id in ids.items():
        for p in range(rng.randint(0, 4)):
            texts = []
            for _ in range(rng.randint(1, 5)):
                n = rng.choice((1, 3, 8, 20, 150))  # 150: a docsize past one varint byte
                words = [rng.choice(VOCABULARY) for _ in range(n)]
                if rng.random() < 0.4:
                    words += ["the"] * rng.randint(1, 4)  # df > N/2: the IDF floor
                texts.append(rng.choice((" ", "_", ", ", "-")).join(words))
            title = " ".join(rng.choice(VOCABULARY) for _ in range(rng.randint(0, 3)))
            page(store, project_id, f"{name}-{p}", *texts, title=title or "Untitled")
        if rng.random() < 0.3 and store.list_pages(project_id):  # a page saved again
            page(store, project_id, f"{name}-0", "quill relay quill", title="Quill title only")
    return ids


QUERIES = (
    "quill",
    "Quill quill",  # the same word twice: two phrases, as FTS5 counts them
    "naïve café",
    "retry_count",  # one word, a two-token phrase
    "retry_count count_retry the",
    "the",  # in most claims: the IDF floor
    "title only",  # title-only hits
    "ß x9 42 zed",
    "a_a b_b a",  # self-overlapping phrases
)
