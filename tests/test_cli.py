from __future__ import annotations

import sys
from pathlib import Path

from pdf_toolkit import cli


def test_mixed_to_pdf_cli_dispatches_order_and_image_options(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    output_path = tmp_path / "mixed.pdf"
    first_pdf = tmp_path / "first.pdf"
    image_path = tmp_path / "image.png"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"%PDF-1.7\n")
    image_path.write_bytes(b"image")
    second_pdf.write_bytes(b"%PDF-1.7\n")
    calls: dict[str, object] = {}

    def fake_mixed_files_to_pdf(
        source_paths: list[Path],
        output_path_arg: Path,
        *,
        fallback_dpi: int,
        jpeg_quality: int,
        page_size: str,
        margin_mm: float,
        placement: str,
    ) -> Path:
        calls["source_paths"] = source_paths
        calls["output_path"] = output_path_arg
        calls["fallback_dpi"] = fallback_dpi
        calls["jpeg_quality"] = jpeg_quality
        calls["page_size"] = page_size
        calls["margin_mm"] = margin_mm
        calls["placement"] = placement
        output_path_arg.write_bytes(b"%PDF-1.7\n%%EOF\n")
        return output_path_arg

    monkeypatch.setattr(cli, "mixed_files_to_pdf", fake_mixed_files_to_pdf)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pdfkit",
            "mixed-to-pdf",
            str(output_path),
            str(first_pdf),
            str(image_path),
            str(second_pdf),
            "--fallback-dpi",
            "240",
            "--jpeg-quality",
            "91",
            "--page-size",
            "letter",
            "--margin-mm",
            "6.5",
            "--placement",
            "fill",
        ],
    )

    cli.main()

    assert calls["source_paths"] == [first_pdf, image_path, second_pdf]
    assert calls["output_path"] == output_path
    assert calls["fallback_dpi"] == 240
    assert calls["jpeg_quality"] == 91
    assert calls["page_size"] == "letter"
    assert calls["margin_mm"] == 6.5
    assert calls["placement"] == "fill"
    assert str(output_path.resolve()) in capsys.readouterr().out


def test_scan_cleanup_cli_dispatches_output_options(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    input_path = tmp_path / "scan.pdf"
    output_path = tmp_path / "cleaned.pdf"
    preview_dir = tmp_path / "previews"
    input_path.write_bytes(b"%PDF-1.7\n")
    expected_analysis = object()
    calls: dict[str, object] = {}

    def fake_analyze_scan_pdf(input_path_arg: Path, preview_dir_arg: Path):
        calls["analyze_input"] = input_path_arg
        calls["preview_dir"] = preview_dir_arg
        return expected_analysis

    def fake_clean_scanned_pdf(
        input_path_arg: Path,
        output_path_arg: Path,
        *,
        analysis,
        default_settings,
    ) -> Path:
        calls["cleanup_input"] = input_path_arg
        calls["output_path"] = output_path_arg
        calls["analysis"] = analysis
        calls["default_settings"] = default_settings
        output_path_arg.write_bytes(b"%PDF-1.7\n%%EOF\n")
        return output_path_arg

    monkeypatch.setattr(cli, "analyze_scan_pdf", fake_analyze_scan_pdf)
    monkeypatch.setattr(cli, "clean_scanned_pdf", fake_clean_scanned_pdf)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pdfkit",
            "scan-cleanup",
            str(input_path),
            str(output_path),
            "--strength",
            "0.7",
            "--white-point",
            "244",
            "--contrast",
            "1.1",
            "--dpi-cap",
            "450",
            "--jpeg-quality",
            "92",
            "--preview-dir",
            str(preview_dir),
        ],
    )

    cli.main()

    default_settings = calls["default_settings"]
    assert calls["analyze_input"] == input_path
    assert calls["preview_dir"] == preview_dir
    assert calls["cleanup_input"] == input_path
    assert calls["output_path"] == output_path
    assert calls["analysis"] is expected_analysis
    assert default_settings.strength == 0.7
    assert default_settings.white_point == 244
    assert default_settings.contrast == 1.1
    assert default_settings.dpi_cap == 450
    assert default_settings.jpeg_quality == 92
    assert str(output_path.resolve()) in capsys.readouterr().out
