"""Shared test helpers extracted from test_reviewer6_forget_siblings_unit."""

import io


def restore(source, target):
    archive = io.BytesIO()
    source.export(archive)
    archive.seek(0)
    target.import_export(archive)
