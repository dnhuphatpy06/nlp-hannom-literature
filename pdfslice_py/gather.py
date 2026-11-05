"""Rebuild a PDF from split page images, or just check for missing pages.

Pillow saves a multi-page PDF from the JPEGs directly with
`resolution=72.0`, putting one image pixel per PDF point (i.e. treating the
image as if it were 72 DPI), regardless of the image's own DPI metadata.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image

from .discover import find_images_deep, parse_page_from_image_name
from .failure import describe_error, get_progress, set_progress
from .filename_template import DEFAULT_TEMPLATE
from .hash import hash_file
from .logger import Logger, create_logger, utc_now_iso
from .manifest import Manifest, ManifestImageEntry, manifest_path_for, read_manifest, write_manifest

_PDF_PAGE_RESOLUTION = 72.0  # 1 image pixel == 1 PDF point

Action = Literal[
    "created",
    "skipped-unchanged",
    "would-create",
    "missing-source",
    "check-only",
    "no-images",
    "failed",
]


@dataclass
class UnitReport:
    folder: str
    source_pdf: str | None
    page_count: int | None
    found_images: int
    missing_pages: list[int]
    action: Action
    output_pdf: str | None = None
    error: str | None = None  # set when this unit failed; the run continues with the next one


@dataclass
class GatherOptions:
    input: str
    dry_run: bool = False
    check_only: bool = False
    backup: bool = True
    logger: Logger = field(default=None)  # type: ignore[assignment]


def _find_unit_folders(root: str | Path) -> list[str]:
    """Discover "unit" folders: a folder produced by `split` — recognized by
    the `.pdfslice-manifest.json` written by split (not by the mere presence
    of a PDF, since the source folder the PDF was found in also still
    contains a PDF that split never deletes)."""
    root = Path(root)
    if not root.is_dir():
        return []

    units: list[str] = []

    def walk(directory: Path) -> None:
        if manifest_path_for(directory).exists():
            units.append(str(directory))
            return  # don't descend further into a recognized unit
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                walk(entry)

    walk(root)
    return units


def gather_all(
    input: str | Path,  # noqa: A002 - mirrors TS `input` option name
    logger: Logger | None = None,
    dry_run: bool = False,
    check_only: bool = False,
    backup: bool = True,
) -> list[UnitReport]:
    """Rebuild a PDF from split page images for every unit folder under
    `input`, or (with `check_only=True`) just report missing pages.

    `logger` is optional for library use — defaults to a quiet-info
    `Logger` (see `create_logger`) when not provided.
    """
    if logger is None:
        logger = create_logger()
    units = _find_unit_folders(input)
    logger.info(f"Found {len(units)} unit folder(s) under {input}")

    reports: list[UnitReport] = []
    for index, folder in enumerate(units):
        logger.info(f"Processing unit {index + 1}/{len(units)}", folder=folder)
        try:
            reports.append(
                _gather_one(
                    folder, logger=logger, dry_run=dry_run, check_only=check_only, backup=backup
                )
            )
        except Exception as err:
            # One bad unit must not abort the rest, and the error must say where it died.
            logger.error(
                "Failed to gather unit",
                folder=folder,
                stage=get_progress().get("stage"),
                error=describe_error(err),
            )
            reports.append(
                UnitReport(
                    folder=folder,
                    source_pdf=None,
                    page_count=None,
                    found_images=0,
                    missing_pages=[],
                    action="failed",
                    error=str(err),
                )
            )
    return reports


def _gather_one(
    folder: str,
    *,
    logger: Logger,
    dry_run: bool = False,
    check_only: bool = False,
    backup: bool = True,
) -> UnitReport:
    def track(stage: str) -> None:
        set_progress(folder=folder, stage=stage)

    track("read-manifest")
    manifest = read_manifest(folder)
    if manifest is None:
        logger.warn("No manifest found in unit folder", folder=folder)
        return UnitReport(
            folder=folder,
            source_pdf=None,
            page_count=None,
            found_images=0,
            missing_pages=[],
            action="missing-source",
        )

    source_pdf_path = Path(folder) / manifest.sourcePdf
    if not source_pdf_path.exists():
        logger.warn(
            "Source PDF recorded in manifest is missing",
            folder=folder,
            expected=manifest.sourcePdf,
        )
        return UnitReport(
            folder=folder,
            source_pdf=None,
            page_count=None,
            found_images=0,
            missing_pages=[],
            action="missing-source",
        )

    # Expected page count is what split recorded (from the renderer that made the
    # images). Re-parsing the PDF with another library can disagree on odd files,
    # and after a partial gather the overwritten PDF no longer has every page.
    page_count = manifest.pageCount
    template = manifest.filenameTemplate or DEFAULT_TEMPLATE

    track("scan-images")
    image_paths = find_images_deep(folder)
    found_pages: set[int] = set()
    for img_path in image_paths:
        page = parse_page_from_image_name(Path(img_path).name, template)
        if page is not None:
            found_pages.add(page)

    missing_pages = [p for p in range(1, page_count + 1) if p not in found_pages]

    if missing_pages:
        logger.warn(
            "Missing page image(s)",
            folder=folder,
            missing_pages=missing_pages,
            expected=page_count,
            found=len(found_pages),
        )
    else:
        logger.info(f"All {page_count} page image(s) present", folder=folder)

    if check_only:
        return UnitReport(
            folder=folder,
            source_pdf=str(source_pdf_path),
            page_count=page_count,
            found_images=len(found_pages),
            missing_pages=missing_pages,
            action="check-only",
        )

    # Decide whether to (re)build the combined PDF, comparing the manifest's
    # recorded image hashes against the current on-disk images.
    track("hash-images")
    ordered = sorted(
        (p for p in image_paths if parse_page_from_image_name(Path(p).name, template) is not None),
        key=lambda p: parse_page_from_image_name(Path(p).name, template),  # type: ignore[arg-type]
    )
    current_entries = [
        ManifestImageEntry(
            file=Path(p).name,
            page=parse_page_from_image_name(Path(p).name, template),  # type: ignore[arg-type]
            hash=hash_file(p),
        )
        for p in ordered
    ]

    # Output overwrites the source PDF in place, per spec.
    output_pdf_path = source_pdf_path

    # Overwriting the source with an empty PDF would destroy it.
    if not current_entries:
        logger.warn("No page images found, skipping PDF creation", folder=folder)
        return UnitReport(
            folder=folder,
            source_pdf=str(source_pdf_path),
            page_count=page_count,
            found_images=0,
            missing_pages=missing_pages,
            action="no-images",
        )

    unchanged = (
        bool(manifest.gatheredAt)
        and len(manifest.images) == len(current_entries)
        and all(a.hash == b.hash for a, b in zip(manifest.images, current_entries, strict=True))
    )

    if unchanged and not missing_pages:
        logger.info("No changes detected, skipping PDF creation", folder=folder)
        return UnitReport(
            folder=folder,
            source_pdf=str(source_pdf_path),
            page_count=page_count,
            found_images=len(found_pages),
            missing_pages=missing_pages,
            output_pdf=str(output_pdf_path),
            action="skipped-unchanged",
        )

    if dry_run:
        logger.info(f"[dry-run] would overwrite {output_pdf_path}")
        return UnitReport(
            folder=folder,
            source_pdf=str(source_pdf_path),
            page_count=page_count,
            found_images=len(found_pages),
            missing_pages=missing_pages,
            output_pdf=str(output_pdf_path),
            action="would-create",
        )

    # Back up the existing PDF before overwriting it (default on; --no-backup to skip).
    if backup:
        track("backup")
        backup_path = Path(folder) / f"{output_pdf_path.stem}.bak-{int(time.time() * 1000)}.pdf"
        shutil.copyfile(output_pdf_path, backup_path)
        logger.info("Backed up previous PDF", backup_path=str(backup_path))

    track("build-pdf")
    images = [Image.open(p).convert("RGB") for p in ordered]
    first, rest = images[0], images[1:]
    track("write-pdf")
    first.save(
        output_pdf_path, "PDF", save_all=True, append_images=rest, resolution=_PDF_PAGE_RESOLUTION
    )
    for im in images:
        im.close()

    track("write-manifest")
    now = utc_now_iso()
    write_manifest(
        folder,
        Manifest(
            version=1,
            sourcePdf=manifest.sourcePdf,
            sourcePdfHash=hash_file(output_pdf_path),
            pageCount=page_count,
            images=current_entries,
            updatedAt=now,
            gatheredAt=now,
            filenameTemplate=template,
        ),
    )

    logger.info(
        "PDF overwritten",
        output_pdf=str(output_pdf_path),
        page_count=len(ordered),
        bytes=output_pdf_path.stat().st_size,
    )
    return UnitReport(
        folder=folder,
        source_pdf=str(source_pdf_path),
        page_count=page_count,
        found_images=len(found_pages),
        missing_pages=missing_pages,
        output_pdf=str(output_pdf_path),
        action="created",
    )
