"""Thin wrapper over pypdfium2, mirroring pdfslice's pdf-rasterizer.ts.

pypdfium2 (Google's PDFium, BSD-licensed — chosen over PyMuPDF's AGPL
license for a permissively licensed CLI) rasterizes each page. Each page and
bitmap is closed right after use so memory stays bounded by roughly one page
instead of growing with page count.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image


class PdfRasterizer:
    def __init__(self, pdf_path: str | Path, scale: float) -> None:
        self._scale = scale
        self._doc = pdfium.PdfDocument(str(pdf_path))
        self.page_count = len(self._doc)

    def render_page(self, page_number: int) -> Image.Image:
        """Render one page (1-based) to an RGB image and release its native memory."""
        page = self._doc[page_number - 1]
        try:
            bitmap = page.render(scale=self._scale)
            try:
                return bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()

    def destroy(self) -> None:
        self._doc.close()


def open_pdf_for_rasterizing(pdf_path: str | Path, scale: float) -> PdfRasterizer:
    return PdfRasterizer(pdf_path, scale)
