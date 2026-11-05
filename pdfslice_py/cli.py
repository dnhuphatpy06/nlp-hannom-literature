"""CLI: split, gather, check commands."""

from __future__ import annotations

import faulthandler
import sys
import time

import click

from . import __version__
from .failure import describe_error, get_progress
from .filename_template import DEFAULT_TEMPLATE
from .gather import gather_all
from .logger import Logger, create_logger
from .split import split_all


def _log_flags(f: click.decorators.FC) -> click.decorators.FC:
    f = click.option("--verbose", is_flag=True, default=False, help="Enable debug logging")(f)
    f = click.option("--quiet", is_flag=True, default=False, help="Only log errors")(f)
    f = click.option("--log-file", default=None, help="Path to also write logs as JSON")(f)
    return f


def _fail(logger: Logger, message: str, input: str, err: Exception) -> None:  # noqa: A002
    logger.error(message, input=input, **get_progress(), error=describe_error(err))
    sys.exit(1)


@click.group()
@click.version_option(version=__version__, prog_name="pdfslice")
def cli() -> None:
    """Split PDFs into page images, then gather or check them back."""
    # A native crash (e.g. inside PDFium) would otherwise end the process with
    # no message at all; this prints the Python traceback of every thread.
    faulthandler.enable()


@cli.command("split")
@click.argument("input", type=str)
@click.option(
    "-l",
    "--level",
    type=int,
    default=1,
    help="How many directory levels deep to search for PDFs",
)
@click.option(
    "-f",
    "--flatten",
    is_flag=True,
    default=False,
    help="Pull every discovered PDF's output folder to the input root, "
    "instead of alongside each PDF",
)
@click.option(
    "--template",
    type=str,
    default=DEFAULT_TEMPLATE,
    help="Page image filename template. Placeholders: {{filename}}, "
    "{{page_number}}. Must contain exactly one {{page_number}}.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-split even if the output folder already matches this PDF (skipped by default)",
)
@click.option(
    "--dry-run", is_flag=True, default=False, help="Preview actions without writing any files"
)
@_log_flags
def split_cmd(
    input: str,  # noqa: A002 - mirrors Click argument name and TS API
    level: int,
    flatten: bool,
    template: str,
    force: bool,
    dry_run: bool,
    verbose: bool,
    quiet: bool,
    log_file: str | None,
) -> None:
    """Split PDF(s) into per-page JPG images alongside the source file."""
    logger = create_logger(verbose=verbose, quiet=quiet, log_file=log_file)
    started_at = time.monotonic()
    try:
        results = split_all(
            input,
            logger,
            level=level,
            flatten=flatten,
            template=template,
            force=force,
            dry_run=dry_run,
        )
    except Exception as err:
        _fail(logger, "Split failed", input, err)
        return

    failed = [r for r in results if r.failed]
    logger.info(
        f"Done. Processed {len(results)} PDF(s).",
        split=len([r for r in results if not r.failed and not r.skipped]),
        skipped=len([r for r in results if not r.failed and r.skipped]),
        failed=len(failed),
        duration_ms=round((time.monotonic() - started_at) * 1000),
    )

    # split_all already logged which PDFs failed.
    if failed:
        sys.exit(1)


@cli.command("gather")
@click.argument("input", type=str)
@click.option(
    "--dry-run", is_flag=True, default=False, help="Preview actions without writing any files"
)
@click.option(
    "--backup/--no-backup",
    default=True,
    help="Back up the existing PDF before overwriting it",
)
@_log_flags
def gather_cmd(
    input: str,  # noqa: A002 - mirrors Click argument name and TS API
    dry_run: bool,
    backup: bool,
    verbose: bool,
    quiet: bool,
    log_file: str | None,
) -> None:
    """Gather page images back into a PDF, reporting any missing pages."""
    logger = create_logger(verbose=verbose, quiet=quiet, log_file=log_file)
    started_at = time.monotonic()
    try:
        reports = gather_all(input, logger, dry_run=dry_run, check_only=False, backup=backup)
    except Exception as err:
        _fail(logger, "Gather failed", input, err)
        return

    with_missing = [r for r in reports if r.missing_pages]
    if with_missing:
        logger.warn(f"{len(with_missing)} unit(s) have missing pages")
    logger.info(
        f"Done. Processed {len(reports)} unit folder(s).",
        created=len([r for r in reports if r.action == "created"]),
        unchanged=len([r for r in reports if r.action == "skipped-unchanged"]),
        with_missing_pages=len(with_missing),
        failed=len([r for r in reports if r.action == "failed"]),
        duration_ms=round((time.monotonic() - started_at) * 1000),
    )

    failed = [r for r in reports if r.action == "failed"]
    if failed:
        logger.error(f"{len(failed)} unit(s) failed", folders=[r.folder for r in failed])
        sys.exit(1)


@cli.command("check")
@click.argument("input", type=str)
@_log_flags
def check_cmd(
    input: str,  # noqa: A002 - mirrors Click argument name and TS API
    verbose: bool,
    quiet: bool,
    log_file: str | None,
) -> None:
    """Report missing page images without writing any PDF (read-only)."""
    logger = create_logger(verbose=verbose, quiet=quiet, log_file=log_file)
    try:
        reports = gather_all(input, logger, check_only=True)
    except Exception as err:
        _fail(logger, "Check failed", input, err)
        return

    exit_code = 0
    failed = [r for r in reports if r.action == "failed"]
    if failed:
        logger.error(f"{len(failed)} unit(s) failed", folders=[r.folder for r in failed])
        exit_code = 1

    with_missing = [r for r in reports if r.missing_pages]
    if with_missing:
        logger.warn(f"{len(with_missing)} unit(s) have missing pages")
        exit_code = 1
    elif not failed:
        logger.info(f"All {len(reports)} unit(s) complete.")

    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    cli()
