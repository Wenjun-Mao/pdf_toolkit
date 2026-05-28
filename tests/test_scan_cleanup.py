from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PIL import Image

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


def test_scan_cleanup_rounds_effective_dpi_before_rendering(tmp_path: Path) -> None:
    image_path = tmp_path / "page.jpg"
    with Image.new("RGB", (827, 1169), color=(245, 245, 245)) as image:
        image.save(image_path, format="JPEG", quality=95)

    source_pdf = tmp_path / "source.pdf"
    document = fitz.open()
    page = document.new_page(width=595.44, height=841.68)
    page.insert_image(page.rect, filename=str(image_path))
    document.save(source_pdf)
    document.close()

    analysis = analyze_scan_pdf(source_pdf, tmp_path / "previews")
    assert round(analysis.pages[0].effective_dpi or 0) == 100

    cleaned_pdf = tmp_path / "cleaned.pdf"
    clean_scanned_pdf(
        source_pdf,
        cleaned_pdf,
        analysis=analysis,
        default_settings=CleanupSettings(dpi_cap=200),
    )

    with fitz.open(cleaned_pdf) as cleaned_document:
        cleaned_page = cleaned_document[0]
        xref = cleaned_page.get_images(full=True)[0][0]
        image_info = cleaned_document.extract_image(xref)

    assert image_info["width"] == 827
    assert image_info["height"] == 1169


def test_scan_cleanup_settings_normalize_output_quality_controls() -> None:
    low_settings = CleanupSettings(dpi_cap=80, jpeg_quality=40).normalized()
    high_settings = CleanupSettings(dpi_cap=900, jpeg_quality=120).normalized()

    assert low_settings.dpi_cap == 120
    assert low_settings.jpeg_quality == 70
    assert high_settings.dpi_cap == 600
    assert high_settings.jpeg_quality == 100


def test_scan_analysis_default_preview_is_readable_width(tmp_path: Path, sample_scan_pdf: Path) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    preview_path = Path(analysis.pages[0].preview_path)

    with Image.open(preview_path) as preview_image:
        assert preview_image.width >= 720


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
