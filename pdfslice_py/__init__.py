"""pdfslice-py: split PDFs into page images, then gather or check them back.

Usable as a library (`from pdfslice_py import split_all, gather_all`) or as
the `pdfslice` CLI (`split`/`gather`/`check` subcommands).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .discover import find_images_deep, find_pdfs, page_image_name, parse_page_from_image_name
from .failure import describe_error, get_progress, set_progress
from .filename_template import DEFAULT_TEMPLATE, FilenameTemplate, compile_template
from .gather import Action, UnitReport, gather_all
from .hash import hash_file
from .logger import Logger, create_logger, format_line, utc_now_iso
from .manifest import (
    MANIFEST_FILENAME,
    Manifest,
    ManifestImageEntry,
    manifest_path_for,
    read_manifest,
    write_manifest,
)
from .split import SplitResult, split_all

try:
    # Single source of truth is pyproject.toml, which release-please bumps.
    __version__ = _pkg_version("pdfslice-py")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = [
    # split
    "split_all",
    "SplitResult",
    # gather / check
    "gather_all",
    "UnitReport",
    "Action",
    # manifest
    "Manifest",
    "ManifestImageEntry",
    "MANIFEST_FILENAME",
    "read_manifest",
    "write_manifest",
    "manifest_path_for",
    # discovery
    "find_pdfs",
    "find_images_deep",
    "page_image_name",
    "parse_page_from_image_name",
    # filename templating
    "DEFAULT_TEMPLATE",
    "FilenameTemplate",
    "compile_template",
    # hashing
    "hash_file",
    # logging
    "Logger",
    "create_logger",
    "format_line",
    "utc_now_iso",
    # error detail
    "describe_error",
    "get_progress",
    "set_progress",
]
