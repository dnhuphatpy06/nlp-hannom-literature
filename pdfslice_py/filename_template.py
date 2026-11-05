"""Page-image filename templating."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_TEMPLATE = "{{filename}}.{{page_number}}.jpg"

_PLACEHOLDER_PATTERN = re.compile(r"(\{\{filename\}\}|\{\{page_number\}\})")


def _pad_page_number(page: int) -> str:
    return str(page).zfill(3) if page < 1000 else str(page)


@dataclass(frozen=True)
class FilenameTemplate:
    render: Callable[[str, int], str]
    parse_page: Callable[[str], int | None]


def compile_template(template: str) -> FilenameTemplate:
    page_count = template.count("{{page_number}}")
    if page_count != 1:
        raise ValueError(
            f"Template must contain exactly one {{{{page_number}}}} placeholder, "
            f'found {page_count} in "{template}"'
        )

    parts = _PLACEHOLDER_PATTERN.split(template)
    regex_source = ""
    for i, part in enumerate(parts):
        if i % 2 == 0:
            regex_source += re.escape(part)
        elif part == "{{page_number}}":
            regex_source += r"(\d+)"
        elif part == "{{filename}}":
            regex_source += ".+?"

    regex = re.compile(f"^{regex_source}$", re.IGNORECASE)

    def render(base_name: str, page: int) -> str:
        return template.replace("{{filename}}", base_name).replace(
            "{{page_number}}", _pad_page_number(page)
        )

    def parse_page(file_name: str) -> int | None:
        m = regex.match(file_name)
        if not m:
            return None
        return int(m.group(1))

    return FilenameTemplate(render=render, parse_page=parse_page)
