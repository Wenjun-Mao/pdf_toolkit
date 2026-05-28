from __future__ import annotations

from pathlib import Path

import fitz

from pdf_toolkit.models import JobStatus
from pdf_toolkit.pdf_ops import CleanupSettings, analyze_scan_pdf
from pdf_toolkit import jobs
from pdf_toolkit.jobs import create_job, enqueue_mixed_to_pdf, get_job


def test_mixed_to_pdf_job_completes_inline(
    app_client,
    sample_merge_pdfs: list[Path],
    sample_image_inputs: list[Path],
) -> None:
    job = create_job(
        "mixed-to-pdf",
        "Mixed to PDF",
        [sample_merge_pdfs[1], sample_image_inputs[0], sample_merge_pdfs[0]],
        params_json={
            "fallback_dpi": 300,
            "jpeg_quality": 95,
            "page_size": "original",
            "margin_mm": 0.0,
            "placement": "fit",
        },
    )

    enqueue_mixed_to_pdf(job.id)

    completed_job = get_job(job.id)
    assert completed_job is not None
    assert completed_job.status == "completed"
    assert completed_job.output_path is not None
    assert Path(completed_job.output_path).name == "mixed.pdf"

    with fitz.open(completed_job.output_path) as output_doc:
        assert output_doc.page_count == 4
        assert "Second PDF / Page 1" in output_doc[0].get_text()
        assert output_doc[1].get_images(full=True)
        assert "First PDF / Page 1" in output_doc[2].get_text()


def test_scan_process_job_uses_output_quality_settings(
    app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    job = create_job(
        "scan-cleanup-process",
        "Process Scan Cleanup",
        [sample_scan_pdf],
        params_json={
            "analysis": analysis.to_json(),
            "defaults": {
                "strength": 0.7,
                "white_point": 244,
                "contrast": 1.1,
                "dpi_cap": 450,
                "jpeg_quality": 92,
            },
            "page_overrides": {},
        },
    )
    calls: dict[str, object] = {}

    def fake_clean_scanned_pdf(
        source_path: Path,
        output_path: Path,
        *,
        analysis,
        default_settings: CleanupSettings,
        page_overrides,
    ) -> Path:
        calls["source_path"] = source_path
        calls["output_path"] = output_path
        calls["analysis"] = analysis
        calls["default_settings"] = default_settings
        calls["page_overrides"] = page_overrides
        output_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
        return output_path

    monkeypatch.setattr(jobs, "clean_scanned_pdf", fake_clean_scanned_pdf)

    jobs.run_scan_process_job(job.id)

    completed_job = get_job(job.id)
    default_settings = calls["default_settings"]
    assert completed_job is not None
    assert completed_job.status == JobStatus.COMPLETED.value
    assert calls["source_path"] == sample_scan_pdf
    assert default_settings.dpi_cap == 450
    assert default_settings.jpeg_quality == 92
