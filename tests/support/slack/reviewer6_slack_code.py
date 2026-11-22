"""Shared test helpers extracted from test_reviewer6_slack_code_unit."""

from markdown_it import MarkdownIt

AUTHOR = "Cedar Quill · 2030-03-17 17:46:40 UTC"

READER = MarkdownIt("commonmark", {"html": False}).enable("table")
