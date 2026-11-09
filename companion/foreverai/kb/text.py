"""Text extraction: MediaWiki wikitext and Blizzard HTML -> plain text, plus chunking."""

from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser

import mwparserfromhell
from mwparserfromhell.nodes import Comment, ExternalLink, Heading, HTMLEntity, Tag, Template, Text, Wikilink

_SKIP_TEMPLATES = {"clear", "stub", "reflist", "toc", "notoc", "-", "!", "!!", "anchor", "cite", "cite web",
                   "main", "see also", "seealso", "for", "about", "wowhead"}
_SKIP_LINK_PREFIX = ("file:", "image:", "category:", "media:")


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _render(nodes) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, Text):
            out.append(str(node))
        elif isinstance(node, Wikilink):
            title = str(node.title).strip()
            if title.lower().startswith(_SKIP_LINK_PREFIX):
                continue
            out.append(_render(node.text.nodes) if node.text else title.split("#")[0])
        elif isinstance(node, ExternalLink):
            out.append(_render(node.title.nodes) if node.title else "")
        elif isinstance(node, Template):
            name = str(node.name).strip()
            if name.lower() in _SKIP_TEMPLATES or name.lower().startswith(("nav", "cite")):
                continue
            parts = []
            for p in node.params:
                key = str(p.name).strip()
                val = re.sub(r"\s+", " ", _render(p.value.nodes)).strip()
                if not val:
                    continue
                parts.append(val if key.isdigit() else f"{key}={val}")
            out.append(f"[{name}: {'; '.join(parts)}]" if parts else "")
        elif isinstance(node, Heading):
            out.append("\n" + "#" * node.level + " " + _render(node.title.nodes).strip() + "\n")
        elif isinstance(node, Tag):
            tag = str(node.tag).lower()
            if tag in ("ref", "references", "gallery", "noinclude"):
                continue
            if tag == "br":
                out.append("\n")
                continue
            inner = _render(node.contents.nodes) if node.contents else ""
            if tag in ("td", "th"):
                out.append(inner.strip() + " | ")
            elif tag == "tr":
                out.append("\n" + inner)
            elif tag == "li":
                out.append("\n- " + inner.strip())
            else:
                out.append(inner)
        elif isinstance(node, Comment):
            continue
        elif isinstance(node, HTMLEntity):
            out.append(node.normalize())
        else:
            out.append(str(node))
    return "".join(out)


def clean(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def wikitext_to_text(wikitext: str) -> str:
    code = mwparserfromhell.parse(wikitext)
    return clean(_render(code.nodes))


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "tr", "hr", "ul", "ol", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("h1", "h2", "h3", "h4"):
            self.out.append("\n## ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(fragment: str) -> str:
    p = _HTMLText()
    p.feed(fragment)
    return clean(html.unescape("".join(p.out)))


def chunk_text(title: str, text: str, max_chars: int = 1400) -> list[tuple[str, str]]:
    """Split text into (section, chunk) pairs along markdown-ish headings and paragraphs."""
    sections: list[tuple[str, str]] = []
    current, buf = "", []
    for line in text.split("\n"):
        m = re.match(r"^#{1,6} (.+)$", line)
        if m:
            if buf:
                sections.append((current, "\n".join(buf).strip()))
            current, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if buf:
        sections.append((current, "\n".join(buf).strip()))
    chunks: list[tuple[str, str]] = []
    for section, body in sections:
        if not body:
            continue
        paras = body.split("\n\n")
        acc = ""
        for para in paras:
            while len(para) > max_chars:
                chunks.append((section, para[:max_chars]))
                para = para[max_chars:]
            if len(acc) + len(para) + 2 > max_chars and acc:
                chunks.append((section, acc.strip()))
                acc = ""
            acc += para + "\n\n"
        if acc.strip():
            chunks.append((section, acc.strip()))
    return chunks
