from __future__ import annotations

import re
from pathlib import Path

import pdf_toolkit.web.app as web_app
from pdf_toolkit.models import Job, JobStatus
from pdf_toolkit.pdf_ops import CleanupSettings, analyze_scan_pdf
from pdf_toolkit.jobs import create_job, get_job, update_job_fields
from pdf_toolkit.settings import Settings
from pdf_toolkit.web.rendering import serialize_job
from pdf_toolkit.web.scan_cleanup_preview import _processed_preview_filename


def test_serialize_job_marks_scan_awaiting_settings_separately() -> None:
    scan_payload = serialize_job(
        Job(
            id="scan-job",
            tool_name="scan-cleanup-analysis",
            display_name="Analyze Scan Cleanup",
            status=JobStatus.AWAITING_SETTINGS.value,
        )
    )
    mixed_payload = serialize_job(
        Job(
            id="mixed-job",
            tool_name="mixed-to-pdf",
            display_name="Mixed to PDF",
            status=JobStatus.AWAITING_SETTINGS.value,
        )
    )

    assert scan_payload["awaiting_settings"] is True
    assert scan_payload["awaiting_scan_settings"] is True
    assert mixed_payload["awaiting_settings"] is True
    assert mixed_payload["awaiting_scan_settings"] is False


def test_serialize_job_uses_settings_backed_scan_defaults() -> None:
    settings = Settings(
        scan_default_strength=0.5,
        scan_default_white_point=248,
        scan_default_contrast=1.25,
        scan_default_dpi_cap=360,
        scan_default_jpeg_quality=88,
    )

    payload = serialize_job(
        Job(
            id="scan-job",
            tool_name="scan-cleanup-analysis",
            display_name="Analyze Scan Cleanup",
            status=JobStatus.AWAITING_SETTINGS.value,
        ),
        settings,
    )

    assert payload["scan_defaults"] == {
        "strength": 0.5,
        "white_point": 248,
        "contrast": 1.25,
        "dpi_cap": 360,
        "jpeg_quality": 88,
    }


def test_login_required_redirects_to_login(app_client) -> None:
    unauthenticated = app_client.__class__(app_client.app)
    response = unauthenticated.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_optional_allows_home_access(open_app_client) -> None:
    response = open_app_client.get("/")
    assert response.status_code == 200
    assert "PDF Kit" in response.text


def test_login_optional_redirects_login_page_to_home(open_app_client) -> None:
    response = open_app_client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_merge_submission_completes_without_login(open_app_client, sample_merge_pdfs: list[Path]) -> None:
    files = [
        ("files", (sample_merge_pdfs[0].name, sample_merge_pdfs[0].read_bytes(), "application/pdf")),
        ("files", (sample_merge_pdfs[1].name, sample_merge_pdfs[1].read_bytes(), "application/pdf")),
    ]
    response = open_app_client.post("/tools/merge/submit", files=files)

    assert response.status_code == 200
    assert "Download merged.pdf" in response.text


def test_merge_submission_completes_inline(app_client, sample_merge_pdfs: list[Path]) -> None:
    files = [
        ("files", (sample_merge_pdfs[0].name, sample_merge_pdfs[0].read_bytes(), "application/pdf")),
        ("files", (sample_merge_pdfs[1].name, sample_merge_pdfs[1].read_bytes(), "application/pdf")),
    ]
    response = app_client.post("/tools/merge/submit", files=files)

    assert response.status_code == 200
    assert "Download merged.pdf" in response.text


def test_images_to_pdf_submission_completes_inline(app_client, sample_image_inputs: list[Path]) -> None:
    files = [
        (
            "files",
            (
                image_path.name,
                image_path.read_bytes(),
                "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
            ),
        )
        for image_path in sample_image_inputs
    ]
    response = app_client.post(
        "/tools/images-to-pdf/submit",
        data={
            "fallback_dpi": "300",
            "jpeg_quality": "95",
            "page_size": "letter",
            "margin_mm": "12.7",
            "placement": "fit",
        },
        files=files,
    )

    assert response.status_code == 200
    assert "Download images.pdf" in response.text


def test_id_halves_to_pdf_submission_completes_inline(app_client, sample_image_inputs: list[Path]) -> None:
    top_image = sample_image_inputs[0]
    bottom_image = sample_image_inputs[1]
    files = [
        (
            "top_image",
            (
                top_image.name,
                top_image.read_bytes(),
                "image/jpeg" if top_image.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
            ),
        ),
        (
            "bottom_image",
            (
                bottom_image.name,
                bottom_image.read_bytes(),
                "image/jpeg" if bottom_image.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
            ),
        ),
    ]
    response = app_client.post(
        "/tools/id-halves-to-pdf/submit",
        data={
            "fallback_dpi": "300",
            "jpeg_quality": "95",
        },
        files=files,
    )

    assert response.status_code == 200
    assert "Download id-halves.pdf" in response.text


def test_scan_cleanup_review_form_exposes_output_controls(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    assert 'name="dpi_cap"' in response.text
    assert 'value="300"' in response.text
    assert 'name="jpeg_quality"' in response.text
    assert 'value="92"' in response.text
    assert 'name="preview_page"' in response.text
    assert "Update preview" in response.text
    assert f'hx-post="/tools/scan-cleanup/{job.id}/preview"' in response.text
    assert f'id="job-launch-panel-{job.id}"' in response.text
    assert f'id="scan-preview-result-{job.id}"' in response.text
    assert f'hx-target="#job-launch-panel-{job.id}"' in response.text
    assert f'hx-target="#scan-preview-result-{job.id}"' in response.text
    assert f'hx-include="#job-launch-panel-{job.id} form"' in response.text
    assert "What do these settings do?" in response.text
    assert 'class="settings-help-grid"' in response.text
    assert response.text.count('class="settings-help-item"') == 5
    assert "Strength controls how aggressively background tint and noise are neutralized." in response.text
    assert "White Point controls which light pixels become pure white." in response.text
    assert "Contrast boosts local foreground separation." in response.text
    assert "Max DPI caps the render resolution for each page." in response.text
    assert "JPEG Quality controls output compression." in response.text
    assert "Optional Page Overrides" not in response.text
    assert 'name="page_1_strength"' not in response.text


def test_scan_cleanup_partial_embeds_review_when_analysis_is_ready(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.get(
        f"/jobs/{job.id}?partial=1",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert f'id="job-{job.id}"' in response.text
    assert "Review analysis and launch cleanup" in response.text
    assert "Run Cleanup" in response.text
    assert "Preview for page 1" in response.text
    assert "Open Analysis" not in response.text


def test_scan_cleanup_process_submission_stores_output_controls(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )
    monkeypatch.setattr(web_app, "enqueue_scan_process", lambda job_id, settings: None)

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/process",
        data={
            "strength": "0.7",
            "white_point": "244",
            "contrast": "1.1",
            "dpi_cap": "450",
            "jpeg_quality": "92",
        },
    )

    match = re.search(r'id="job-([^"]+)"', response.text)
    assert response.status_code == 200
    assert match is not None
    process_job = get_job(match.group(1))
    assert process_job is not None
    assert process_job.params_json["defaults"]["dpi_cap"] == 450
    assert process_job.params_json["defaults"]["jpeg_quality"] == 92


def test_scan_cleanup_process_submission_stores_page_overrides(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )
    monkeypatch.setattr(web_app, "enqueue_scan_process", lambda job_id, settings: None)

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/process",
        data={
            "strength": "0.65",
            "white_point": "242",
            "contrast": "1.05",
            "dpi_cap": "300",
            "jpeg_quality": "92",
            "page_1_strength": "0.8",
            "page_1_white_point": "246",
            "page_1_contrast": "1.2",
        },
    )

    match = re.search(r'id="job-([^"]+)"', response.text)
    assert response.status_code == 200
    assert match is not None
    process_job = get_job(match.group(1))
    assert process_job is not None
    assert process_job.params_json["page_overrides"] == {
        "1": {"strength": 0.8, "white_point": 246, "contrast": 1.2}
    }


def test_scan_cleanup_process_submission_ignores_page_metadata_fields(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )
    monkeypatch.setattr(web_app, "enqueue_scan_process", lambda job_id, settings: None)

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/process",
        data={
            "strength": "0.65",
            "white_point": "242",
            "contrast": "1.05",
            "dpi_cap": "300",
            "jpeg_quality": "92",
            "page_count": "2",
            "page_1_label": "Cover",
            "page_1_strength": "0.8",
            "page_1_contrast": "",
        },
    )

    match = re.search(r'id="job-([^"]+)"', response.text)
    assert response.status_code == 200
    assert match is not None
    process_job = get_job(match.group(1))
    assert process_job is not None
    assert process_job.params_json["page_overrides"] == {
        "1": {"strength": 0.8}
    }


def test_scan_cleanup_preview_submission_returns_before_after_panel(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/preview",
        data={
            "preview_page": "1",
            "strength": "0.7",
            "white_point": "244",
            "contrast": "1.1",
            "dpi_cap": "300",
            "jpeg_quality": "92",
        },
    )

    assert response.status_code == 200
    assert "Original" in response.text
    assert "Processed" in response.text
    assert "Page 1" in response.text
    assert "/previews/" in response.text
    assert "processed-page-001-" in response.text
    processed_filename = re.search(r"processed-page-001-[a-f0-9]+\.jpg", response.text)
    assert processed_filename is not None
    assert (tmp_path / "data" / "previews" / analysis_job.id / processed_filename.group(0)).exists()


def test_scan_cleanup_preview_rejects_invalid_page(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/preview",
        data={
            "preview_page": "99",
            "strength": "0.7",
            "white_point": "244",
            "contrast": "1.1",
            "dpi_cap": "300",
            "jpeg_quality": "92",
        },
    )

    assert response.status_code == 400
    assert "Preview page must be between 1 and 1." in response.text


def test_scan_cleanup_preview_rejects_malformed_page(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/preview",
        data={
            "preview_page": "abc",
            "strength": "0.7",
            "white_point": "244",
            "contrast": "1.1",
            "dpi_cap": "300",
            "jpeg_quality": "92",
        },
    )

    assert response.status_code == 400
    assert "Preview page must be a whole number." in response.text


def test_scan_cleanup_preview_rejects_invalid_numeric_settings(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/preview",
        data={
            "preview_page": "1",
            "strength": "loud",
            "white_point": "244",
            "contrast": "1.1",
            "dpi_cap": "300",
            "jpeg_quality": "92",
        },
    )

    assert response.status_code == 400
    assert "Preview settings must be numeric." in response.text


def test_processed_preview_filename_includes_preview_width() -> None:
    settings = CleanupSettings(strength=0.7, white_point=244, contrast=1.1, dpi_cap=300, jpeg_quality=92)

    narrow_filename = _processed_preview_filename(1, settings, 600)
    wide_filename = _processed_preview_filename(1, settings, 900)

    assert narrow_filename.startswith("processed-page-001-")
    assert wide_filename.startswith("processed-page-001-")
    assert narrow_filename != wide_filename
