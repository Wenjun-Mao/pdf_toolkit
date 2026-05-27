from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .images_to_pdf import images_to_pdf
from .merge import merge_pdfs

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def mixed_files_to_pdf(
    source_paths: list[Path],
    output_path: Path,
    *,
    fallback_dpi: int = 300,
    jpeg_quality: int = 95,
    page_size: str = "original",
    margin_mm: float = 0.0,
    placement: str = "fit",
) -> Path:
    """Normalize PDFs and images into ordered PDF segments, then write one PDF."""

    if not source_paths:
        raise ValueError("At least one PDF or image file is required.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mixed-to-pdf-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        pdf_segments: list[Path] = []

        for index, source_path in enumerate(source_paths, start=1):
            input_kind = classify_mixed_input(source_path)
            if input_kind == "pdf":
                _validate_pdf(source_path)
                pdf_segments.append(source_path)
                continue

            segment_path = temp_dir / f"{index:03d}-image.pdf"
            images_to_pdf(
                [source_path],
                segment_path,
                fallback_dpi=fallback_dpi,
                jpeg_quality=jpeg_quality,
                page_size=page_size,
                margin_mm=margin_mm,
                placement=placement,
            )
            pdf_segments.append(segment_path)

        _write_pdf_segments(pdf_segments, output_path)

    return output_path


def classify_mixed_input(source_path: Path) -> str:
    suffix = source_path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    raise ValueError(f"Unsupported mixed PDF input: {source_path.name}. Use PDF or image files.")


def _validate_pdf(source_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"PDF file was not found: {source_path}")

    try:
        with fitz.open(source_path) as document:
            if document.page_count < 1:
                raise ValueError(f"PDF contains no pages: {source_path.name}")
    except (RuntimeError, ValueError) as exc:
        raise ValueError(f"Could not read PDF file {source_path.name}: {exc}") from exc


def _write_pdf_segments(pdf_segments: list[Path], output_path: Path) -> None:
    if len(pdf_segments) == 1:
        _resave_pdf_segment(pdf_segments[0], output_path)
        return

    merge_pdfs(pdf_segments, output_path)


def _resave_pdf_segment(source_path: Path, output_path: Path) -> None:
    with fitz.open(source_path) as source_document:
        output_document = fitz.open()
        try:
            output_document.insert_pdf(source_document)
            output_document.save(output_path, garbage=4, deflate=True)
        finally:
            output_document.close()
