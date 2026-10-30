#!/usr/bin/env python3
"""Validate local website links, anchors, and static assets without dependencies."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.duplicates: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            if element_id in self.ids:
                self.duplicates.add(element_id)
            self.ids.add(element_id)
        for attr in ("href", "src"):
            value = values.get(attr)
            if value:
                self.links.append(value)


def page_files() -> list[Path]:
    return sorted(ROOT.rglob("*.html"))


def page_for_url(source: Path, url_path: str) -> Path:
    if not url_path:
        return source
    target = (source.parent / unquote(url_path)).resolve()
    if target.is_dir():
        target = target / "index.html"
    return target


def main() -> int:
    pages = page_files()
    if not pages:
        print("ERROR: no HTML pages found")
        return 1

    parsed: dict[Path, PageParser] = {}
    errors: list[str] = []

    for page in pages:
        parser = PageParser()
        try:
            parser.feed(page.read_text(encoding="utf-8"))
            parser.close()
        except Exception as exc:  # pragma: no cover - defensive parser boundary
            errors.append(f"{page.relative_to(ROOT)}: HTML parsing failed: {exc}")
            continue
        parsed[page.resolve()] = parser
        for duplicate in sorted(parser.duplicates):
            errors.append(f"{page.relative_to(ROOT)}: duplicate id '{duplicate}'")

    for source, parser in parsed.items():
        for raw_url in parser.links:
            parts = urlsplit(raw_url)
            if parts.scheme or parts.netloc or raw_url.startswith("//"):
                continue
            target = page_for_url(source, parts.path)
            if not target.exists():
                errors.append(
                    f"{source.relative_to(ROOT)}: missing local target '{raw_url}'"
                )
                continue
            if parts.fragment:
                target_parser = parsed.get(target.resolve())
                if target_parser is None and target.suffix.lower() == ".html":
                    target_parser = PageParser()
                    target_parser.feed(target.read_text(encoding="utf-8"))
                    target_parser.close()
                    parsed[target.resolve()] = target_parser
                fragment = unquote(parts.fragment)
                if target_parser and fragment not in target_parser.ids:
                    errors.append(
                        f"{source.relative_to(ROOT)}: missing anchor '{raw_url}'"
                    )

    if errors:
        print("Website validation failed:")
        for error in errors:
            print(f" - {error}")
        return 1

    print(f"Website validation passed: {len(pages)} HTML pages, local links and assets resolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())