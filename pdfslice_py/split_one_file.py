"""Split a single PDF into page images.

Contains no logging calls (the caller — the worker-process wrapper or the
parent that reads its progress messages — handles logging), mirroring
pdfslice's split-one-file.ts. Image quality/resolution: scale 2, JPEG quality 90.
"""

from __future__ import annotations

import hashlib
import io
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .discover import page_image_name
from .hash import hash_file
from .logger import utc_now_iso
from .manifest import Manifest, ManifestImageEntry, read_manifest, write_manifest
from .pdf_rasterizer import open_pdf_for_rasterizing

_RENDER_SCALE = 2  # ~2x native resolution
_JPEG_QUALITY = 90

# {"stage": ..., "page": ..., "page_count": ..., "file_name": ..., "bytes": ..., "rss_mb": ...}
SplitProgress = dict[str, Any]


@dataclass
class SplitOneOptions:
    pdf_path: str
    input: str  # noqa: A003
    flatten: bool
    template: str
    force: bool
    dry_run: bool


@dataclass
class SplitOneResult:
    pdf: str
    output_folder: str
    page_count: int
    images: list[str] = field(default_factory=list)
    skipped: bool = False


def _folder_name_for(pdf_path: str | Path) -> str:
    return Path(pdf_path).stem


def split_one_file(
    opts: SplitOneOptions,
    on_progress: Callable[[SplitProgress], None] = lambda _p: None,
) -> SplitOneResult:
    pdf_path = opts.pdf_path
    base_name = _folder_name_for(pdf_path)
    parent_dir = Path(opts.input) if opts.flatten else Path(pdf_path).parent
    output_folder = parent_dir / base_name
    dest_pdf_path = output_folder / Path(pdf_path).name

    # Skip re-splitting when the output folder already reflects this exact
    # PDF (same content, same template) — avoids redundant rasterization
    # work on repeated runs. --force bypasses this check.
    if not opts.force and not opts.dry_run and dest_pdf_path.exists():
        on_progress({"stage": "check-existing"})
        existing = read_manifest(output_folder)
        if (
            existing is not None
            and existing.filenameTemplate == opts.template
            and existing.sourcePdfHash == hash_file(pdf_path)
        ):
            return SplitOneResult(
                pdf=str(dest_pdf_path),
                output_folder=str(output_folder),
                page_count=existing.pageCount,
                images=[str(output_folder / i.file) for i in existing.images],
                skipped=True,
            )

    if opts.dry_run:
        on_progress({"stage": "open"})
        # Page count comes from the renderer, the same source the real run uses.
        probe = open_pdf_for_rasterizing(pdf_path, _RENDER_SCALE)
        try:
            page_count = probe.page_count
        finally:
            probe.destroy()
        return SplitOneResult(
            pdf=str(pdf_path),
            output_folder=str(output_folder),
            page_count=page_count,
            images=[],
            skipped=True,
        )

    on_progress({"stage": "mkdir"})
    output_folder.mkdir(parents=True, exist_ok=True)

    # "Move" without deleting the original: copy into the new folder,
    # overwriting any stale copy from a previous split. The original PDF
    # at its source path is left untouched, per spec.
    on_progress({"stage": "copy"})
    shutil.copyfile(pdf_path, dest_pdf_path)

    # Clear any page images left over from a previous split of this folder
    # (e.g. the old PDF had more pages, or used a different template) so
    # stale images never linger alongside the freshly generated set.
    on_progress({"stage": "cleanup"})
    for entry in output_folder.iterdir():
        if entry.is_file() and entry.suffix.lower() in {".jpg", ".jpeg"}:
            entry.unlink()

    images: list[str] = []
    image_entries: list[ManifestImageEntry] = []

    on_progress({"stage": "open"})
    rasterizer = open_pdf_for_rasterizing(dest_pdf_path, _RENDER_SCALE)
    page_count = rasterizer.page_count

    try:
        for page_num in range(1, page_count + 1):
            file_name = page_image_name(base_name, page_num, opts.template)
            image_path = output_folder / file_name

            on_progress({"stage": "render", "page": page_num, "page_count": page_count})
            image = rasterizer.render_page(page_num)

            on_progress({"stage": "encode", "page": page_num, "page_count": page_count})
            # Encoder options match what pdfslice passes to sharp.
            buffer = io.BytesIO()
            image.save(
                buffer,
                "JPEG",
                quality=_JPEG_QUALITY,
                subsampling="4:2:0",
                progressive=False,
                optimize=True,
            )
            jpeg_bytes = buffer.getvalue()

            on_progress({"stage": "write", "page": page_num, "page_count": page_count})
            image_path.write_bytes(jpeg_bytes)
            # Hash the buffer we already have in memory instead of re-reading
            # the file back from disk — same result, one less full-page read.
            file_hash = hashlib.sha256(jpeg_bytes).hexdigest()
            images.append(str(image_path))
            image_entries.append(ManifestImageEntry(file=file_name, page=page_num, hash=file_hash))
            on_progress(
                {
                    "stage": "write",
                    "page": page_num,
                    "page_count": page_count,
                    "file_name": file_name,
                    "bytes": len(jpeg_bytes),
                }
            )
    finally:
        rasterizer.destroy()

    # The manifest is written last: a folder without one is an interrupted
    # split, which gather/check ignore.
    on_progress({"stage": "manifest", "page_count": page_count})
    manifest = Manifest(
        version=1,
        sourcePdf=dest_pdf_path.name,
        sourcePdfHash=hash_file(dest_pdf_path),
        pageCount=page_count,
        images=image_entries,
        updatedAt=utc_now_iso(),
        filenameTemplate=opts.template,
    )
    write_manifest(output_folder, manifest)

    return SplitOneResult(
        pdf=str(dest_pdf_path),
        output_folder=str(output_folder),
        page_count=page_count,
        images=images,
        skipped=False,
    )
