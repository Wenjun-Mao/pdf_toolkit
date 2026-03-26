from __future__ import annotations

from pathlib import Path

import fitz

from pdf_toolkit.pdf_ops import extract_embedded_images, extract_pages, images_to_pdf, merge_pdfs, split_pdf


def test_merge_extract_and_split_round_trip(tmp_path: Path, sample_merge_pdfs: list[Path]) -> None:
    merged_path = tmp_path / "merged.pdf"
    merge_pdfs(sample_merge_pdfs, merged_path)

    with fitz.open(sample_merge_pdfs[0]) as first_doc, fitz.open(sample_merge_pdfs[1]) as second_doc, fitz.open(merged_path) as merged_doc:
        assert len(merged_doc) == len(first_doc) + len(second_doc)

    extracted_path = tmp_path / "extracted.pdf"
    extract_pages(merged_path, "1-2", extracted_path)
    with fitz.open(extracted_path) as extracted_doc:
        assert len(extracted_doc) == 2

    split_output_dir = tmp_path / "split"
    split_parts = split_pdf(merged_path, split_output_dir, every_n=2)
    assert len(split_parts) >= 2
    assert all(path.exists() for path in split_parts)


def test_extract_embedded_images_returns_manifest(tmp_path: Path, sample_scan_pdf: Path) -> None:
    output_dir = tmp_path / "images"
    manifest = extract_embedded_images(sample_scan_pdf, output_dir)

    assert manifest
    assert any(entry["images"] for entry in manifest)
    exported_files = list(output_dir.glob("*"))
    assert exported_files


def test_images_to_pdf_builds_expected_pages(tmp_path: Path, sample_image_inputs: list[Path]) -> None:
    output_path = tmp_path / "images.pdf"
    images_to_pdf(sample_image_inputs, output_path)

    with fitz.open(output_path) as output_doc:
        assert output_doc.page_count == 2
        first_page = output_doc[0]
        assert round(first_page.rect.width, 2) == 144.0
        assert round(first_page.rect.height, 2) == 216.0

        second_page = output_doc[1]
        assert round(second_page.rect.width, 2) == 96.0
        assert round(second_page.rect.height, 2) == 72.0

        rendered = second_page.get_pixmap(alpha=False)
        top_left = rendered.pixel(0, 0)
        assert top_left == (255, 255, 255)


def test_images_to_pdf_letter_fit_respects_margins_and_orientation(tmp_path: Path, sample_image_inputs: list[Path]) -> None:
    output_path = tmp_path / "images-letter-fit.pdf"
    images_to_pdf(
        sample_image_inputs,
        output_path,
        page_size="letter",
        margin_mm=12.7,
        placement="fit",
    )

    with fitz.open(output_path) as output_doc:
        first_page = output_doc[0]
        assert round(first_page.rect.width, 2) == 612.0
        assert round(first_page.rect.height, 2) == 792.0
        first_xref = first_page.get_images(full=True)[0][0]
        first_rect = first_page.get_image_rects(first_xref)[0]
        assert round(first_rect.x0, 2) == 66.0
        assert round(first_rect.y0, 2) == 36.0
        assert round(first_rect.width, 2) == 480.0
        assert round(first_rect.height, 2) == 720.0

        second_page = output_doc[1]
        assert round(second_page.rect.width, 2) == 792.0
        assert round(second_page.rect.height, 2) == 612.0
        second_xref = second_page.get_images(full=True)[0][0]
        second_rect = second_page.get_image_rects(second_xref)[0]
        assert round(second_rect.x0, 2) == 36.0
        assert round(second_rect.y0, 2) == 36.0
        assert round(second_rect.width, 2) == 720.0
        assert round(second_rect.height, 2) == 540.0


def test_images_to_pdf_letter_fill_crops_without_distortion(tmp_path: Path, sample_image_inputs: list[Path]) -> None:
    output_path = tmp_path / "images-letter-fill.pdf"
    images_to_pdf(
        sample_image_inputs[:1],
        output_path,
        page_size="letter",
        margin_mm=12.7,
        placement="fill",
    )

    with fitz.open(output_path) as output_doc:
        page = output_doc[0]
        image_xref = page.get_images(full=True)[0][0]
        image_rect = page.get_image_rects(image_xref)[0]
        assert round(page.rect.width, 2) == 612.0
        assert round(page.rect.height, 2) == 792.0
        assert round(image_rect.x0, 2) == 36.0
        assert round(image_rect.width, 2) == 540.0
        assert round(image_rect.height, 2) == 810.0

        rendered = page.get_pixmap(alpha=False)
        assert rendered.pixel(10, 10) == (255, 255, 255)
        assert rendered.pixel(rendered.width // 2, rendered.height // 2) != (255, 255, 255)
