from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import fitz


@contextmanager
def _open_pdf(source_path: Path) -> Iterator[fitz.Document]:
    try:
        document = fitz.open(source_path)
    except (RuntimeError, OSError) as exc:
        raise ValueError("This file could not be opened as a PDF.") from exc
    try:
        if not document.is_pdf:
            raise ValueError("Select a PDF file.")
        if document.needs_pass:
            raise ValueError("This PDF needs a password. Open an unlocked copy instead.")
        if document.page_count < 1:
            raise ValueError("The PDF must contain at least one page.")
        yield document
    finally:
        document.close()


def pdf_page_count(source_path: Path) -> int:
    with _open_pdf(source_path) as document:
        return document.page_count


def validate_page_order(page_numbers: list[int], page_count: int) -> None:
    if not page_numbers:
        raise ValueError("Keep at least one page before saving.")
    if any(type(number) is not int or not 1 <= number <= page_count for number in page_numbers):
        raise ValueError("The page order contains a page outside this PDF.")
    if len(set(page_numbers)) != len(page_numbers):
        raise ValueError("Each source page can appear only once.")


def render_organizer_page(source_path: Path, page_number: int, *, max_edge: int) -> bytes:
    with _open_pdf(source_path) as document:
        validate_page_order([page_number], document.page_count)
        page = document[page_number - 1]
        scale = max_edge / max(page.rect.width, page.rect.height)
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def organize_pdf_pages(source_path: Path, page_numbers: list[int], output_path: Path) -> Path:
    if source_path.resolve() == output_path.resolve():
        raise ValueError("Save the organized PDF to a new file.")
    with _open_pdf(source_path) as document:
        validate_page_order(page_numbers, document.page_count)
        # Select existing PDF pages; preview rasters never become output content.
        document.select([number - 1 for number in page_numbers])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(output_path, garbage=4, deflate=True)
    return output_path
