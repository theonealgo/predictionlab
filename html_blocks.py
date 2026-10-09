"""Locate actual HTML elements while preserving the original source bytes."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass
class Block:
    tag: str
    attrs: dict
    start: int
    opening_end: int
    end: int = -1


class _Blocks(HTMLParser):
    def __init__(self, source: str, predicate):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.predicate = predicate
        self.lines = [0]
        self.lines.extend(i + 1 for i, char in enumerate(source) if char == "\n")
        self.stack = []
        self.blocks = []
        self.feed(source)

    def _source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        start = self._source_offset()
        block = Block(tag, attrs, start, start + len(self.get_starttag_text()))
        if self.predicate(tag, attrs):
            self.blocks.append(block)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(block)
        else:
            block.end = block.opening_end

    def handle_startendtag(self, tag, attrs):
        before = len(self.stack)
        self.handle_starttag(tag, attrs)
        if len(self.stack) > before:
            self.stack.pop().end = self._source_offset() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                block = self.stack[index]
                block.end = self.source.find(">", self._source_offset()) + 1
                del self.stack[index:]
                break


def blocks(source: str, predicate) -> list[Block]:
    """Never interpret a JavaScript template string as a page element."""
    return sorted(
        (block for block in _Blocks(source, predicate).blocks if block.end > block.start),
        key=lambda block: block.start,
    )


def remove_blocks(source: str, found: list[Block]) -> str:
    """Remove outermost selected elements once, retaining all other bytes."""
    outer = []
    for block in sorted(found, key=lambda item: (item.start, -item.end)):
        if not outer or block.start >= outer[-1].end:
            outer.append(block)
    for block in reversed(outer):
        source = source[:block.start] + source[block.end:]
    return source
