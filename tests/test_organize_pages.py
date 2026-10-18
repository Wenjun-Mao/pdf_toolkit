from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
import pytest

from pdf_toolkit.pdf_ops.organize_pages import organize_pdf_pages, pdf_page_count, render_organizer_page


@pytest.fixture
def organizer_pdf(tmp_path: Path, sample_image_inputs: list[Path]) -> Path:
    source = tmp_path / "four-pages.pdf"
    with fitz.open() as document:
        for number in range(1, 5):
            page = document.new_page(width=400 + number * 10, height=600)
            page.insert_text((40, 60), f"Source page {number}")
        document[0].insert_image(fitz.Rect(40, 100, 160, 260), filename=str(sample_image_inputs[0]))
        document[2].set_rotation(90)
        document[3].add_text_annot((40, 100), "Retained note")
        document[3].insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(40, 130, 120, 150), "page": 0})
        widget = fitz.Widget()
        widget.field_name = "retained-field"
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value = "Still editable"
        widget.rect = fitz.Rect(40, 280, 160, 310)
        document[0].add_widget(widget)
        document.set_metadata({"title": "Organizer quality fixture"})
        document.save(source)
    return source


def test_organizing_reorders_and_removes_without_rasterizing(organizer_pdf: Path, tmp_path: Path) -> None:
    source_hash = hashlib.sha256(organizer_pdf.read_bytes()).hexdigest()
    output = organize_pdf_pages(organizer_pdf, [4, 1, 3], tmp_path / "organized.pdf")

    with fitz.open(organizer_pdf) as original, fitz.open(output) as result:
        assert [page.get_text().strip() for page in result] == ["Source page 4", "Source page 1\nStill editable", "Source page 3"]
        assert result.metadata["title"] == original.metadata["title"]
        for new_index, old_index in enumerate([3, 0, 2]):
            assert result[new_index].rect == original[old_index].rect
            assert result[new_index].rotation == original[old_index].rotation
            assert result[new_index].get_pixmap().samples == original[old_index].get_pixmap().samples
        old_image = original.extract_image(original[0].get_images()[0][0])
        new_image = result.extract_image(result[1].get_images()[0][0])
        assert new_image["image"] == old_image["image"]
        assert next(result[0].annots()).info["content"] == "Retained note"
        assert next(result[1].widgets()).field_value == "Still editable"
        assert result[0].get_links()[0]["page"] == 1
    assert hashlib.sha256(organizer_pdf.read_bytes()).hexdigest() == source_hash


@pytest.mark.parametrize("order", [[], [1, 1], [0, 1], [1, 5], [-1], [True], ["1"]])
def test_invalid_page_orders_never_create_output(organizer_pdf: Path, tmp_path: Path, order) -> None:
    output = tmp_path / "invalid.pdf"
    with pytest.raises(ValueError):
        organize_pdf_pages(organizer_pdf, order, output)
    assert not output.exists()


def test_cannot_overwrite_source(organizer_pdf: Path) -> None:
    with pytest.raises(ValueError, match="new file"):
        organize_pdf_pages(organizer_pdf, [1], organizer_pdf)
    assert pdf_page_count(organizer_pdf) == 4


def test_preview_renders_only_requested_page_at_bounded_resolution(organizer_pdf: Path) -> None:
    png = render_organizer_page(organizer_pdf, 3, max_edge=360)
    pixmap = fitz.Pixmap(png)
    assert max(pixmap.width, pixmap.height) <= 361
    with fitz.open(organizer_pdf) as source:
        page = source[2]
        scale = 360 / max(page.rect.width, page.rect.height)
        assert pixmap.samples == page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).samples


def test_password_protected_pdf_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "locked.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password")
    with pytest.raises(ValueError, match="password"):
        pdf_page_count(source)
