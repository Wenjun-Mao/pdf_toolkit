from __future__ import annotations

import re
from pathlib import Path

import fitz

from pdf_toolkit.jobs import create_job, update_job_fields
from pdf_toolkit.models import JobStatus
from pdf_toolkit.pdf_ops import analyze_scan_pdf


def test_scan_cleanup_tuning_form_can_reset_global_defaults(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    job = _create_scan_analysis_job(sample_scan_pdf, tmp_path / "previews")

    response = open_app_client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    assert "Reset defaults" in response.text
    assert 'x-on:click="resetScanDefaults($el.form)"' in response.text
    assert 'data-preview-submit' in response.text
    assert 'name="strength" step="0.05" min="0" max="1" value="0.65" data-scan-default="0.65"' in response.text
    assert 'name="white_point" step="1" min="215" max="252" value="242" data-scan-default="242"' in response.text
    assert 'name="contrast" step="0.05" min="0.7" max="1.6" value="1.05" data-scan-default="1.05"' in response.text
    assert 'name="dpi_cap" step="1" min="120" max="600" value="300" data-scan-default="300"' in response.text
    assert 'name="jpeg_quality" step="1" min="70" max="100" value="92" data-scan-default="92"' in response.text
    assert "Reset all tuning" not in response.text


def test_scan_cleanup_tuning_form_can_reset_everything_when_page_overrides_exist(
    open_app_client,
    sample_image_inputs: list[Path],
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "two-page-scan.pdf"
    document = fitz.open()
    for image_path in sample_image_inputs:
        page = document.new_page(width=300, height=225)
        page.insert_image(page.rect, filename=str(image_path))
    document.save(source_pdf)
    document.close()
    job = _create_scan_analysis_job(source_pdf, tmp_path / "previews")

    response = open_app_client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    assert "Reset all tuning" in response.text
    assert "Reset global settings and clear all per-page overrides?" in response.text
    assert 'x-on:click="confirm(' in response.text
    assert 'resetAllScanTuning($el.form)' in response.text
    assert len(re.findall(r"<input[^>]+data-page-override", response.text)) == 6


def _create_scan_analysis_job(source_pdf: Path, preview_dir: Path):
    analysis = analyze_scan_pdf(source_pdf, preview_dir)
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [source_pdf],
    )
    update_job_fields(
        job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )
    return job
