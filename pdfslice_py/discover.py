"""PDF/image discovery on the filesystem."""

from __future__ import annotations

import os
from pathlib import Path

from .filename_template import DEFAULT_TEMPLATE, compile_template

_IMAGE_SUFFIXES = {".jpg", ".jpeg"}


def find_pdfs(root: str | Path, level: int = 1) -> list[str]:
    """Find all PDF files under `root`, descending up to `level` directories
    deep. level=1 (default): PDFs directly in `root` only. level=2: `root`
    and one subfolder deep. Etc. If `root` itself is a PDF file, returns
    just that file."""
    root = Path(root)
    if root.is_file():
        return [str(root)] if root.suffix.lower() == ".pdf" else []

    results: list[str] = []

    def walk(directory: Path, depth: int) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                full = Path(entry.path)
                if entry.is_file() and full.suffix.lower() == ".pdf":
                    results.append(str(full))
                elif entry.is_dir() and depth < level:
                    walk(full, depth + 1)

    walk(root, 1)
    # scandir order is filesystem-dependent; sort so both CLIs process the same order.
    return sorted(results)


def find_images_deep(root: str | Path) -> list[str]:
    """Recursively find all image files (jpg/jpeg) under `root`, any depth.
    Used by gather/check, since split output can be nested by flatten mode."""
    root = Path(root)
    results: list[str] = []

    def walk(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                full = Path(entry.path)
                if entry.is_file() and full.suffix.lower() in _IMAGE_SUFFIXES:
                    results.append(str(full))
                elif entry.is_dir():
                    walk(full)

    walk(root)
    return sorted(results)


def page_image_name(base_name: str, page: int, template: str = DEFAULT_TEMPLATE) -> str:
    """Build the page-image filename using a template (default:
    "{{filename}}.{{page_number}}.jpg"). Page number is zero-padded to 3
    digits; if the number itself is wider than 3 digits, no padding is
    applied (natural width is used)."""
    return compile_template(template).render(base_name, page)


def parse_page_from_image_name(file_name: str, template: str = DEFAULT_TEMPLATE) -> int | None:
    """Parse a page number back out of a name produced by page_image_name,
    using the same template it was generated with."""
    return compile_template(template).parse_page(file_name)
