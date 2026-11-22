"""Shared test helpers extracted from test_design_system_unit."""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from support.paths import REPO_ROOT

PACKAGE = REPO_ROOT / "src" / "cairn"

CSS = PACKAGE / "static" / "cairn.css"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: list["Node | str"] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def all(self, tag: str | None = None, cls: str | None = None) -> list["Node"]:
        return [
            n
            for n in self.walk()
            if n is not self and (tag is None or n.tag == tag) and (cls is None or cls in n.classes)
        ]

    def kids(self) -> list["Node"]:
        return [c for c in self.children if isinstance(c, Node)]

    def within(self, cls: str) -> bool:
        node = self.parent
        while node is not None:
            if cls in node.classes:
                return True
            node = node.parent
        return False


class Tree(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self._stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs}, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in VOID:
            self._stack.append(node)

    def handle_endtag(self, tag):
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)


def tree(html: str) -> Node:
    return Tree(html).root


def rule(block: str, selector: str) -> str:
    """The declarations of the rule in `block` whose selector list is exactly `selector`."""
    flat = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    for found in re.finditer(r"([^{}]+)\{([^{}]*)\}", flat):
        if " ".join(found[1].split()) == selector:
            return found[2]
    raise AssertionError(f"no rule for {selector}")
