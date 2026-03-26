from __future__ import annotations

from pathlib import Path

import fitz
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .ranges import build_every_n_groups, parse_page_range_spec, parse_split_range_groups


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def extract_pages(source_path: Path, page_spec: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(source_path) as source_document:
        pages = parse_page_range_spec(page_spec, len(source_document))
        extracted_document = fitz.open()
        try:
            for page_number in pages:
                extracted_document.insert_pdf(
                    source_document,
                    from_page=page_number - 1,
                    to_page=page_number - 1,
                )
            extracted_document.save(output_path, garbage=4, deflate=True)
        finally:
            extracted_document.close()
    return output_path


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def split_pdf(
    source_path: Path,
    output_dir: Path,
    *,
    range_spec: str | None = None,
    every_n: int | None = None,
) -> list[Path]:
    if not range_spec and not every_n:
        raise ValueError("Provide either explicit split ranges or an every-N split size.")

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_paths: list[Path] = []
    with fitz.open(source_path) as source_document:
        if range_spec:
            groups = parse_split_range_groups(range_spec, len(source_document))
        else:
            groups = build_every_n_groups(len(source_document), every_n or 0)

        for part_index, page_numbers in enumerate(groups, start=1):
            target_path = output_dir / f"part-{part_index:02d}.pdf"
            part_document = fitz.open()
            try:
                for page_number in page_numbers:
                    part_document.insert_pdf(
                        source_document,
                        from_page=page_number - 1,
                        to_page=page_number - 1,
                    )
                part_document.save(target_path, garbage=4, deflate=True)
            finally:
                part_document.close()
            generated_paths.append(target_path)

    return generated_paths
