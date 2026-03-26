from __future__ import annotations

from pathlib import Path

import fitz
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def merge_pdfs(source_paths: list[Path], output_path: Path) -> Path:
    if len(source_paths) < 2:
        raise ValueError("At least two PDFs are required to merge.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged_document = fitz.open()
    try:
        for source_path in source_paths:
            with fitz.open(source_path) as source_document:
                merged_document.insert_pdf(source_document)
        merged_document.save(output_path, garbage=4, deflate=True)
    finally:
        merged_document.close()
    return output_path
