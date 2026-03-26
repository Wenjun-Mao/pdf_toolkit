from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from pdf_toolkit.pdf_ops import (
    CleanupSettings,
    analyze_scan_pdf,
    clean_scanned_pdf,
    compare_render_metrics,
    extract_pages,
    generate_before_after_contact_sheet,
    run_tesseract_similarity,
)
from pdf_toolkit.pdf_ops.scan_cleanup import GOLDEN_SCAN_PAGES


@pytest.mark.slow
def test_scan_cleanup_quality_subset(tmp_path: Path, sample_scan_pdf: Path) -> None:
    subset_pdf = tmp_path / "golden-pages.pdf"
    page_spec = ",".join(str(page_number) for page_number in GOLDEN_SCAN_PAGES[:12])
    extract_pages(sample_scan_pdf, page_spec, subset_pdf)

    analysis = analyze_scan_pdf(subset_pdf, tmp_path / "previews")
    cleaned_pdf = tmp_path / "cleaned.pdf"
    clean_scanned_pdf(
        subset_pdf,
        cleaned_pdf,
        analysis=analysis,
        default_settings=CleanupSettings(),
    )

    with fitz.open(subset_pdf) as source_doc, fitz.open(cleaned_pdf) as cleaned_doc:
        assert len(source_doc) == len(cleaned_doc)
        assert bool(source_doc[0].get_text("text").strip()) <= bool(cleaned_doc[0].get_text("text").strip())

    metrics = compare_render_metrics(
        subset_pdf,
        cleaned_pdf,
        page_numbers=list(range(1, min(13, analysis.page_count + 1))),
    )
    assert metrics["improved_background_pages"] >= max(6, analysis.page_count // 2)
    assert metrics["no_darkening_regressions"] is True
    assert metrics["dark_stroke_retention"] >= 0.70

    contact_sheet = generate_before_after_contact_sheet(
        subset_pdf,
        cleaned_pdf,
        list(range(1, min(7, analysis.page_count + 1))),
        tmp_path / "contact-sheet.jpg",
    )
    assert contact_sheet.exists()

    tesseract_similarity = run_tesseract_similarity(subset_pdf, cleaned_pdf, 1)
    if tesseract_similarity is not None:
        assert tesseract_similarity >= 0.75


@pytest.mark.slow
def test_scan_cleanup_full_document_size_gate(tmp_path: Path, sample_scan_pdf: Path, repo_root: Path) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "full-previews")
    cleaned_pdf = tmp_path / "math-cleaned.pdf"
    clean_scanned_pdf(
        sample_scan_pdf,
        cleaned_pdf,
        analysis=analysis,
        default_settings=CleanupSettings(),
    )

    compressed_baseline = repo_root / "Books" / "sec4" / "Math3000Exam_cleaned_compressed.pdf"
    bloated_baseline = repo_root / "Books" / "sec4" / "output_cleaned.pdf"

    assert cleaned_pdf.stat().st_size < 15 * 1024 * 1024
    assert cleaned_pdf.stat().st_size < compressed_baseline.stat().st_size
    assert cleaned_pdf.stat().st_size < bloated_baseline.stat().st_size
