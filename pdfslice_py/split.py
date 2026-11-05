"""PDF -> per-page JPG splitting, one worker process per PDF.

Mirrors pdfslice's split.ts: each PDF is split in its own spawned process, so
a memory blowup or native crash (PDFium) on one file only affects that file —
the batch continues with the next PDF.
"""

from __future__ import annotations

import multiprocessing
import signal as signal_module
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .discover import find_pdfs
from .filename_template import DEFAULT_TEMPLATE
from .logger import Logger, create_logger
from .split_one_file import SplitOneOptions


@dataclass
class SplitResult:
    pdf: str
    output_folder: str
    page_count: int
    images: list[str] = field(default_factory=list)
    skipped: bool = False
    # Set when this file's worker process crashed or errored; page_count/images will be empty.
    failed: bool = False
    error_message: str | None = None


def split_all(
    input: str | Path,  # noqa: A002 - mirrors TS `input` option name
    logger: Logger | None = None,
    level: int = 1,
    flatten: bool = False,
    template: str = DEFAULT_TEMPLATE,
    force: bool = False,
    dry_run: bool = False,
) -> list[SplitResult]:
    """Split every PDF found under `input` into per-page JPG images.

    `logger` is optional for library use — defaults to an info-level
    `Logger` (see `create_logger`) when not provided.

    Workers are started with the "spawn" method, so scripts that call this
    must guard their entry point with `if __name__ == "__main__":`.
    """
    if logger is None:
        logger = create_logger()
    pdfs = find_pdfs(input, level)
    logger.info(f"Found {len(pdfs)} PDF file(s) under {input}", search_level=level)

    results: list[SplitResult] = []
    for index, pdf_path in enumerate(pdfs):
        logger.info(f"Processing PDF {index + 1}/{len(pdfs)}", pdf_path=pdf_path)
        result = _split_one_in_worker(
            SplitOneOptions(
                pdf_path=str(pdf_path),
                input=str(input),
                flatten=flatten,
                template=template,
                force=force,
                dry_run=dry_run,
            ),
            logger,
        )
        results.append(result)

        # The worker has no logger, so the outcome is logged here.
        if not result.failed:
            if dry_run:
                logger.info(
                    f"[dry-run] would split {pdf_path} into {result.output_folder} "
                    f"({result.page_count} page(s))"
                )
            elif result.skipped:
                logger.info(
                    "Skipped, already split",
                    pdf_path=pdf_path,
                    output_folder=result.output_folder,
                    page_count=result.page_count,
                )
            else:
                logger.info(
                    f"Split complete: {result.page_count} page(s)",
                    pdf_path=pdf_path,
                    output_folder=result.output_folder,
                    page_count=result.page_count,
                )

    failed = [r for r in results if r.failed]
    if failed:
        logger.warn(
            f"{len(failed)} of {len(pdfs)} PDF(s) failed to split",
            failed=[r.pdf for r in failed],
        )

    return results


def _failed_result(pdf_path: str, error_message: str) -> SplitResult:
    return SplitResult(
        pdf=pdf_path,
        output_folder="",
        page_count=0,
        images=[],
        skipped=False,
        failed=True,
        error_message=error_message,
    )


def _split_one_in_worker(opts: SplitOneOptions, logger: Logger) -> SplitResult:
    """Run split_one_file for a single PDF in its own child process."""
    # Imported here so the child process only imports the worker module on spawn.
    from .split_worker import run_worker

    pdf_path = opts.pdf_path
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=run_worker, args=(opts, sender))

    started_at = time.monotonic()
    proc.start()
    # Close our copy of the child's end so a dead child shows up as EOF, not a hang.
    sender.close()
    logger.debug("Worker started", pdf_path=pdf_path, pid=proc.pid)

    last: dict[str, Any] = {"stage": "start"}
    final: dict[str, Any] | None = None
    try:
        while True:
            try:
                msg = receiver.recv()
            except EOFError:
                break  # worker ended without sending a result
            if "progress" in msg:
                last = msg["progress"]
                if last.get("file_name") is not None:
                    logger.debug(
                        "Wrote page image",
                        file_name=last["file_name"],
                        page=last.get("page"),
                        page_count=last.get("page_count"),
                        bytes=last.get("bytes"),
                        rss_mb=last.get("rss_mb"),
                    )
                continue
            final = msg
            break
    finally:
        receiver.close()
        proc.join()

    duration_ms = round((time.monotonic() - started_at) * 1000)

    if final is not None and final.get("ok"):
        logger.debug(
            "Worker finished",
            pdf_path=pdf_path,
            pid=proc.pid,
            duration_ms=duration_ms,
            rss_mb=last.get("rss_mb"),
        )
        r = final["result"]
        return SplitResult(
            pdf=r["pdf"],
            output_folder=r["output_folder"],
            page_count=r["page_count"],
            images=r["images"],
            skipped=r["skipped"],
        )

    if final is not None:
        error = final["error"]
        logger.error(
            "Failed to split PDF",
            pdf_path=pdf_path,
            stage=final.get("stage"),
            page=final.get("page"),
            rss_mb=last.get("rss_mb"),
            error=error,
        )
        return _failed_result(pdf_path, error["message"])

    # No result message at all: the process crashed outright (e.g. OOM kill)
    # — treat as failure for this file and move on rather than losing the batch.
    exit_code = proc.exitcode
    sig = None
    if exit_code is not None and exit_code < 0:
        try:
            sig = signal_module.Signals(-exit_code).name
        except ValueError:
            sig = f"signal {-exit_code}"
    reason = (
        f"killed by signal {sig} (likely out of memory)"
        if sig is not None
        else f"exited with code {exit_code}"
    )
    logger.error(
        "Worker process crashed",
        pdf_path=pdf_path,
        reason=reason,
        pid=proc.pid,
        duration_ms=duration_ms,
        exit_code=exit_code if sig is None else None,
        signal=sig,
        stage=last.get("stage"),
        page=last.get("page"),
        page_count=last.get("page_count"),
        rss_mb=last.get("rss_mb"),
    )
    return _failed_result(pdf_path, reason)
