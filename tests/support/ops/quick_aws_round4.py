"""Shared test helpers extracted from test_quick_aws_round4_unit."""

from markdown_it import MarkdownIt

from support.ops.quick_infra import QUICK

README = QUICK / "README.md"


def section(title):
    tokens = MarkdownIt().parse(README.read_text())
    start = next(
        index + 3
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2" and tokens[index + 1].content == title
    )
    end = next(
        (index for index in range(start, len(tokens)) if tokens[index].tag == "h2"), len(tokens)
    )
    return [child for token in tokens[start:end] for child in token.children or []]
