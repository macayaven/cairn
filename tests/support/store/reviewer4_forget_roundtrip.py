"""Shared test helpers extracted from test_reviewer4_forget_roundtrip_unit."""

import io
from contextlib import closing

import pytest

from cairn.store import Store


@pytest.fixture
def store():
    with closing(Store(":memory:")) as source:
        yield source


def versions(store, project_id):
    return {
        (head.slug, version): store.get_page(project_id, head.slug, version)
        for head in store.list_pages(project_id)
        for version in store.page_versions(project_id, head.slug)
    }


def assert_round_trip(store, project):
    archive = io.BytesIO()
    exported = store.export(archive)
    archive.seek(0)
    with closing(Store(":memory:")) as target:
        assert target.import_export(archive).pages == exported.pages
        original = versions(store, project.id)
        restored = versions(target, target.get_project(project.name).id)
        # Import gives the project a new id. Everything else belongs to the version.
        assert {
            key: page.model_copy(update={"project_id": project.id})
            for key, page in restored.items()
        } == original
